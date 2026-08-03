"""End-to-end pipeline: natural language -> runnable OpenFOAM v13 case.

Flow (docs/phase-ii-architecture.md §2):
  NL prompt --LLM--> CaseIntent (compact) --expand--> CaseSpec --render--> files
  --validate (Docker v13 ladder)--> verdict

Extraction follows "reason free, constrain late": the model emits a small JSON
intent; we repair (strip fences / isolate the JSON span), validate with Pydantic,
and on failure re-prompt with the specific error — a validate-and-retry loop
rather than hard grammar-constrained decoding (the Constraint Tax finding).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError as PydanticValidationError

from .case import CaseFiles
from .intent import CaseIntent, intent_to_spec
from .llm_client import LLMConfig, make_client
from .mesh_io import MeshPatch, copy_polymesh, load_external_mesh
from .renderer import render
from .validation_agent import ValidationAgent, ValidationResult

_SCHEMA = """{
  "regime": "laminar" | "turbulent",
  "nu": number,                     // kinematic viscosity, m^2/s
  "geometry": "box" | "external_mesh",
  "box_size": [Lx, Ly, Lz],         // meters; required when geometry is "box"
  "cells": [Nx, Ny, Nz],            // optional, default [20, 20, 1]
  "patches": [
    { "name": string,
      "role": "inlet" | "outlet" | "wall" | "movingWall" | "empty",
      "velocity": [x, y, z],        // only for inlet / movingWall
      "pressure": number }          // only for outlet (gauge pressure)
  ]
}"""

_EXAMPLE = """Example — "Lid-driven cavity: 0.1m square box, top wall slides at 1 m/s, other
walls fixed, 2D, turbulent, air nu=1e-5" becomes:
{"regime":"turbulent","nu":1e-05,"geometry":"box","box_size":[0.1,0.1,0.1],
"cells":[20,20,1],"patches":[
{"name":"movingWall","role":"movingWall","velocity":[1,0,0]},
{"name":"fixedWalls","role":"wall"},
{"name":"frontAndBack","role":"empty"}]}"""

_SYSTEM = (
    "You convert a fluid-simulation description into a compact JSON intent for "
    "OpenFOAM. Output ONLY the JSON object — no prose, no markdown fences. "
    "Use exactly this schema:\n" + _SCHEMA + "\n\n" + _EXAMPLE
)

# LM-Studio-friendly JSON schema (uses `items`, enums; no tuples/$refs) for
# grammar-constrained structured output. Guarantees well-formed brackets, which
# small models otherwise get wrong (e.g. `[1,0,0}`).
_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "case_intent",
        "strict": False,
        "schema": {
            "type": "object",
            "properties": {
                "regime": {"type": "string", "enum": ["laminar", "turbulent"]},
                "nu": {"type": "number"},
                "geometry": {"type": "string", "enum": ["box", "external_mesh"]},
                "box_size": {"type": "array", "items": {"type": "number"}},
                "cells": {"type": "array", "items": {"type": "integer"}},
                "patches": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "role": {"type": "string",
                                     "enum": ["inlet", "outlet", "wall", "movingWall", "empty"]},
                            "velocity": {"type": "array", "items": {"type": "number"}},
                            "pressure": {"type": "number"},
                        },
                        "required": ["name", "role"],
                    },
                },
            },
            "required": ["regime", "nu", "geometry", "box_size", "patches"],
        },
    },
}


def _extract_json(text: str) -> str:
    """Strip markdown fences/comments and isolate the outermost {...} span."""
    text = re.sub(r"```(?:json)?", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1:
        text = text[start:end + 1]
    text = re.sub(r"//[^\n]*", "", text)  # tolerate stray // comments
    text = re.sub(r",(\s*[}\]])", r"\1", text)  # tolerate trailing commas
    return text.strip()


def _known_patches_hint(known_patches: list[MeshPatch]) -> str:
    """Format the mesh's real patch names/types so the model must reuse them
    verbatim (geometry="external_mesh") instead of inventing new patch names.
    """
    lines = "\n".join(f"- {p.name!r} (type {p.type}, {p.n_faces} faces)" for p in known_patches)
    header = (
        "\n\nThis case uses a mesh that is already provided "
        '(geometry must be "external_mesh"). '
        "It has exactly these patches - use these names verbatim, "
        "do not invent, rename, merge, or drop any of them:\n"
    )
    return header + lines


def _chat_create(client, config: LLMConfig, messages: list[dict]):
    """Call chat.completions.create, tolerating backend quirks:
    - some backends (e.g. reasoning models like gpt-5-mini on the Siemens SDC
      LLM Gateway) reject temperature=0, only accepting the default (1);
    - some backends don't support `response_format=json_schema` at all.
    Tries the strictest combination first, degrading until one is accepted.
    """
    attempts = [
        {"temperature": 0, "response_format": _RESPONSE_FORMAT},
        {"response_format": _RESPONSE_FORMAT},
        {"temperature": 0},
        {},
    ]
    last_exc: Exception | None = None
    for kwargs in attempts:
        try:
            return client.chat.completions.create(model=config.model, messages=messages, **kwargs)
        except Exception as e:  # noqa: BLE001 - deliberately broad; trying next combo
            last_exc = e
    raise last_exc  # all combinations failed


def extract_intent(nl: str, config: LLMConfig | None = None, max_retries: int = 3,
                    known_patches: list[MeshPatch] | None = None) -> CaseIntent:
    """NL -> validated CaseIntent, with a repair/validate/retry loop.

    When `known_patches` is given (patches read from a provided mesh's boundary
    file - see mesh_io.py), the prompt is grounded with the real patch names so
    the model assigns physics roles/values to them rather than inventing a box.
    """
    config = config or LLMConfig()
    client = make_client(config)
    user_content = nl + (_known_patches_hint(known_patches) if known_patches else "")
    messages = [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": user_content}]

    last_err = ""
    for _ in range(max_retries):
        resp = _chat_create(client, config, messages)
        raw = resp.choices[0].message.content or ""
        try:
            return CaseIntent(**json.loads(_extract_json(raw)))
        except (json.JSONDecodeError, PydanticValidationError, TypeError) as e:
            last_err = str(e)
            messages += [
                {"role": "assistant", "content": raw},
                {"role": "user",
                 "content": f"That was not valid. Error: {last_err}. "
                            "Return only the corrected JSON object."},
            ]
    raise ValueError(f"Could not extract a valid CaseIntent after {max_retries} tries: {last_err}")


@dataclass
class PipelineResult:
    intent: CaseIntent
    case: CaseFiles
    validation: ValidationResult


def generate(nl: str, case_dir: Path, config: LLMConfig | None = None,
             validate: bool = True, mesh_dir: Path | None = None) -> PipelineResult:
    """Full pipeline: NL -> intent -> spec -> rendered files (-> optional run).

    `mesh_dir`, when given, points at an externally-provided mesh (a polyMesh
    directory, or a folder containing `boundary` directly - see mesh_io.py).
    No mesh is generated in this repo (R1, docs/phase-ii-architecture.md §2.1):
    the mesh's boundary file is read for patch names/types, the LLM is grounded
    on those exact names, and the raw polyMesh files are copied unparsed into
    the case. Validation then skips blockMesh, since the mesh already exists.
    """
    known_patches = load_external_mesh(mesh_dir) if mesh_dir else None
    intent = extract_intent(nl, config, known_patches=known_patches)
    spec = intent_to_spec(intent)
    case = render(spec)
    case.write(case_dir)
    if mesh_dir:
        copy_polymesh(mesh_dir, case_dir)
    if not validate:
        result = ValidationResult(True, "skipped")
    else:
        result = ValidationAgent().run_ladder(case_dir, skip_mesh_generation=bool(mesh_dir))
    return PipelineResult(intent, case, result)
