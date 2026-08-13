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

from ir.intent import CaseIntent, intent_to_spec
from knowledge.rag import KnowledgeRetriever
from knowledge.skills import SkillRegistry
from llm.llm_client import LLMConfig, make_client
from mesh.mesh_io import MeshPatch, copy_polymesh, load_external_mesh
from rendering.case import CaseFiles
from rendering.renderer import render
from validation.validation_agent import ValidationAgent, ValidationResult

_SCHEMA = """{
  "regime": "laminar" | "turbulent",
  "flow_type": "incompressible" | "compressible",  // optional, default "incompressible"
  "solver_mode": "transient" | "steadyState",       // optional, default "transient"
  "fluid_species": string,                           // e.g. "air", "nitrogen", "co2", "oxygen", "helium", "water_vapour"
  "nu": number,                                      // kinematic viscosity, m^2/s (incompressible)
  "p_ambient": number,                               // ambient/reference pressure, Pa (compressible)
  "temperature": number,                             // reference temperature, K (compressible)
  "geometry": "box" | "external_mesh",
  "box_size": [Lx, Ly, Lz],                          // meters; required when geometry is "box"
  "cells": [Nx, Ny, Nz],                             // optional, default [20, 20, 1]
  "turbulence_model": string,                        // "kEpsilon"|"kOmegaSST"|"SpalartAllmaras"|"realizableKE"|"RNGkEpsilon"; omit for default kEpsilon
  "custom_fv_schemes": string,                       // full fvSchemes dict body (between outer braces); omit to use standard preset
  "custom_fv_solution": string,                      // full fvSolution dict body including relaxationFactors; omit to use standard preset
  "patches": [
    { "name": string,
      "role": "inlet" | "outlet" | "wall" | "movingWall" | "empty" | "cyclic" | "symmetry",
      "velocity": [x, y, z],                         // only for inlet / movingWall
      "pressure": number,                            // gauge or absolute Pa
      "temperature": number,                         // inlet / wall temperature [K]
      "faces": ["xMin" | "xMax" | "yMin" | "yMax" | "zMin" | "zMax"],  // optional, box geometry only;
                                                                       // if one patch lists faces, all must
      "bc_overrides": {                              // optional: per-field BC type overrides
        "<field>": {"type": "<bcType>", "<key>": "<value>", ...}
      }
    }
  ]
}"""

_EXAMPLE = """Example — "Lid-driven cavity: 0.1m square box, top wall slides at 1 m/s, other
 walls fixed, 2D, turbulent, air nu=1e-5" becomes:
{"regime":"turbulent","nu":1e-05,"geometry":"box","box_size":[0.1,0.1,0.1],
"cells":[20,20,1],"patches":[
{"name":"movingWall","role":"movingWall","velocity":[1,0,0]},
{"name":"fixedWalls","role":"wall"},
{"name":"frontAndBack","role":"empty"}]}"""

_BC_GUIDANCE = """
When to use bc_overrides (use role defaults otherwise):
- Compressible inlet pressure: {"p": {"type": "totalPressure", "p0": "uniform <abs_Pa>"}}
  pair with: {"U": {"type": "pressureInletOutletVelocity", "value": "uniform (0 0 0)"}}
- Compressible outlet (back-flow safe): {"U": {"type": "inletOutlet", "inletValue": "uniform (0 0 0)", "value": "uniform (0 0 0)"}}
- Wall p for compressible: {"p": {"type": "fixedFluxPressure", "value": "uniform 0"}}
- Mass-flow inlet velocity: {"U": {"type": "flowRateInletVelocity", "massFlowRate": <kg/s>}}

When to use custom_fv_solution (relaxationFactors):
- High-speed or highly swirling compressible SIMPLE: p~0.05, rho~0.05, U~0.2, h~0.3, k~0.3, epsilon~0.3
- Mildly coupled compressible SIMPLE: p~0.3, U~0.5, h~0.5
- Incompressible channel/duct: p~0.3, U~0.7

IMPORTANT — transient compressible flow (fluid module, solver_mode=transient):
DO NOT write a custom_fv_solution. The standard preset already renders a valid
PIMPLE loop, the energy (h) solver, and rho relaxation for this case. Omit
custom_fv_solution entirely; a hand-written body almost always drops the PIMPLE
sub-dict or the h solver entry and fails startup with "keyword ... is undefined".

When to use turbulence_model:
- Strong adverse pressure gradient or separation: kOmegaSST
- External aerodynamics / thin boundary layer: SpalartAllmaras
- Swirling / recirculating flows: realizableKE or RNGkEpsilon
- Default general: kEpsilon
"""

