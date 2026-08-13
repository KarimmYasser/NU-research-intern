"""Deterministic semantic validator for agent-chosen CaseIntent fields.

Sits between the LLM JSON output (post-Pydantic) and the renderer.
Checks every agent-chosen override against curated knowledge registries
without requiring OpenFOAM or Docker — errors are returned as structured
messages that the pipeline feeds back to the LLM in its existing retry loop.

Validation hierarchy (fastest → slowest):
  1. BC type name exists in bc_types.json registry
  2. BC type is applicable to the target field
  3. BC type's required keys are all present
  4. Turbulence model name exists in turbulence_models.json registry
  5. Relaxation factors are in the physically valid range (0, 1]
  6. Patch names referenced in bc_overrides exist in the known mesh patches
  7. fvSchemes / fvSolution bodies are parseable as OpenFOAM dict syntax

None of these checks require the renderer, a file-system write, or Docker.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ir.intent import CaseIntent
    from mesh.mesh_io import MeshPatch

import json

_KNOWLEDGE_DIR = Path(__file__).resolve().parent


def _load_json(filename: str) -> dict[str, Any]:
    path = _KNOWLEDGE_DIR / filename
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


@dataclass
class IntentValidationError:
    """A single semantic validation failure in a CaseIntent."""
    field: str    # dot-path to the offending value, e.g. "patches[0].bc_overrides.p"
    message: str  # human-readable explanation

    def __str__(self) -> str:
        return f"{self.field}: {self.message}"


class IntentValidator:
    """Deterministic semantic validator for agent-chosen CaseIntent fields.

    All checks are pure Python against local JSON registries — no LLM, no
    renderer, no Docker.  Instantiate once; call validate() per intent.
    """

    def __init__(self, knowledge_dir: Path | None = None):
        kdir = knowledge_dir or _KNOWLEDGE_DIR
        raw_bc = json.loads((kdir / "bc_types.json").read_text()) if (kdir / "bc_types.json").exists() else {}
        raw_turb = json.loads((kdir / "turbulence_models.json").read_text()) if (kdir / "turbulence_models.json").exists() else {}

        # BC registry: type_name -> {applicable_fields, required_keys, optional_keys}
        self._bc_registry: dict[str, dict] = {
            entry["type"]: entry
            for entry in raw_bc.get("bc_types", [])
        }

        # Turbulence model registry: name -> entry
        ras = {m["name"]: m for m in raw_turb.get("ras_models", [])}
        les = {m["name"]: m for m in raw_turb.get("les_models", [])}
        self._turb_registry: dict[str, dict] = {**ras, **les}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def validate(
        self,
        intent: CaseIntent,
        known_patches: list[MeshPatch] | None = None,
    ) -> list[IntentValidationError]:
        """Return a list of semantic errors in *intent*.

        Returns an empty list if the intent is valid.
        Errors are ordered from most to least critical.
        """
        errors: list[IntentValidationError] = []

        # 1. Turbulence model
        errors.extend(self._check_turbulence_model(intent))

        # 2. Per-patch bc_overrides
        for i, patch in enumerate(intent.patches):
            bc_overrides = getattr(patch, "bc_overrides", None)
            if bc_overrides:
                errors.extend(
                    self._check_bc_overrides(bc_overrides, f"patches[{i}]({patch.name})")
                )

        # 3. Relaxation factors in custom_fv_solution
        if getattr(intent, "custom_fv_solution", None):
            errors.extend(
                self._check_relaxation_factors(intent.custom_fv_solution, "custom_fv_solution")
            )

        # 4. OpenFOAM dict syntax check for custom_fv_schemes / custom_fv_solution
        if getattr(intent, "custom_fv_schemes", None):
            errors.extend(
                self._check_foam_dict_syntax(intent.custom_fv_schemes, "custom_fv_schemes")
            )
        if getattr(intent, "custom_fv_solution", None):
            errors.extend(
                self._check_foam_dict_syntax(intent.custom_fv_solution, "custom_fv_solution")
            )
            errors.extend(
                self._check_fv_solution_blocks(intent, "custom_fv_solution")
            )

        # 5. Box-geometry face assignment (canonical fallback must not strand a patch)
        errors.extend(self._check_box_geometry(intent))

        # 6. Patch names must exist in the mesh (when a mesh is provided)
        if known_patches:
            errors.extend(self._check_patch_names(intent, known_patches))

        # 7. extra_files paths must stay within the case directory (no traversal).
        errors.extend(self._check_extra_files(intent))

        return errors

    # ------------------------------------------------------------------
    # Private checks
    # ------------------------------------------------------------------

    def _check_extra_files(self, intent: CaseIntent) -> list[IntentValidationError]:
        """Reject extra_files keys that escape the case directory (path traversal)
        or use absolute paths. All keys inside the case dir are allowed: extra
        files may target any depth (system/fvConstraints, constant/thermo/...)
        and are written after the standard layers, so an agent can also ship
        a whole-file override as a first-render extra.
        """
        extra = getattr(intent, "extra_files", None)
        if not extra:
            return []
        errors: list[IntentValidationError] = []
        for rel in extra:
            if not isinstance(rel, str) or not rel.strip():
                errors.append(IntentValidationError(
                    field=f"extra_files[{rel!r}]",
                    message="extra_files keys must be non-empty relative paths.",
                ))
                continue
            p = PurePosixPath(rel)
            if p.is_absolute() or ".." in p.parts:
                errors.append(IntentValidationError(
                    field=f"extra_files[{rel}]",
                    message="extra_files paths must stay inside the case directory.",
                ))
        return errors

    def _check_turbulence_model(self, intent: CaseIntent) -> list[IntentValidationError]:
        model = getattr(intent, "turbulence_model", None)
        if not model:
            return []  # not set → defaults used, always valid
        if model not in self._turb_registry:
            known = sorted(self._turb_registry.keys())
            return [IntentValidationError(
                field="turbulence_model",
                message=(
                    f"Unknown turbulence model '{model}'. "
                    f"Valid options: {known}. "
                    "Use one of the listed names exactly as shown."
                )
            )]
        return []

    def _check_bc_overrides(
        self, bc_overrides: dict[str, dict[str, str]], path_prefix: str
    ) -> list[IntentValidationError]:
        errors = []
        for field_name, bc_dict in bc_overrides.items():
            field_path = f"{path_prefix}.bc_overrides.{field_name}"
            if not isinstance(bc_dict, dict):
                errors.append(IntentValidationError(
                    field=field_path,
                    message=f"bc_overrides value must be a dict, got {type(bc_dict).__name__}."
                ))
                continue

            bc_type = bc_dict.get("type")
            if not bc_type:
                errors.append(IntentValidationError(
                    field=field_path,
                    message="bc_override dict must contain a 'type' key."
                ))
                continue

            # Check type is known
            if bc_type not in self._bc_registry:
                known = sorted(self._bc_registry.keys())
                errors.append(IntentValidationError(
                    field=f"{field_path}.type",
                    message=(
                        f"Unknown BC type '{bc_type}'. "
                        f"Valid types: {known}."
                    )
                ))
                continue  # can't check required keys if type unknown

            entry = self._bc_registry[bc_type]

            # Check field applicability
            applicable = entry.get("applicable_fields", [])
            if field_name not in applicable:
                errors.append(IntentValidationError(
                    field=f"{field_path}.type",
                    message=(
                        f"BC type '{bc_type}' cannot be applied to field '{field_name}'. "
                        f"It is only valid for fields: {applicable}."
                    )
                ))

            # Check required keys
            required = entry.get("required_keys", [])
            for req_key in required:
                if req_key not in bc_dict:
                    errors.append(IntentValidationError(
                        field=f"{field_path}",
                        message=(
                            f"BC type '{bc_type}' requires key '{req_key}' "
                            f"but it is missing. Provided keys: {list(bc_dict.keys())}."
                        )
                    ))

            # Check alpha phase fraction bounds [0, 1]
            if field_name.startswith("alpha"):
                for val_key in ("value", "inletValue"):
                    val_str = bc_dict.get(val_key)
                    if val_str is not None and isinstance(val_str, (int, float, str)):
                        try:
                            s = val_str.replace("uniform", "").strip() if isinstance(val_str, str) else str(val_str)
                            val = float(s)
                            if val < 0.0 or val > 1.0:
                                errors.append(IntentValidationError(
                                    field=f"{field_path}.{val_key}",
                                    message=f"Phase fraction boundary value '{val}' for field '{field_name}' must be within physical bounds [0, 1]."
                                ))
                        except ValueError:
                            pass

        return errors

    def _check_relaxation_factors(self, fv_solution_body: str, path: str) -> list[IntentValidationError]:
        """Scan fvSolution body for relaxationFactors entries and check 0 < r <= 1."""
        errors = []
        # Match patterns like: p  0.05; or U 1.5;
        pattern = re.compile(r"\b(\w+)\s+([\d.eE+\-]+)\s*;")
        # Only check inside a relaxationFactors block
        rf_block = re.search(r"relaxationFactors\s*\{([^}]*(?:\{[^}]*\}[^}]*)*)\}", fv_solution_body, re.DOTALL)
        if not rf_block:
            return errors
        block_text = rf_block.group(1)
        for m in pattern.finditer(block_text):
            key, val_str = m.group(1), m.group(2)
            try:
                val = float(val_str)
            except ValueError:
                continue
            if not (0.0 < val <= 1.0):
                errors.append(IntentValidationError(
                    field=f"{path}.relaxationFactors.{key}",
                    message=(
                        f"Relaxation factor for '{key}' is {val}. "
                        "Must be in range (0, 1]. Values > 1 cause divergence; 0 freezes the field."
                    )
                ))
        return errors

    def _check_foam_dict_syntax(self, body: str, path: str) -> list[IntentValidationError]:
        """Lightweight brace-balance check for OpenFOAM dictionary bodies."""
        depth = 0
        for i, ch in enumerate(body):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
            if depth < 0:
                return [IntentValidationError(
                    field=path,
                    message=f"Unmatched '}}' at character {i} — check brace balance in the dict body."
                )]
        if depth != 0:
            return [IntentValidationError(
                field=path,
                message=f"Unclosed '{{' — {depth} opening brace(s) have no matching '}}'. Check the dict body."
            )]
        return []

    def _check_fv_solution_blocks(self, intent: CaseIntent, path: str) -> list[IntentValidationError]:
        """Compressible `fluid` fvSolution bodies must carry the blocks the
        module reads at runtime.

        The OpenFOAM v13 `fluid` module (compressible) fails at startup with
        "keyword PIMPLE is undefined in dictionary .../fvSolution" when the
        transient solution-control sub-dict is missing — which is exactly what
        a model's hand-written body tends to omit. Catch it here (cheap, no
        Docker) instead of on the validation ladder.
        """
        case_type = getattr(intent, "case_type", "incompressibleFluid")
        flow_type = getattr(intent, "flow_type", "incompressible")
        if not case_type or case_type == "incompressibleFluid":
            # Mirror ir.intent._resolve_case_type: a compressible intent with the
            # default family maps to the `fluid` module, whose fvSolution needs
            # the energy-solver entry this check enforces.
            if flow_type == "compressible":
                case_type = "fluid"
        body = getattr(intent, "custom_fv_solution", "") or ""
        if case_type != "fluid":
            return []
        required = "PIMPLE" if getattr(intent, "solver_mode", "transient") != "steadyState" else "SIMPLE"
        if not re.search(rf"\b{required}\s*{{", body):
            return [IntentValidationError(
                field=path,
                message=(
                    f"Custom fvSolution for the compressible 'fluid' solver must "
                    f"include a '{required}' sub-dict (this {getattr(intent, 'solver_mode', 'transient')} "
                    "solver reads it at startup). Add it or omit custom_fv_solution "
                    "to use the standard preset."
                )
            )]

# The energy field. The `fluid` module solves for enthalpy and looks up
        # its linear solver in the fvSolution `solvers` sub-dict; a hand-written
        # `solvers` block that lists U/k/epsilon but omits h dies at startup with
        # "keyword h is undefined in dictionary .../fvSolution/solvers". Require
        # an entry that covers it (explicit `h`/`he`, or a pattern like "(U|h|e)").
        solvers_match = re.search(r"\bsolvers\s*\{", body)
        if solvers_match:
            entry_names = self._dict_entry_names(body, body.find("{", solvers_match.start()) + 1)
            energy_covered = any(
                re.search(r"\bh\b|\bhe\b|\be\b", name)  # bare keys h/he/e
                or re.search(r"[|(]h(?:[|)])|[|(]he(?:[|)])|[|(]e(?:[|)])", name)  # patterns like "(U|h|e)"
                for name in entry_names
            )
            if not energy_covered:
                return [IntentValidationError(
                    field=path,
                    message=(
                        "Custom fvSolution `solvers` block for the compressible "
                        "'fluid' solver must define a solver for the energy field "
                        "(h / he / e) — the module reads e.g. solvers/h at startup. "
                        "Add an explicit solver block for it (or a regex entry like "
                        '"\\"(U|h|e|k|epsilon|R)\\""), or omit custom_fv_solution to use '
                        "the standard preset which already covers it."
                    )
                )]

        # Symmetric solvers like PCG cannot be used for asymmetric fields (h, U, k, epsilon, etc.)
        asymmetric_pcg = re.search(
            r'("(?:[^"]*\b(?:h|he|e|U|k|epsilon|omega|nut|alphat|rho)\b[^"]*)"|'
            r'\b(?:h|he|e|U|k|epsilon|omega|nut|alphat|rho)\b)\s*\{[^}]*\bsolver\s+PCG\b',
            body
        )
        if asymmetric_pcg:
            return [IntentValidationError(
                field=path,
                message=(
                    "Linear solver 'PCG' is only valid for symmetric matrices (e.g. pressure p/p_rgh). "
                    "Asymmetric transport fields (h, U, k, epsilon, omega, etc.) must use an asymmetric "
                    "matrix solver such as 'PBiCGStab', 'smoothSolver', or 'GAMG'."
                )
            )]

        # Check GAMG smoother and agglomerator keywords if GAMG is specified
        valid_smoothers = {"GaussSeidel", "symGaussSeidel", "DIC", "FDIC", "DILU", "nonBlockingGaussSeidel"}
        valid_agglomerators = {"faceAreaPair", "algebraicPair", "pair"}
        for match in re.finditer(r"\bsolver\s+GAMG\b[^}]*\}", body, re.DOTALL):
            block = match.group(0)
            smoother_match = re.search(r"\bsmoother\s+([A-Za-z0-9_]+)", block)
            if smoother_match:
                sm = smoother_match.group(1)
                if sm not in valid_smoothers:
                    return [IntentValidationError(
                        field=path,
                        message=f"Unknown GAMG smoother '{sm}'. Valid GAMG smoothers: {sorted(valid_smoothers)}"
                    )]
            agglom_match = re.search(r"\bagglomerator\s+([A-Za-z0-9_]+)", block)
            if agglom_match:
                agg = agglom_match.group(1)
                if agg not in valid_agglomerators:
                    return [IntentValidationError(
                        field=path,
                        message=f"Unknown GAMG agglomerator '{agg}'. Valid GAMG agglomerators: {sorted(valid_agglomerators)}"
                    )]

        return []

    @staticmethod
    def _dict_entry_names(body: str, offset: int) -> list[str]:
        """Top-level dictionary-entry names inside the braces starting at *offset*.

        Entries are the word (or quoted regex) immediately before a '{' at that
        brace level. Handles `p { ... }`, `"p.*" { ... }`, `"(U|h|e)" { ... }`,
        and skips non-block scalars (`U 0.1;`) and nested sub-dicts.
        """
        names: list[str] = []
        tokens = re.findall(r'"([^"]*)"|([^\s"]+)', body[offset:])
        depth = 0
        pending = None  # last token seen at depth 0 before a '{'
        for quoted, bare in tokens:
            token = quoted or bare
            if token == "{":
                depth += 1
                if depth == 1 and pending is not None:
                    names.append(pending)
                    pending = None
            elif token == "}":
                depth -= 1
            elif depth == 0:
                pending = token
        return names

    def _check_box_geometry(self, intent: CaseIntent) -> list[IntentValidationError]:
        """Box geometry: explicit faces must tile all six faces; the canonical
        fallback must not strand a patch face-less (too many patches for a box).

        For external meshes, faces are meaningless — reject them.
        """
        if getattr(intent, "geometry", "external_mesh") != "box":
            offenders = [p.name for p in intent.patches if p.faces]
            if offenders:
                return [IntentValidationError(
                    field="patches",
                    message=(
                        "`faces` is only valid for box geometry, but the case is "
                        f"external_mesh and patch(es) {offenders} list faces."
                    )
                )]
            return []
        from ir.intent import _assign_faces  # deferred import avoids init-order coupling
        try:
            _assign_faces(intent.patches)
        except ValueError as e:
            return [IntentValidationError(field="geometry", message=str(e))]
        return []

    def _check_patch_names(
        self, intent: CaseIntent, known_patches: list[MeshPatch]
    ) -> list[IntentValidationError]:
        """When a mesh is provided, every intent patch must exist in it — the
        boundary file is the single source of truth for patch names."""
        known_names = {p.name for p in known_patches}
        errors = []
        for i, patch in enumerate(intent.patches):
            if patch.name not in known_names:
                errors.append(IntentValidationError(
                    field=f"patches[{i}].name",
                    message=(
                        f"Patch '{patch.name}' does not exist in the provided mesh. "
                        f"Available patches: {sorted(known_names)}. "
                        "Use the mesh's patch names verbatim."
                    )
                ))
        return errors
