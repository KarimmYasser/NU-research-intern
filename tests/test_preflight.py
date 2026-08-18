"""Tests for the preflight gate: static audit (Level A), checkMesh metrics
parser (Level B), and the bounded dry-run residual predictor (Level C).

Level A is pure file inspection; B and C are pure parsers/logic here (the
Docker-touching orchestration in runner.py is exercised in test_graph via the
node-level stubs). No Docker needed.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ir.intent import CaseIntent, PatchIntent, intent_to_spec
from preflight import (
    audit_case,
    parse_checkmesh_metrics,
    parse_residuals,
    predict_from_residuals,
)
from preflight.metrics import evaluate_mesh_metrics
from preflight.ofdict import boundary_fields
from rendering.case import CaseFiles


def _box_case(tmp_path: Path) -> Path:
    intent = CaseIntent(
        regime="laminar", nu=1e-5, geometry="box",
        box_size=(0.1, 0.1, 0.1), cells=(20, 20, 1),
        patches=[
            PatchIntent(name="in", role="inlet", velocity=(1, 0, 0)),
            PatchIntent(name="out", role="outlet", pressure=0.0),
            PatchIntent(name="walls", role="wall"),
        ],
    )
    from rendering.renderer import render
    case: CaseFiles = render(intent_to_spec(intent))
    d = tmp_path / "case"
    case.write(d)
    # Fake a mesh boundary so patch-coverage checks have something to hold onto.
    poly = d / "constant" / "polyMesh"
    poly.mkdir(parents=True, exist_ok=True)
    (poly / "boundary").write_text(
        "FoamFile\n{\n    version 2.0;\n}\n\n3\n(\n"
        "    in\n    {\n        type patch;\n        nFaces 10;\n        startFace 0;\n    }\n"
        "    out\n    {\n        type patch;\n        nFaces 10;\n        startFace 10;\n    }\n"
        "    walls\n    {\n        type wall;\n        nFaces 40;\n        startFace 20;\n    }\n)\n"
    )
    return d


# --- Level A: static audit -------------------------------------------------


def test_audit_clean_box_case(tmp_path):
    d = _box_case(tmp_path)
    findings = audit_case(d)
    fails = [f for f in findings if f.level == "fail"]
    assert not fails, "\n".join(f.message for f in fails)
    codes = {f.code for f in findings}
    assert "solver_entry" in codes and "ddt_default" in codes


def test_audit_flags_nonpositive_internal_fields(tmp_path):
    d = _box_case(tmp_path)
    (d / "0" / "T").write_text(
        "FoamFile\n{\n    version 2.0;\n    class volScalarField;\n    object T;\n}\n"
        "dimensions      [0 0 0 1 0 0 0];\n"
        "internalField   uniform 0;\n"
        "boundaryField\n{\n"
        "    in\n    {\n        type fixedValue;\n        value uniform 300;\n    }\n"
        "    out\n    {\n        type zeroGradient;\n    }\n"
        "    walls\n    {\n        type zeroGradient;\n    }\n"
        "}\n"
    )
    findings = audit_case(d)
    assert any(f.code == "nonpositive_T" and f.level == "fail" for f in findings)


def test_audit_flags_missing_rho_relaxation_for_compressible(tmp_path):
    """The RHVT first-solve NaN root cause: compressible transient fluid needs a
    rho field relaxation. A compressible-looking case without it must fail."""
    d = _box_case(tmp_path)
    # Make the case look compressible. v13 carries the thermo dict inside
    # constant/physicalProperties (thermoType block), not the legacy
    # thermophysicalProperties name.
    (d / "constant" / "physicalProperties").write_text(
        "FoamFile\n{\n    version 2.0;\n    class dictionary;\n}\n"
        "thermoType\n{\n    type heRhoThermo;\n    mixture pureMixture;\n    "
        "transport const;\n    thermo hConst;\n    equationOfState perfectGas;\n}\n"
    )
    (d / "0" / "p").write_text(
        "FoamFile\n{\n    version 2.0;\n    class volScalarField;\n    object p;\n}\n"
        "dimensions      [1 -1 -2 0 0 0 0];\n"
        "internalField   uniform 101325;\n"
        "boundaryField\n{\n"
        "    in\n    {\n        type totalPressure;\n        p0 uniform 101325;\n    }\n"
        "    out\n    {\n        type zeroGradient;\n    }\n"
        "    walls\n    {\n        type zeroGradient;\n    }\n"
        "}\n"
    )
    # Override fvSolution to drop the rho relaxation and energy solver entry.
    sol = d / "system" / "fvSolution"
    sol.write_text(
        "FoamFile\n{\n    version 2.0;\n    class dictionary;\n    object fvSolution;\n}\n"
        "solvers\n{\n"
        "    p\n    {\n        solver GAMG;\n    }\n"
        "    U\n    {\n        solver smoothSolver;\n    }\n"
        "}\n"
        "PIMPLE\n{\n    nOuterCorrectors 1;\n}\n"
        "relaxationFactors\n{\n    fields\n    {\n        p 0.3;\n    }\n"
        "    equations\n    {\n        \".*\" 1;\n    }\n}\n"
    )
    findings = audit_case(d)
    assert any(f.code == "missing_rho_relaxation" and f.level == "fail" for f in findings)
    assert any(f.code == "missing_energy_solver" and f.level == "fail" for f in findings)


def test_audit_flags_incomplete_solver_entries(tmp_path):
    """A GAMG solver entry without a `smoother` key is the live
    'keyword smoother is undefined in dictionary solvers/h' DICTIONARY_SYNTAX
    failure — must be caught at Level A, not in the Docker ladder."""
    d = _box_case(tmp_path)
    sol = d / "system" / "fvSolution"
    text = sol.read_text()
    text = text.replace(
        "        solver          smoothSolver;\n"
        "        smoother        GaussSeidel;\n",
        "        solver          GAMG;\n",
    )
    sol.write_text(text)
    findings = audit_case(d)
    assert any(f.code == "incomplete_solver_entries" and f.level == "fail" for f in findings)


def test_audit_flags_incomplete_solver_entries_one_line(tmp_path):
    """The LLM compresses fvSolution onto one line; the mid-line parser must
    still find solver entries missing required keys."""
    d = _box_case(tmp_path)
    sol = d / "system" / "fvSolution"
    sol.write_text(
        "FoamFile\n{\n    version 2.0;\n    class dictionary;\n    object fvSolution;\n}\n"
        "solvers { p { solver GAMG; tolerance 1e-06; relTol 0.1; } "
        "U { solver smoothSolver; smoother GaussSeidel; tolerance 1e-05; } "
        "h { solver GAMG; } }\n"
        "PIMPLE { nCorrectors 2; }\n"
    )
    findings = audit_case(d)
    assert any(f.code == "incomplete_solver_entries" and f.level == "fail" for f in findings)


def test_audit_flags_patch_not_in_field(tmp_path):
    """A 0/ field missing a mesh boundary patch -> patchNotInMesh read error."""
    d = _box_case(tmp_path)
    u = (d / "0" / "U").read_text()
    # Remove the entire 'walls' patch entry from U's boundaryField.
    import re
    dropped = re.sub(r"\n    walls\n    \{.*?\n    \}\n", "\n", u, flags=re.DOTALL)
    assert "walls" not in boundary_fields(dropped), "sanity: walls must be removed"
    (d / "0" / "U").write_text(dropped)
    findings = audit_case(d)
    assert any(f.code.startswith("patch_not_in_field") and f.level == "fail" for f in findings)


def test_audit_tolerates_malformed_files(tmp_path):
    d = _box_case(tmp_path)
    (d / "system" / "fvSolution").write_text("this is not { valid;")
    (d / "0" / "U").write_text("")  # empty field file must not crash the audit
    findings = audit_case(d)  # must not raise
    assert findings


def test_audit_flags_loop_block_ddt_mismatch(tmp_path):
    """A transient (Euler) case whose fvSolution only has a SIMPLE block must
    FAIL Level A: the v13 module reads PIMPLE for transient and dies with
    'keyword PIMPLE is undefined' (seen live after an LLM fvSolution override)."""
    d = _box_case(tmp_path)
    sol = d / "system" / "fvSolution"
    text = sol.read_text()
    import re
    text = re.sub(r"\nPIMPLE\n\{.*?\n\}\n", "\n", text, flags=re.DOTALL)
    text = text.replace(
        "// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //\n\n",
        "// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //\n\n"
        "SIMPLE\n{\n    nCorrectors 1;\n}\n",
    )
    sol.write_text(text)
    findings = audit_case(d)
    assert any(f.code == "loop_block_mismatch" and f.level == "fail" for f in findings)


# --- Level B: checkMesh metrics -------------------------------------------


def test_metrics_parse_good_mesh():
    out = """
    Mesh stats
        points:           12345
        cells:            3548475
    Mesh quality
        Minimum face area = 1.4e-10. Maximum face area = 9.3e-08.
        Min volume = 5.7e-14. Max volume = 1.5e-11. Total volume = 1.5e-05.
        Mesh non-orthogonality Max: 41.26 average: 4.97
        Max skewness = 2.08 OK.
        Max aspect ratio = 42.7 OK.
        Mesh OK.
