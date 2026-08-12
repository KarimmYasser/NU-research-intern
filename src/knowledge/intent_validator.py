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
from pathlib import Path
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

        # 5. Patch names referenced in bc_overrides must exist in mesh (if we know patches)
        if known_patches:
            errors.extend(self._check_patch_names(intent, known_patches))

        return errors

    # ------------------------------------------------------------------
    # Private checks
    # ------------------------------------------------------------------

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

    def _check_patch_names(
        self, intent: CaseIntent, known_patches: list[MeshPatch]
    ) -> list[IntentValidationError]:
        """Check that every patch with bc_overrides actually exists in the mesh."""
        known_names = {p.name for p in known_patches}
        errors = []
        for i, patch in enumerate(intent.patches):
            bc_overrides = getattr(patch, "bc_overrides", None)
            if bc_overrides and patch.name not in known_names:
                errors.append(IntentValidationError(
                    field=f"patches[{i}].name",
                    message=(
                        f"Patch '{patch.name}' has bc_overrides but does not exist in the mesh. "
                        f"Available patches: {sorted(known_names)}."
                    )
                ))
        return errors