_SYSTEM = (
    "You convert a fluid-simulation description into a compact JSON intent for "
    "OpenFOAM. Output ONLY the JSON object — no prose, no markdown fences. "
    "Use exactly this schema:\n" + _SCHEMA + "\n\n" + _EXAMPLE + _BC_GUIDANCE
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
                "flow_type": {"type": "string", "enum": ["incompressible", "compressible"]},
                "solver_mode": {"type": "string", "enum": ["transient", "steadyState"]},
                "fluid_species": {"type": "string",
                                  "enum": ["air", "nitrogen", "co2", "oxygen", "helium", "water_vapour"]},
                "nu": {"type": "number"},
                "p_ambient": {"type": "number"},
                "temperature": {"type": "number"},
                "geometry": {"type": "string", "enum": ["box", "external_mesh"]},
                "box_size": {"type": "array", "items": {"type": "number"}},
                "cells": {"type": "array", "items": {"type": "integer"}},
                "turbulence_model": {
                    "type": "string",
                    "enum": ["kEpsilon", "kOmegaSST", "SpalartAllmaras", "realizableKE", "RNGkEpsilon", "LamBremhorstKE"],
                },
                "custom_fv_schemes": {"type": "string"},
                "custom_fv_solution": {"type": "string"},
                "patches": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "role": {"type": "string",
                                     "enum": ["inlet", "outlet", "wall", "movingWall", "empty", "cyclic", "symmetry"]},
                            "velocity": {"type": "array", "items": {"type": "number"}},
                            "pressure": {"type": "number"},
                            "temperature": {"type": "number"},
                            "faces": {
                                "type": "array",
                                "items": {"type": "string",
                                          "enum": ["xMin", "xMax", "yMin", "yMax", "zMin", "zMax"]},
                            },
                            "bc_overrides": {
                                "type": "object",
                                "additionalProperties": {
                                    "type": "object",
                                    "additionalProperties": {"type": "string"},
                                },
                            },
                        },
                        "required": ["name", "role"],
                    },
                },
            },
            "required": ["regime", "geometry", "patches"],
        },
    },
}

# Schema for the patch_files node: the LLM emits a dict of file paths -> corrected content.
_PATCH_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "file_patches",
        "strict": False,
        "schema": {
            "type": "object",
            "properties": {
                "file_overrides": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                },
            },
            "required": ["file_overrides"],
        },
    },
}

_PATCH_SYSTEM = (
    "You are an OpenFOAM v13 expert. You will be given an error from a failed "
    "OpenFOAM validation run and the current content of the offending file(s). "
    "Output ONLY a JSON object with this schema: "
    '{"file_overrides": {"<relative/path>": "<full corrected file content>"}}. '
    "Write the complete, corrected file content verbatim — no prose, no markdown fences."
)