"""
    m = parse_checkmesh_metrics(out)
    assert m.cells == 3548475
    assert m.non_orthogonality_max == 41.26
    assert m.non_orthogonality_avg == 4.97
    assert m.max_skewness == 2.08
    assert m.max_aspect_ratio == 42.7
    assert m.ok
    rows = evaluate_mesh_metrics(m)
    assert all(level == "pass" for _, level, _ in rows)


def test_metrics_parse_bad_mesh():
    out = """
        cells:            3548475
        Min volume = -1.0e-12.
        Mesh non-orthogonality Max: 151.0 average: 40.0
        Max skewness = 9.0
        Failed 2 mesh checks.
"""
    m = parse_checkmesh_metrics(out)
    assert not m.ok and m.failed_checks == 2
    rows = {code: level for code, level, _ in evaluate_mesh_metrics(m)}
    assert rows["nonpositive_cell_volume"] == "fail"
    assert rows["non_ortho_fail"] == "fail"
    assert rows["skew_fail"] == "fail"
    assert rows["checkmesh_failed"] == "fail"


def test_metrics_parse_empty():
    m = parse_checkmesh_metrics("")
    assert m.cells is None and m.ok is False
    assert evaluate_mesh_metrics(m)  # non-crashing


# --- Level C: residual-slope predictor -------------------------------------


_DIVERGING_LOG = """Time = 0.0002

