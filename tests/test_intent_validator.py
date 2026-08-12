"""Tests for the deterministic IntentValidator.

All checks are pure-Python against local JSON registries — no LLM, no renderer,
no Docker. Tests verify that:
  1. Valid intents pass with zero errors.
  2. Unknown BC type names are caught.
  3. BC types with missing required keys are caught.
  4. BC types applied to wrong fields are caught.
  5. Unknown turbulence model names are caught.
  6. Out-of-range relaxation factors are caught.
  7. Unbalanced braces in custom_fv_schemes/solution are caught.
  8. Patch names that don't exist in the known mesh are caught.
"""

from __future__ import annotations

import pytest

from ir.intent import CaseIntent, PatchIntent
from knowledge.intent_validator import IntentValidator


def _make_simple_intent(**overrides) -> CaseIntent:
    """Minimal valid compressible CaseIntent for testing."""
    data = dict(
        regime="turbulent",
        flow_type="compressible",
        solver_mode="steadyState",
        geometry="external_mesh",
        patches=[
            PatchIntent(name="inlet", role="inlet", velocity=(0, 0, -1.0), temperature=300.0),
            PatchIntent(name="outlet", role="outlet", pressure=100000.0),
            PatchIntent(name="walls", role="wall"),
        ],
    )
    data.update(overrides)
    return CaseIntent(**data)


@pytest.fixture
def validator():
    return IntentValidator()


# ─── Valid intent ──────────────────────────────────────────────────────────────

def test_valid_intent_no_errors(validator):
    intent = _make_simple_intent()
    errors = validator.validate(intent)
    assert errors == [], f"Expected no errors, got: {errors}"


def test_valid_intent_with_bc_override(validator):
    """totalPressure on p with required p0 — should be valid."""
    intent = _make_simple_intent(
        patches=[
            PatchIntent(
                name="inlet", role="inlet",
                bc_overrides={"p": {"type": "totalPressure", "p0": "uniform 400000"}}
            ),
            PatchIntent(name="outlet", role="outlet"),
            PatchIntent(name="walls", role="wall"),
        ]
    )
    errors = validator.validate(intent)
    assert errors == [], f"Expected no errors, got: {errors}"


def test_valid_turbulence_model(validator):
    intent = _make_simple_intent(turbulence_model="kOmegaSST")
    errors = validator.validate(intent)
    assert errors == []


def test_valid_relaxation_factors(validator):
    fv_solution = """
SIMPLE { nNonOrthogonalCorrectors 1; }
relaxationFactors { fields { p 0.3; } equations { U 0.7; } }
"""
    intent = _make_simple_intent(custom_fv_solution=fv_solution)
    errors = validator.validate(intent)
    assert errors == []


# ─── Unknown BC type ───────────────────────────────────────────────────────────

def test_unknown_bc_type_is_caught(validator):
    intent = _make_simple_intent(
        patches=[
            PatchIntent(
                name="inlet", role="inlet",
                bc_overrides={"p": {"type": "totalPressuer"}}  # typo
            ),
            PatchIntent(name="outlet", role="outlet"),
            PatchIntent(name="walls", role="wall"),
        ]
    )
    errors = validator.validate(intent)
    assert any("totalPressuer" in str(e) for e in errors), f"Typo not caught: {errors}"
    assert any("Unknown BC type" in str(e) for e in errors)


# ─── Missing required key ──────────────────────────────────────────────────────

def test_totalPressure_missing_p0_is_caught(validator):
    """totalPressure requires p0 — omitting it should fail."""
    intent = _make_simple_intent(
        patches=[
            PatchIntent(
                name="inlet", role="inlet",
                bc_overrides={"p": {"type": "totalPressure"}}  # missing p0
            ),
            PatchIntent(name="outlet", role="outlet"),
            PatchIntent(name="walls", role="wall"),
        ]
    )
    errors = validator.validate(intent)
    assert any("p0" in str(e) for e in errors), f"Missing p0 not caught: {errors}"
    assert any("requires key" in str(e) for e in errors)


def test_fixedValue_missing_value_is_caught(validator):
    """fixedValue requires value — omitting it should fail."""
    intent = _make_simple_intent(
        patches=[
            PatchIntent(
                name="inlet", role="inlet",
                bc_overrides={"T": {"type": "fixedValue"}}  # missing value
            ),
            PatchIntent(name="outlet", role="outlet"),
            PatchIntent(name="walls", role="wall"),
        ]
    )
    errors = validator.validate(intent)
    assert any("requires key" in str(e) for e in errors), f"Missing value not caught: {errors}"


# ─── Wrong field for BC type ───────────────────────────────────────────────────

def test_totalPressure_on_U_is_caught(validator):
    """totalPressure is only valid for p, not U."""
    intent = _make_simple_intent(
        patches=[
            PatchIntent(
                name="inlet", role="inlet",
                bc_overrides={"U": {"type": "totalPressure", "p0": "uniform 400000"}}
            ),
            PatchIntent(name="outlet", role="outlet"),
            PatchIntent(name="walls", role="wall"),
        ]
    )
    errors = validator.validate(intent)
    assert any("cannot be applied" in str(e) for e in errors), f"Wrong-field error not caught: {errors}"