def _extract_json(text: str) -> str:
    """Strip markdown fences/comments and isolate the outermost {...} span."""
    text = re.sub(r"```(?:json)?", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1:
        text = text[start:end + 1]
    text = re.sub(r"//[^\n]*", "", text)  # tolerate stray // comments
    text = re.sub(r",(\s*[}\]])", r"\1", text)  # tolerate trailing commas
    return text.strip()


def _extract_fenced_json(text: str) -> str:
    """Isolate the outermost {...} span WITHOUT stripping `//` comments.

    `extract_file_patches` embeds whole OpenFOAM dictionary files (which are
    full of `//` comment lines and `//` separators) inside the JSON string
    values. Reusing `_extract_json`'s comment-stripping there corrupts the
    JSON, so this lighter variant only drops fences and trailing commas.
    """
    text = re.sub(r"```(?:json)?", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1:
        text = text[start:end + 1]
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


def _chat_create(client, config: LLMConfig, messages: list[dict], response_format: dict | None = None):
    """Call chat.completions.create, tolerating backend quirks:
    - some backends (e.g. reasoning models like gpt-5-mini on the Siemens SDC
      LLM Gateway) reject temperature=0, only accepting the default (1);
    - some backends don't support `response_format=json_schema` at all.
    Tries the strictest combination first, degrading until one is accepted.
    `response_format` defaults to the intent schema; pass the patch schema
    (`_PATCH_RESPONSE_FORMAT`) for `extract_file_patches`.
    """
    rf = response_format or _RESPONSE_FORMAT
    attempts = [
        {"temperature": 0, "response_format": rf},
        {"response_format": rf},
        {"temperature": 0},
        {},
    ]
    last_exc: Exception | None = None
    for kwargs in attempts:
        try:
            return client.chat.completions.create(model=config.model, messages=messages, **kwargs)
        except Exception as e:  # noqa: BLE001 - deliberately broad; trying next combo
            last_exc = e
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("All LLM client chat completion attempts failed")


def extract_intent(nl: str, config: LLMConfig | None = None, max_retries: int = 3,
                    known_patches: list[MeshPatch] | None = None) -> CaseIntent:
    """NL -> validated CaseIntent with RAG context grounding and Skill enrichment.

    Validation is three-stage (fast inner loop, no Docker):
      1. Pydantic schema validation — types, enums, required fields.
      1.5 EnvelopeConstraint — clamp LLM-emitted numbers into curated physically
         plausible ranges (nu, p_ambient, temperature, velocities, pressures,
         box_size, cells); unit-error outliers are fed back to the LLM.
      2. IntentValidator semantic check — BC types, required BC keys, field
         applicability, turbulence model name, relaxation factor ranges.
    All failure modes are fed back to the LLM in the same retry loop.
    """
    from knowledge.envelopes import EnvelopeConstraint
    from knowledge.intent_validator import IntentValidator
    config = config or LLMConfig()
    client = make_client(config)

    retriever = KnowledgeRetriever()
    rag_context = retriever.format_prompt_context(nl)
    validator = IntentValidator()
    envelope = EnvelopeConstraint()

    user_content = nl + (_known_patches_hint(known_patches) if known_patches else "") + rag_context
    messages = [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": user_content}]

    skill_registry = SkillRegistry()
    last_err = ""
    for attempt_idx in range(1, max_retries + 1):
        resp = _chat_create(client, config, messages)
        raw = resp.choices[0].message.content or ""
        try:
            intent = CaseIntent(**json.loads(_extract_json(raw)))
            print("      -> [Stage 1/2] Pydantic schema validation PASSED", flush=True)
        except (json.JSONDecodeError, PydanticValidationError, TypeError) as e:
            last_err = str(e)
            print(f"      -> [Stage 1/2] Pydantic validation FAILED (attempt {attempt_idx}/{max_retries}): {last_err}", flush=True)
            messages += [
                {"role": "assistant", "content": raw},
                {"role": "user",
                 "content": f"That was not valid JSON/schema. Error: {last_err}. "
                            "Return only the corrected JSON object."},
            ]
            continue

        # Stage 1.5: deterministic physical-envelope constraint. Clamp numbers
        # into plausible ranges; reject only unit/dimension-level outliers.
        intent, env_notes, env_errors = envelope.apply(intent)
        for note in env_notes:
            print(f"         * constraint: {note}", flush=True)
        if env_errors:
            err_lines = "\n".join(f"- {e}" for e in env_errors)
            last_err = err_lines
            print(f"      -> [Stage 1.5/2] EnvelopeConstraint FAILED with {len(env_errors)} error(s):", flush=True)
            for err in env_errors:
                print(f"         * {err}", flush=True)
            messages += [
                {"role": "assistant", "content": raw},
                {"role": "user",
                 "content": (
                     f"Your intent has values outside physically plausible ranges:\n"
                     f"{err_lines}\n"
                     "Fix all listed values (mind the units) and return only the corrected JSON object."
                 )},
            ]
            continue

        # Stage 2: deterministic semantic validation against knowledge registries.
        print("      -> [Stage 2/2] Running IntentValidator against OpenFOAM registries...", flush=True)
        semantic_errors = validator.validate(intent, known_patches=known_patches)
        if semantic_errors:
            err_lines = "\n".join(f"- {e}" for e in semantic_errors)
            last_err = err_lines
            print(f"      -> [Stage 2/2] IntentValidator FAILED with {len(semantic_errors)} error(s):", flush=True)
            for err in semantic_errors:
                print(f"         * {err}", flush=True)
            messages += [
                {"role": "assistant", "content": raw},
                {"role": "user",
                 "content": (
                     f"Your intent has semantic errors that would cause OpenFOAM to fail:\n"
                     f"{err_lines}\n"
                     "Fix all listed errors and return only the corrected JSON object."
                 )},
            ]
            continue

        print("      -> [Stage 2/2] IntentValidator PASSED (0 semantic errors)", flush=True)
        enriched_intent = skill_registry.apply_skills(intent, nl, known_patches)
        return enriched_intent

    raise ValueError(f"Could not extract a valid CaseIntent after {max_retries} tries: {last_err}")


def extract_file_patches(prompt: str, config: LLMConfig | None = None,
                         max_retries: int = 2) -> dict[str, str]:
    """Ask the LLM to emit direct file-content patches for a failing OpenFOAM file.

    Returns a dict mapping relative case paths (e.g. "system/fvSolution") to
    corrected file content strings. Returns an empty dict on failure rather than
    raising, so the graph can fall back to full intent re-extraction.
    """
    config = config or LLMConfig()
    client = make_client(config)
    messages = [{"role": "system", "content": _PATCH_SYSTEM},
                {"role": "user", "content": prompt}]
    for _ in range(max_retries):
        try:
            resp = _chat_create(client, config, messages, response_format=_PATCH_RESPONSE_FORMAT)
            raw = resp.choices[0].message.content or ""
            data = json.loads(_extract_fenced_json(raw))
            overrides = data.get("file_overrides", {})
            if isinstance(overrides, dict) and overrides:
                return {k: v for k, v in overrides.items() if isinstance(k, str) and isinstance(v, str)}
        except Exception:  # noqa: BLE001
            pass
    return {}


@dataclass
class PipelineResult:
    intent: CaseIntent
    case: CaseFiles
    validation: ValidationResult


def generate(nl: str, case_dir: Path, config: LLMConfig | None = None,
             validate: bool = True, mesh_dir: Path | None = None) -> PipelineResult:
    """Full pipeline: NL -> intent -> spec -> rendered files (-> optional run).

    .. deprecated::
        Use the LangGraph graph (orchestration.graph.run) instead. This function
        is kept for backwards compatibility but does not apply file_overrides or
        route DICTIONARY_SYNTAX errors to the file-patching node.
    """
    import warnings
    warnings.warn(
        "generate() is deprecated; use orchestration.graph.run() for full agent capabilities.",
        DeprecationWarning,
        stacklevel=2,
    )
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