DILUPBiCGStab:  Solving for Ux, Initial residual = 0.0092, Final residual = 1e-5, No Iterations 1
GAMG:  Solving for p, Initial residual = 0.99, Final residual = 0.1, No Iterations 50

Time = 0.0004

DILUPBiCGStab:  Solving for Ux, Initial residual = 0.08, Final residual = 1e-4, No Iterations 2
GAMG:  Solving for p, Initial residual = 0.996, Final residual = 0.3, No Iterations 50

Time = 0.0006

DILUPBiCGStab:  Solving for Ux, Initial residual = 0.2, Final residual = 0.05, No Iterations 3
GAMG:  Solving for p, Initial residual = 0.998, Final residual = 0.6, No Iterations 50
"""

_CONVERGING_LOG = """Time = 0.0002

DILUPBiCGStab:  Solving for Ux, Initial residual = 0.5, Final residual = 1e-5, No Iterations 1
GAMG:  Solving for p, Initial residual = 0.99, Final residual = 0.1, No Iterations 50

Time = 0.0004

DILUPBiCGStab:  Solving for Ux, Initial residual = 0.2, Final residual = 1e-6, No Iterations 1
GAMG:  Solving for p, Initial residual = 0.4, Final residual = 0.05, No Iterations 50

Time = 0.0006

