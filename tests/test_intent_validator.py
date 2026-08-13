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
    return CaseIntent.model_validate(data)


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


def test_asymmetric_pcg_solver_is_caught(validator):
    fv_solution = """
SIMPLE { nNonOrthogonalCorrectors 0; }
solvers
{
    p { solver GAMG; tolerance 1e-6; relTol 0.01; }
    h { solver PCG; tolerance 1e-6; relTol 0.01; }
}
"""
    intent = _make_simple_intent(flow_type="compressible", custom_fv_solution=fv_solution)
    errors = validator.validate(intent)
    assert any("PCG" in str(e) and "asymmetric" in str(e) for e in errors), f"Errors: {errors}"


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

def test_too_many_box_patches_caught(validator):
    """A box with more wall patches than remaining side faces must be flagged
    (and resolved by listing faces explicitly)."""
    intent = CaseIntent(
        geometry="box", box_size=(1.0, 1.0, 1.0),
        patches=[PatchIntent(name=f"wall{i}", role="wall") for i in range(7)],
    )
    errors = validator.validate(intent)
    assert any("could not be assigned" in e.message for e in errors), f"{errors}"


def test_duplicate_explicit_faces_caught(validator):
    intent = CaseIntent(
        geometry="box", box_size=(1.0, 1.0, 1.0),
        patches=[
            PatchIntent(name="a", role="wall", faces=["xMin"]),
            PatchIntent(name="b", role="wall", faces=["xMin"]),
            PatchIntent(name="c", role="wall", faces=["xMax", "yMin", "yMax", "zMin", "zMax"]),
        ],
    )
    errors = validator.validate(intent)
    assert any("multiple patches" in e.message for e in errors), f"{errors}"


def test_faces_rejected_for_external_mesh(validator):
    intent = _make_simple_intent(
        patches=[
            PatchIntent(name="inlet", role="inlet", faces=["xMin"]),
            PatchIntent(name="outlet", role="outlet"),
            PatchIntent(name="walls", role="wall"),
        ]
    )
    errors = validator.validate(intent)
    assert any("faces" in e.message and "external_mesh" in e.message for e in errors), f"{errors}"


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


# ─── Fluid case: mandatory PIMPLE/SIMPLE block ────────────────────────────────

def test_fluid_custom_fv_solution_requires_solver_control_block(validator):
    """Compressible `fluid` fvSolution must carry PIMPLE (transient) / SIMPLE
    (steady) — the module reads it at startup; catch before Docker."""
    fv = "solvers { p { solver GAMG; } \"(U|h|e|k|epsilon|R)\" { solver PBiCGStab; } } relaxationFactors { equations { U 0.5; } }"
    intent = _make_simple_intent(case_type="fluid", solver_mode="transient",
                                 custom_fv_solution=fv)
    errors = validator.validate(intent)
    assert any("PIMPLE" in str(e) for e in errors), f"Missing PIMPLE not caught: {errors}"


def test_fluid_steady_fv_solution_requires_simple_block(validator):
    fv = "solvers { p { solver GAMG; } \"(U|h|e|k|epsilon|R)\" { solver PBiCGStab; } } PIMPLE { nCorrectors 2; }"
    intent = _make_simple_intent(case_type="fluid", solver_mode="steadyState",
                                 custom_fv_solution=fv)
    errors = validator.validate(intent)
    assert any("SIMPLE" in str(e) for e in errors), f"Missing SIMPLE not caught: {errors}"


def test_fluid_custom_fv_solution_with_block_passes(validator):
    fv = ('solvers { p { solver GAMG; } "(U|h|e|k|epsilon|R)" { solver PBiCGStab; } } '
          'PIMPLE { nCorrectors 2; } relaxationFactors { equations { U 0.5; } }')
    intent = _make_simple_intent(case_type="fluid", solver_mode="transient",
                                 custom_fv_solution=fv)
    errors = validator.validate(intent)
    assert errors == [], f"Valid fluid fvSolution reported errors: {errors}"


