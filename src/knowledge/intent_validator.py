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

from ir.case_types import CASE_TYPES
from ir.safe_exit import (
    SafeExitCategory,
    SafeExitEvidence,
    SafeExitValidationResult,
)
from preflight.ofdict import entries, section, sub_blocks

_KNOWLEDGE_DIR = Path(__file__).resolve().parent


def _load_json(filename: str) -> dict[str, Any]:
    path = _KNOWLEDGE_DIR / filename
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _normalize_text(s: str) -> str:
    """Lowercase and collapse whitespace for resilient substring matching."""
    return re.sub(r"\s+", " ", s.strip().lower())


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
        prompt_text: str | None = None,
        validation_result: Any | None = None,
        preflight_result: Any | None = None,
        case_dir: Path | str | None = None,
        remediation_history: list[Any] | None = None,
    ) -> list[IntentValidationError]:
        """Return a list of semantic errors in *intent*.

        Returns an empty list if the intent is valid.
        Errors are ordered from most to least critical.
        """
        errors: list[IntentValidationError] = []

        # 0. Safe Exit Report validation (if proposed)
        if intent.safe_exit is not None:
            errors.extend(self._check_safe_exit(
                intent=intent,
                prompt_text=prompt_text,
                validation_result=validation_result,
                preflight_result=preflight_result,
                case_dir=case_dir,
                remediation_history=remediation_history,
            ))
            if errors:
                return errors  # Halt immediately on safe exit rejection
            return []  # Valid safe exit passes without requiring patch/field expansion

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
        if (not case_type or case_type == "incompressibleFluid") and flow_type == "compressible":
            # Mirror ir.intent._resolve_case_type: a compressible intent with the
            # default family maps to the `fluid` module, whose fvSolution needs
            # the energy-solver entry this check enforces.
            case_type = "fluid"
        compressible = (flow_type == "compressible")
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
                ag = agglom_match.group(1)
                if ag not in valid_agglomerators:
                    return [IntentValidationError(
                        field=path,
                        message=f"Invalid GAMG agglomerator '{ag}'. Valid choices: {sorted(valid_agglomerators)}."
                    )]

        # Outer braces check
        if body.strip().startswith("{") and body.strip().endswith("}"):
            return [IntentValidationError(
                field=path,
                message="custom_fv_solution body must not be wrapped in extra outer braces '{ ... }'."
            )]

        # Compressible density (rho) relaxation check (only when fields sub-dict is explicitly provided)
        if compressible:
            fields_match = re.search(r"fields\s*\{([^}]+)\}", body)
            if fields_match:
                f_body = fields_match.group(1)
                if not re.search(r"\brho\b", f_body):
                    return [IntentValidationError(
                        field=path,
                        message=(
                            "Custom fvSolution `relaxationFactors.fields` block for compressible flow must include "
                            "a density relaxation factor 'rho' (e.g. 'rho 0.05;') to absorb pressure-density "
                            "coupling shocks, or omit custom_fv_solution to use the standard preset."
                        )
                    )]

        # Check individual linear solver blocks inside solvers { ... }
        solvers_text = section(body, "solvers")
        if solvers_text:
            for name, block in sub_blocks(solvers_text):
                e = entries(block)
                kind = e.get("solver", "").strip()
                if "rho" in name and kind == "GAMG":
                    return [IntentValidationError(
                        field=path,
                        message=(
                            f"Custom fvSolution solver block '{name}' cannot use GAMG for density. "
                            "Use smoothSolver with GaussSeidel, or omit custom_fv_solution to use standard presets."
                        )
                    )]
                need = {"GAMG": "smoother",
                        "PCG": "preconditioner",
                        "PBiCG": "preconditioner",
                        "PBiCGStab": "preconditioner",
                        "smoothSolver": "smoother"}.get(kind)
                if need and not e.get(need):
                    return [IntentValidationError(
                        field=path,
                        message=(
                            f"Custom fvSolution solver block '{name}' ({kind}) is missing required key '{need}'. "
                            f"Add '{need}' or omit custom_fv_solution to use standard presets."
                        )
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
        from ir.intent import (
            _assign_faces,  # deferred import avoids init-order coupling
        )
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

    def validate_safe_exit(
        self,
        intent: CaseIntent,
        prompt_text: str | None = None,
        validation_result: Any | None = None,
        preflight_result: Any | None = None,
        case_dir: Path | str | None = None,
        remediation_history: list[Any] | None = None,
        attempt: int = 1,
    ) -> SafeExitValidationResult:
        """Evaluate a proposed SafeExitReport and return an authoritative result."""
        from ir.safe_exit import SafeExitValidationResult
        report = intent.safe_exit
        if report is None:
            return SafeExitValidationResult(
                is_valid=False,
                rejection_reasons=["No safe_exit was proposed in the CaseIntent."],
                validated_at_attempt=attempt,
            )

        errors, verified_ev = self._check_safe_exit_detailed(
            intent=intent,
            prompt_text=prompt_text,
            validation_result=validation_result,
            preflight_result=preflight_result,
            case_dir=case_dir,
            remediation_history=remediation_history,
        )
        return SafeExitValidationResult(
            is_valid=len(errors) == 0,
            rejection_reasons=[e.message for e in errors],
            verified_evidence=verified_ev if not errors else [],
            validated_at_attempt=attempt,
        )

    def _check_safe_exit(
        self,
        intent: CaseIntent,
        prompt_text: str | None = None,
        validation_result: Any | None = None,
        preflight_result: Any | None = None,
        case_dir: Path | str | None = None,
        remediation_history: list[Any] | None = None,
    ) -> list[IntentValidationError]:
        """Strict, evidence-backed validation for proposed SafeExitReports."""
        errors, _ = self._check_safe_exit_detailed(
            intent=intent,
            prompt_text=prompt_text,
            validation_result=validation_result,
            preflight_result=preflight_result,
            case_dir=case_dir,
            remediation_history=remediation_history,
        )
        return errors

    def _check_safe_exit_detailed(
        self,
        intent: CaseIntent,
        prompt_text: str | None = None,
        validation_result: Any | None = None,
        preflight_result: Any | None = None,
        case_dir: Path | str | None = None,
        remediation_history: list[Any] | None = None,
    ) -> tuple[list[IntentValidationError], list[Any]]:
        """Internal helper returning (errors, verified_evidence)."""

        report = intent.safe_exit
        if report is None:
            return [], []

        errors: list[IntentValidationError] = []
        verified_evidence: list[SafeExitEvidence] = []

        # 1. Structural requirements
        summary = (report.error_summary or "").strip()
        if len(summary) < 20:
            errors.append(IntentValidationError(
                field="safe_exit.error_summary",
                message="Safe exit rejected: error_summary must have at least 20 non-whitespace characters explaining the deadlock.",
            ))

        if not report.evidence:
            errors.append(IntentValidationError(
                field="safe_exit.evidence",
                message="Safe exit rejected: at least one verifiable SafeExitEvidence item is required.",
            ))
        else:
            ev_details = []
            for i, ev in enumerate(report.evidence):
                detail = (ev.detail or "").strip()
                if len(detail) < 15:
                    errors.append(IntentValidationError(
                        field=f"safe_exit.evidence[{i}].detail",
                        message="Safe exit rejected: evidence detail must have at least 15 non-whitespace characters.",
                    ))
                norm = _normalize_text(detail)
                if norm in ev_details:
                    errors.append(IntentValidationError(
                        field=f"safe_exit.evidence[{i}].detail",
                        message=f"Safe exit rejected: duplicate evidence item '{detail}'.",
                    ))
                ev_details.append(norm)

        if not report.exhausted_alternatives:
            errors.append(IntentValidationError(
                field="safe_exit.exhausted_alternatives",
                message="Safe exit rejected: at least one exhausted alternative or ruled-out remedy must be listed.",
            ))
        else:
            alt_items = []
            for i, alt in enumerate(report.exhausted_alternatives):
                alt_str = (alt or "").strip()
                if len(alt_str) < 15:
                    errors.append(IntentValidationError(
                        field=f"safe_exit.exhausted_alternatives[{i}]",
                        message="Safe exit rejected: exhausted_alternatives item must have at least 15 characters.",
                    ))
                norm = _normalize_text(alt_str)
                if norm in alt_items:
                    errors.append(IntentValidationError(
                        field=f"safe_exit.exhausted_alternatives[{i}]",
                        message=f"Safe exit rejected: duplicate exhausted alternative '{alt_str}'.",
                    ))
                alt_items.append(norm)

        if not report.user_action_required:
            errors.append(IntentValidationError(
                field="safe_exit.user_action_required",
                message="Safe exit rejected: at least one concrete user action is required to resume the case.",
            ))
        else:
            act_items = []
            for i, act in enumerate(report.user_action_required):
                act_str = (act or "").strip()
                if len(act_str) < 15:
                    errors.append(IntentValidationError(
                        field=f"safe_exit.user_action_required[{i}]",
                        message="Safe exit rejected: user_action_required item must have at least 15 characters.",
                    ))
                norm = _normalize_text(act_str)
                if norm in act_items:
                    errors.append(IntentValidationError(
                        field=f"safe_exit.user_action_required[{i}]",
                        message=f"Safe exit rejected: duplicate user action item '{act_str}'.",
                    ))
                act_items.append(norm)

        if errors:
            return errors, []

        # 2. Anti-laziness & Recoverability Rejection Guardrails
        combined_text = _normalize_text(summary + " " + " ".join(e.detail for e in report.evidence))
        
        # Courant / Timestep check:
        if any(w in combined_text for w in ["courant", "timestep", "delta_t", "deltat"]) and not any(
            w in combined_text for w in ["negative cell volume", "negative volume", "min volume", "non-manifold", "unclosed", "missing species", "unsupported"]
        ):
            errors.append(IntentValidationError(
                field="safe_exit",
                message=(
                    "Safe exit rejected: Courant number and timestep stability are automatically managed by "
                    "adaptive time-stepping (adjustTimeStep/maxCo/deltaT); adjust intent or velocity instead of exiting."
                ),
            ))

        # Relaxation / Stiffness check:
        if any(w in combined_text for w in ["relaxation factor", "stiffness", "linear solver", "gamg smooth"]) and not any(
            w in combined_text for w in ["negative cell volume", "negative volume", "min volume", "non-manifold", "unclosed", "missing species", "unsupported"]
        ):
            errors.append(IntentValidationError(
                field="safe_exit",
                message=(
                    "Safe exit rejected: Numerical solver stiffness can be resolved using bounded upwind schemes "
                    "or relaxation factors in fvSolution."
                ),
            ))

        # 3. Remediation Attempt IDs Verification (if provided)
        if report.remediation_attempt_ids and remediation_history is not None:
            recorded_attempts = {r.attempt for r in remediation_history}
            for att_id in report.remediation_attempt_ids:
                if att_id not in recorded_attempts:
                    errors.append(IntentValidationError(
                        field="safe_exit.remediation_attempt_ids",
                        message=f"Safe exit rejected: cited remediation attempt #{att_id} was not found in recorded execution history {sorted(recorded_attempts)}.",
                    ))

        # 4. Evidence Substring Matching against Trusted Execution State
        check_mesh_log = ""
        solver_log = ""
        if validation_result is not None:
            stdout = getattr(validation_result, "stdout", "") or ""
            stderr = getattr(validation_result, "stderr", "") or ""
            log = getattr(validation_result, "log", "") or ""
            stage = getattr(validation_result, "stage", "") or ""
            if stage == "checkMesh" or "checkMesh" in stdout or "checkMesh" in log:
                check_mesh_log += " " + stdout + " " + stderr + " " + log
            solver_log += " " + stdout + " " + stderr + " " + log
            for err in getattr(validation_result, "errors", []):
                solver_log += f" {err.message} {err.failure_class.name}"
                if stage == "checkMesh":
                    check_mesh_log += f" {err.message} {err.failure_class.name}"

        if preflight_result is not None:
            if hasattr(preflight_result, "as_evidence"):
                check_mesh_log += " " + preflight_result.as_evidence()
            if hasattr(preflight_result, "metrics") and preflight_result.metrics is not None:
                check_mesh_log += " " + (preflight_result.metrics.raw or "")
                if preflight_result.metrics.min_volume is not None and preflight_result.metrics.min_volume <= 0:
                    check_mesh_log += f" min volume <= 0 (min_volume={preflight_result.metrics.min_volume})"
            if hasattr(preflight_result, "audit"):
                for finding in preflight_result.audit:
                    check_mesh_log += f" {finding.code} {finding.message}"

        check_mesh_norm = _normalize_text(check_mesh_log)
        solver_norm = _normalize_text(solver_log)
        prompt_norm = _normalize_text(prompt_text or "")

        for i, ev in enumerate(report.evidence):
            norm_detail = _normalize_text(ev.detail)
            if ev.source == "user_prompt":
                if prompt_text and norm_detail not in prompt_norm:
                    errors.append(IntentValidationError(
                        field=f"safe_exit.evidence[{i}]",
                        message=f"Safe exit rejected: Cited evidence was not found in user prompt: '{ev.detail}'.",
                    ))
                else:
                    verified_evidence.append(ev)
            elif ev.source == "check_mesh":
                if not check_mesh_norm or norm_detail not in check_mesh_norm:
                    errors.append(IntentValidationError(
                        field=f"safe_exit.evidence[{i}]",
                        message=f"Safe exit rejected: Cited check_mesh evidence was not found in recorded checkMesh logs: '{ev.detail}'.",
                    ))
                else:
                    verified_evidence.append(ev)
            elif ev.source == "solver_log":
                if not solver_norm or norm_detail not in solver_norm:
                    errors.append(IntentValidationError(
                        field=f"safe_exit.evidence[{i}]",
                        message=f"Safe exit rejected: Cited solver_log evidence was not found in recorded solver logs: '{ev.detail}'.",
                    ))
                else:
                    verified_evidence.append(ev)
            elif ev.source == "case_file":
                if not ev.file_path:
                    errors.append(IntentValidationError(
                        field=f"safe_exit.evidence[{i}]",
                        message="Safe exit rejected: Evidence source 'case_file' requires a relative file_path.",
                    ))
                elif Path(ev.file_path).is_absolute() or ".." in Path(ev.file_path).parts:
                    errors.append(IntentValidationError(
                        field=f"safe_exit.evidence[{i}]",
                        message=f"Safe exit rejected: file_path '{ev.file_path}' must be relative within the case directory.",
                    ))
                elif case_dir:
                    case_root = Path(case_dir).resolve()
                    fpath = (case_root / ev.file_path).resolve()
                    if not str(fpath).startswith(str(case_root)):
                        errors.append(IntentValidationError(
                            field=f"safe_exit.evidence[{i}]",
                            message=f"Safe exit rejected: file_path '{ev.file_path}' attempts path traversal outside case directory.",
                        ))
                    elif not fpath.is_file() or norm_detail not in _normalize_text(fpath.read_text(errors="ignore")):
                        errors.append(IntentValidationError(
                            field=f"safe_exit.evidence[{i}]",
                            message=f"Safe exit rejected: Case file '{ev.file_path}' does not contain cited excerpt: '{ev.detail}'.",
                        ))
                    else:
                        verified_evidence.append(ev)

        # 4. Category-Specific Proof & User Action Requirements
        cat = report.category
        actions_norm = _normalize_text(" ".join(report.user_action_required))

        if cat == SafeExitCategory.CORRUPT_OR_UNCLOSED_MESH:
            fatal_keywords = [
                "negative cell volume", "negative volume", "min volume <= 0", "min volume < 0",
                "zero-area face", "zero area face", "collapsed face", "non-manifold",
                "unclosed boundary", "unclosed surface", "cannot read polymesh",
                "invalid face topology", "illegal face",
            ]
            has_fatal = any(k in combined_text for k in fatal_keywords)
            has_only_quality = any(k in combined_text for k in ["non-orthogonality", "skewness", "aspect ratio"]) and not has_fatal
            if not has_fatal or has_only_quality:
                errors.append(IntentValidationError(
                    field="safe_exit.category",
                    message=(
                        "Safe exit rejected: corrupt_or_unclosed_mesh requires proof of fatal topology/geometry defects "
                        "(negative cell volumes, zero-area faces, unclosed boundaries). Mesh quality warnings alone (non-orthogonality/skewness) "
                        "do not justify safe exit."
                    ),
                ))
            if not any(k in actions_norm for k in ["cad", "mesh", "polymesh", "manifold", "watertight", "repair", "re-export", "volume", "geometry"]):
                errors.append(IntentValidationError(
                    field="safe_exit.user_action_required",
                    message="Safe exit rejected: corrupt_or_unclosed_mesh action must instruct user on CAD/mesh repair or re-export.",
                ))

        elif cat == SafeExitCategory.CONTRADICTORY_PHYSICS:
            prompt_evs = [e for e in report.evidence if e.source == "user_prompt"]
            if len(prompt_evs) < 2:
                errors.append(IntentValidationError(
                    field="safe_exit.evidence",
                    message="Safe exit rejected: contradictory_physics requires at least two distinct evidence citations from user_prompt demonstrating the conflict.",
                ))
            if not any(k in actions_norm for k in ["choose", "clarify", "select", "specify", "reconcile", "decide", "resolve"]):
                errors.append(IntentValidationError(
                    field="safe_exit.user_action_required",
                    message="Safe exit rejected: contradictory_physics action must ask user to clarify or choose between conflicting parameters.",
                ))

        elif cat == SafeExitCategory.MISSING_ESSENTIAL_DATA:
            if not any(k in actions_norm for k in ["provide", "specify", "set", "supply", "input", "value", "units", "concentration", "species", "thermo", "property", "table", "rate", "mechanism"]):
                errors.append(IntentValidationError(
                    field="safe_exit.user_action_required",
                    message="Safe exit rejected: missing_essential_data action must specify the exact missing physical property/data needed.",
                ))

        elif cat == SafeExitCategory.UNSUPPORTED_PHYSICS:
            if not any(e.source == "user_prompt" for e in report.evidence):
                errors.append(IntentValidationError(
                    field="safe_exit.evidence",
                    message="Safe exit rejected: unsupported_physics must cite the requested unsupported capability from user_prompt.",
                ))
            supported_keys = [
                "approximation", "case_type", "incompressible", "compressible",
                "supported", "module", "vof", "euler", *CASE_TYPES.keys()
            ]
            if not any(k in actions_norm for k in supported_keys):
                errors.append(IntentValidationError(
                    field="safe_exit.user_action_required",
                    message="Safe exit rejected: unsupported_physics action must suggest a supported solver module or approximation from CASE_TYPES.",
                ))

        return errors, verified_evidence