DILUPBiCGStab:  Solving for Ux, Initial residual = 0.05, Final residual = 1e-6, No Iterations 1
GAMG:  Solving for p, Initial residual = 0.1, Final residual = 0.01, No Iterations 50
"""


def test_predictor_flags_divergence():
    steps, n = parse_residuals(_DIVERGING_LOG)
    assert n == 3
    pred = predict_from_residuals(steps, n)
    assert pred.prediction == "FAIL"
    assert any("RISING" in t for t in pred.trend_line)


def test_predictor_passes_decaying_residuals():
    steps, n = parse_residuals(_CONVERGING_LOG)
    pred = predict_from_residuals(steps, n)
    assert pred.prediction == "PASS"


def test_predictor_flags_nan_immediately():
    log = _CONVERGING_LOG.replace("Initial residual = 0.2", "Initial residual = nan")
    steps, n = parse_residuals(log)
    pred = predict_from_residuals(steps, n)
    assert pred.prediction == "FAIL"
    assert "non-finite" in pred.message


def test_predictor_no_steps_is_fail():
    pred = predict_from_residuals([], 0)
    assert pred.prediction == "FAIL"


def test_predictor_stalled_flat_is_warn_not_pass():
    """A field pinned near its start value (slope ~0) is NOT converging: WARN,
    never PASS — even though it contains no NaN."""
    log = """Time = 0.0002

DILUPBiCGStab:  Solving for Ux, Initial residual = 0.99, Final residual = 0.9, No Iterations 50

Time = 0.0004

DILUPBiCGStab:  Solving for Ux, Initial residual = 0.985, Final residual = 0.9, No Iterations 50

Time = 0.0006

DILUPBiCGStab:  Solving for Ux, Initial residual = 0.988, Final residual = 0.9, No Iterations 50
"""
    steps, n = parse_residuals(log)
    pred = predict_from_residuals(steps, n)
    assert pred.prediction == "WARN"
    assert any("flat/stalled" in t for t in pred.trend_line)


def test_predictor_single_noisy_uptick_still_decays():
    """One noisy uptick on an otherwise downward path must not flip to FAIL —
    the log-slope trend sees the net direction, not the last comparison."""
    log = """Time = 0.0002

DILUPBiCGStab:  Solving for Ux, Initial residual = 0.5, Final residual = 1e-5, No Iterations 1

Time = 0.0004

DILUPBiCGStab:  Solving for Ux, Initial residual = 0.9, Final residual = 1e-4, No Iterations 2

Time = 0.0006

DILUPBiCGStab:  Solving for Ux, Initial residual = 0.1, Final residual = 1e-5, No Iterations 1

Time = 0.0008

DILUPBiCGStab:  Solving for Ux, Initial residual = 0.01, Final residual = 1e-6, No Iterations 1
"""
    steps, n = parse_residuals(log)
    pred = predict_from_residuals(steps, n)
    assert pred.prediction == "PASS"


def test_predictor_converged_floor_is_pass_even_if_flat():
    """Residual already at machine floor: flat is fine — it has converged."""
    log = """Time = 0.0002

DILUPBiCGStab:  Solving for Ux, Initial residual = 1e-9, Final residual = 1e-9, No Iterations 1

Time = 0.0004

DILUPBiCGStab:  Solving for Ux, Initial residual = 1e-10, Final residual = 1e-10, No Iterations 1

Time = 0.0006

DILUPBiCGStab:  Solving for Ux, Initial residual = 1e-10, Final residual = 1e-10, No Iterations 1
"""
    steps, n = parse_residuals(log)
    pred = predict_from_residuals(steps, n)
    assert pred.prediction == "PASS"


def test_predictor_small_rising_residual_is_warn_not_fail():
    """A pressure-coupling transient (p residual drifting 0.03 -> 0.2 while
    staying small) must NOT be FAILed when a sibling field is decaying: the
    lid-driven cavity live run showed the full solver converging with residuals
    exactly like this. Only a rise to RISING_MAGNITUDE, or a rise while NO
    field decays, is real divergence."""
    log = """Time = 0.0002

smoothSolver:  Solving for Ux, Initial residual = 1.0, Final residual = 0.6, No Iterations 1
GAMG:  Solving for p, Initial residual = 0.0306, Final residual = 0.009, No Iterations 1

Time = 0.0004