def test_incompressible_custom_fv_solution_not_forced_to_pimple(validator):
    """Only compressible `fluid` demands the solver-control block."""
    fv = "solvers { p { solver PCG; } } PIMPLE { nCorrectors 2; }"
    intent = _make_simple_intent(case_type="incompressibleFluid",
                                 custom_fv_solution=fv)
    errors = validator.validate(intent)
    assert not any("PIMPLE" in str(e) for e in errors)


# ─── Fluid case: energy-field solver entry ────────────────────────────────────

def test_fluid_custom_fv_solution_requires_energy_solver(validator):
    """The `fluid` module reads solvers/h at startup; a hand-written `solvers`
    block listing only p/U/k/epsilon (typical of LLM output) must be rejected
    here, not on the Docker ladder."""
    fv = "solvers { p { solver GAMG; } U { solver PBiCGStab; } k { solver PBiCGStab; } epsilon { solver PBiCGStab; } } PIMPLE { nCorrectors 2; }"
    intent = _make_simple_intent(case_type="fluid", solver_mode="transient",
                                 custom_fv_solution=fv)
    errors = validator.validate(intent)
    assert any("energy field" in str(e) or "solvers/h" in str(e) for e in errors), \
        f"Missing energy solver not caught: {errors}"
    # regex entry covering h/e is accepted
    fv2 = 'solvers { p { solver GAMG; } "(U|h|e|k|epsilon|R)" { solver PBiCGStab; } } PIMPLE { nCorrectors 2; }'
    intent2 = _make_simple_intent(case_type="fluid", solver_mode="transient",
                                  custom_fv_solution=fv2)
    errs2 = validator.validate(intent2)
    assert not any("energy field" in str(e) for e in errs2), f"Regex h/e entry rejected: {errs2}"


def test_compressible_flow_type_maps_to_fluid_for_solver_control(validator):
    """flow_type=compressible with default case_type must resolve to the `fluid`
    family so the PIMPLE/energy-solver checks fire (mirrors _resolve_case_type)."""
    fv = "solvers { p { solver GAMG; } U { solver PBiCGStab; } k { solver PBiCGStab; } epsilon { solver PBiCGStab; } }"
    intent = _make_simple_intent(solver_mode="transient", custom_fv_solution=fv)
    errors = validator.validate(intent)
    assert any("PIMPLE" in str(e) for e in errors), f"Resolved case_type=fluid not enforced: {errors}"

    # With the solver-control block present but energy solver still missing, the
    # energy check must fire for the same resolved-to-fluid intent.
    fv2 = "solvers { p { solver GAMG; } U { solver PBiCGStab; } k { solver PBiCGStab; } epsilon { solver PBiCGStab; } } PIMPLE { nCorrectors 2; }"
    intent2 = _make_simple_intent(solver_mode="transient", custom_fv_solution=fv2)
    errs2 = validator.validate(intent2)
    assert any("energy field" in str(e) for e in errs2), f"Energy check not fired: {errs2}"


# ─── extra_files (arbitrary additional case files) ─────────────────────────────

def test_extra_files_any_case_relative_path_accepted(validator):
    """The agent may emit any additional dictionary at any depth under the case
    dir — system/fvConstraints, constant/thermo/... — exactly the optional
    files OpenFOAM solvers demand that the standard renderer doesn't cover."""
    intent = _make_simple_intent(extra_files={
        "system/fvConstraints": "limitVelocity { type limitVelocity; }",
        "constant/thermo/thermoDict": "// custom\n",
        "0/U": "override",  # whole-file first-render override is legitimate
    })
    assert validator.validate(intent) == [], validator.validate(intent)


def test_extra_files_path_traversal_rejected(validator):
    """extra_files keys must stay inside the case directory: absolute paths and
    ../ traversal are refused so the agent can't write outside the case."""
    for bad in ("../etc/overflow", "/etc/hosts", "a/../../top"):
        intent = _make_simple_intent(extra_files={bad: "x"})
        errors = validator.validate(intent)
        matches = [e for e in errors if "extra_files" in e.field]
        assert any("inside the case directory" in e.message for e in matches), (bad, errors)


def test_extra_files_empty_key_rejected(validator):
    intent = _make_simple_intent(extra_files={"": "x"})
    errors = validator.validate(intent)
    assert any("extra_files" in e.field for e in errors), errors