def test_noSlip_on_p_is_caught(validator):
    """noSlip is only valid for U, not p."""
    intent = _make_simple_intent(
        patches=[
            PatchIntent(name="inlet", role="inlet"),
            PatchIntent(name="outlet", role="outlet"),
            PatchIntent(
                name="walls", role="wall",
                bc_overrides={"p": {"type": "noSlip"}}
            ),
        ]
    )
    errors = validator.validate(intent)
    assert any("cannot be applied" in str(e) for e in errors), f"noSlip on p not caught: {errors}"


# ─── Unknown turbulence model ──────────────────────────────────────────────────

def test_unknown_turbulence_model_is_caught(validator):
    intent = _make_simple_intent(turbulence_model="kOmgea")  # typo
    errors = validator.validate(intent)
    assert any("turbulence_model" in str(e) for e in errors)
    assert any("kOmgea" in str(e) for e in errors)


def test_known_turbulence_models_pass(validator):
    for model in ["kEpsilon", "kOmegaSST", "SpalartAllmaras", "realizableKE", "RNGkEpsilon"]:
        intent = _make_simple_intent(turbulence_model=model)
        errors = validator.validate(intent)
        assert errors == [], f"Model {model} should be valid, got: {errors}"


# ─── Relaxation factors out of range ─────────────────────────────────────────

def test_relaxation_factor_above_one_is_caught(validator):
    fv_solution = """
SIMPLE { nNonOrthogonalCorrectors 1; }
relaxationFactors { equations { p 5.0; U 0.7; } }
"""
    intent = _make_simple_intent(custom_fv_solution=fv_solution)
    errors = validator.validate(intent)
    assert any("relaxationFactors" in str(e) for e in errors), f"rf>1 not caught: {errors}"
    assert any("5.0" in str(e) for e in errors)


def test_relaxation_factor_zero_is_caught(validator):
    fv_solution = """
relaxationFactors { equations { U 0.0; } }
"""
    intent = _make_simple_intent(custom_fv_solution=fv_solution)
    errors = validator.validate(intent)
    assert any("0.0" in str(e) for e in errors), f"rf=0 not caught: {errors}"


# ─── OpenFOAM dict syntax ─────────────────────────────────────────────────────

def test_unbalanced_braces_in_fv_schemes_is_caught(validator):
    bad_schemes = "ddtSchemes { default steadyState; gradSchemes { default Gauss linear;"  # missing }}
    intent = _make_simple_intent(custom_fv_schemes=bad_schemes)
    errors = validator.validate(intent)
    assert any("custom_fv_schemes" in str(e) for e in errors), f"Bad braces not caught: {errors}"


def test_extra_closing_brace_is_caught(validator):
    bad_schemes = "ddtSchemes { default steadyState; }}"  # extra }
    intent = _make_simple_intent(custom_fv_schemes=bad_schemes)
    errors = validator.validate(intent)
    assert any("custom_fv_schemes" in str(e) for e in errors), f"Extra brace not caught: {errors}"


def test_balanced_schemes_pass(validator):
    good_schemes = """
ddtSchemes { default steadyState; }
gradSchemes { default Gauss linear; }
divSchemes { default none; div(phi,U) Gauss limitedLinear 1; }
laplacianSchemes { default Gauss linear corrected; }
"""
    intent = _make_simple_intent(custom_fv_schemes=good_schemes)
    errors = validator.validate(intent)
    # Only check no brace errors (relaxation factors not present)
    brace_errors = [e for e in errors if "brace" in str(e).lower() or "custom_fv_schemes" in str(e)]
    assert brace_errors == [], f"Valid schemes reported brace errors: {brace_errors}"


# ─── Patch name existence check ───────────────────────────────────────────────

def test_invented_patch_name_is_caught(validator):
    from mesh.mesh_io import MeshPatch
    known = [
        MeshPatch(name="inlet", type="patch", n_faces=100, start_face=0),
        MeshPatch(name="outlet", type="patch", n_faces=50, start_face=100),
        MeshPatch(name="walls", type="wall", n_faces=200, start_face=150),
    ]
    intent = _make_simple_intent(
        patches=[
            PatchIntent(
                name="fake_patch", role="inlet",
                bc_overrides={"p": {"type": "totalPressure", "p0": "uniform 400000"}}
            ),
            PatchIntent(name="outlet", role="outlet"),
            PatchIntent(name="walls", role="wall"),
        ]
    )
    errors = validator.validate(intent, known_patches=known)
    assert any("fake_patch" in str(e) for e in errors), f"Invented patch not caught: {errors}"


def test_valid_patch_names_pass(validator):
    from mesh.mesh_io import MeshPatch
    known = [
        MeshPatch(name="inlet", type="patch", n_faces=100, start_face=0),
        MeshPatch(name="outlet", type="patch", n_faces=50, start_face=100),
        MeshPatch(name="walls", type="wall", n_faces=200, start_face=150),
    ]
    intent = _make_simple_intent(
        patches=[
            PatchIntent(name="inlet", role="inlet",
                        bc_overrides={"p": {"type": "totalPressure", "p0": "uniform 400000"}}),
            PatchIntent(name="outlet", role="outlet"),
            PatchIntent(name="walls", role="wall"),
        ]
    )
    errors = validator.validate(intent, known_patches=known)
    assert errors == [], f"Valid patch names reported errors: {errors}"