smoothSolver:  Solving for Ux, Initial residual = 0.6, Final residual = 0.3, No Iterations 1
GAMG:  Solving for p, Initial residual = 0.184, Final residual = 0.01, No Iterations 1

Time = 0.0006

smoothSolver:  Solving for Ux, Initial residual = 0.36, Final residual = 0.18, No Iterations 1
GAMG:  Solving for p, Initial residual = 0.121, Final residual = 0.009, No Iterations 1

Time = 0.0008

smoothSolver:  Solving for Ux, Initial residual = 0.27, Final residual = 0.12, No Iterations 1
GAMG:  Solving for p, Initial residual = 0.134, Final residual = 0.008, No Iterations 1

Time = 0.0010

smoothSolver:  Solving for Ux, Initial residual = 0.18, Final residual = 0.08, No Iterations 1
GAMG:  Solving for p, Initial residual = 0.141, Final residual = 0.008, No Iterations 1
"""
    steps, n = parse_residuals(log)
    pred = predict_from_residuals(steps, n)
    assert pred.prediction == "WARN"
    assert any("rising but small" in t for t in pred.trend_line)


def test_predictor_rising_large_residual_is_fail():
    """A rise to a substantial magnitude (peak >= RISING_MAGNITUDE) is real
    divergence — the boundary between the small coupling transient above and a
    solver that is actually going unstable."""
    log = """Time = 0.0002

GAMG:  Solving for p, Initial residual = 0.4, Final residual = 0.1, No Iterations 50

Time = 0.0004

GAMG:  Solving for p, Initial residual = 0.55, Final residual = 0.2, No Iterations 50

Time = 0.0006

GAMG:  Solving for p, Initial residual = 0.72, Final residual = 0.3, No Iterations 50

Time = 0.0008

GAMG:  Solving for p, Initial residual = 0.89, Final residual = 0.4, No Iterations 50

Time = 0.0010

GAMG:  Solving for p, Initial residual = 0.99, Final residual = 0.5, No Iterations 50
"""
    steps, n = parse_residuals(log)
    pred = predict_from_residuals(steps, n)
    assert pred.prediction == "FAIL"
    assert any("RISING" in t for t in pred.trend_line)


def test_audit_box_case_without_polymesh_uses_blockmeshdict(tmp_path):
    """When a box case is freshly rendered, constant/polyMesh/boundary does not
    exist yet. The audit must fall back to parsing system/blockMeshDict boundary."""
    intent = CaseIntent(
        regime="laminar", nu=1e-5, geometry="box",
        box_size=(0.1, 0.1, 0.1), cells=(20, 20, 1),
        patches=[
            PatchIntent(name="in", role="inlet", velocity=(1, 0, 0)),
            PatchIntent(name="out", role="outlet", pressure=0.0),
            PatchIntent(name="walls", role="wall"),
        ],
    )
    from rendering.renderer import render
    case = render(intent_to_spec(intent))
    d = tmp_path / "case"
    case.write(d)
    # Notice: no constant/polyMesh/boundary created here.
    assert not (d / "constant" / "polyMesh" / "boundary").exists()
    assert (d / "system" / "blockMeshDict").exists()

    findings = audit_case(d)
    fails = [f for f in findings if f.level == "fail"]
    assert not fails, "\n".join(f.message for f in fails)
    # Ensure patch coverage passed for all fields using blockMeshDict
    patch_passes = [f for f in findings if f.code.startswith("patch_cover_")]
    assert len(patch_passes) > 0


if __name__ == "__main__":
    import inspect
    import tempfile

    n = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            params = inspect.signature(fn).parameters
            with tempfile.TemporaryDirectory() as d:
                args = [Path(d)] if "tmp_path" in params else []
                fn(*args)
            print(f"ok  {name}")
            n += 1
    print(f"\n{n} tests passed")


def test_regex_patch_entries_cover_mesh_patches(tmp_path):
    """OpenFOAM allows quoted regex boundaryField entries ("inlet_.*").

    The audit used a plain set difference, so a field using a regex was reported
    as missing every patch it actually covered -- 6 false FAILs on the RHVT case.
    """
    from preflight.audit import audit_case

    case = tmp_path / "case"
    (case / "0").mkdir(parents=True)
    (case / "constant" / "polyMesh").mkdir(parents=True)
    (case / "system").mkdir()
    (case / "constant" / "polyMesh" / "boundary").write_text(
        "3\n(\n"
        "    inlet_1 { type patch; nFaces 10; startFace 0; }\n"
        "    inlet_2 { type patch; nFaces 10; startFace 10; }\n"
        "    walls   { type wall;  nFaces 10; startFace 20; }\n"
        ")\n",
        encoding="utf-8",
    )
    # p covers the inlets by regex; U spells them out. Both are valid and equivalent.
    (case / "0" / "p").write_text(
        'boundaryField\n{\n    "inlet_.*" { type zeroGradient; }\n'
        "    walls { type zeroGradient; }\n}\n",
        encoding="utf-8",
    )
    (case / "0" / "U").write_text(
        "boundaryField\n{\n"
        "    inlet_1 { type fixedValue; value uniform (1 0 0); }\n"
        "    inlet_2 { type fixedValue; value uniform (1 0 0); }\n"
        "    walls { type noSlip; }\n}\n",
        encoding="utf-8",
    )

    findings = audit_case(case)
    assert not [f for f in findings if f.code == "patch_not_in_field"], (
        "regex patch entry wrongly reported as missing coverage"
    )
    assert not [f for f in findings if f.code == "patch_set_mismatch"], (
        "regex and explicit spellings of the same patch set flagged as inconsistent"
    )


def test_first_solve_nan_is_not_reported_as_a_dictionary_error(tmp_path):
    """An immediate NaN can be killed before any `Time = ` marker is logged.

    predict_from_residuals then sees 0 steps and blames a dictionary error,
    sending the agent to hunt through valid files. Observed on the RHVT case.
    """
    from preflight.dry_run import bounded_run_predict

    log = ("Starting time loop\n"
           "GAMG:  Solving for p, Initial residual = 0.998531, "
           "Final residual = nan, No Iterations 50\n"
           "foamRun diverged to NaN - aborted on first non-finite residual\n")

    class _Result:
        ok = False

    result = _Result()
    result.log = log
    pred = bounded_run_predict(tmp_path, lambda _p: result, dry_run_steps=5)

    assert pred.prediction == "FAIL"
    assert "diverged" in pred.message.lower()
    assert "dictionary" not in pred.message.lower().split("not a dictionary")[0]
    assert "p" in pred.message


def _min_case(tmp_path, ddt: str, loop: str):
    case = tmp_path / "c"
    (case / "system").mkdir(parents=True)
    (case / "0").mkdir()
    (case / "system" / "fvSchemes").write_text(
        f"ddtSchemes\n{{\n    default         {ddt};\n}}\n", encoding="utf-8")
    (case / "system" / "fvSolution").write_text(
        "solvers\n{\n    p { solver GAMG; tolerance 1e-6; }\n}\n"
        f"{loop}\n{{\n    nCorrectors 2;\n}}\n"
        "relaxationFactors\n{\n    fields { p 0.3; }\n}\n", encoding="utf-8")
    return case


def test_steady_ddt_with_pimple_block_is_not_a_failure(tmp_path):
    """Verified on the RHVT case: steadyState ddt + PIMPLE ran 10 steps with all
    residuals decaying on the v13 `fluid` module. The audit used to FAIL this,
    condemning the only working configuration."""
    from preflight.audit import audit_case

    findings = audit_case(_min_case(tmp_path, "steadyState", "PIMPLE"))
    assert not [f for f in findings if f.level == "fail" and "loop_block" in f.code], (
        "steady + PIMPLE wrongly reported as a fatal mismatch"
    )


def test_transient_ddt_with_simple_only_still_fails(tmp_path):
    """The evidenced direction must keep failing: a transient run whose fvSolution
    only has SIMPLE dies at startup looking up PIMPLE."""
    from preflight.audit import audit_case

    findings = audit_case(_min_case(tmp_path, "Euler", "SIMPLE"))
    assert [f for f in findings if f.code == "loop_block_mismatch" and f.level == "fail"]
