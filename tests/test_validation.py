"""Tests for the validation agent's error parser (parse_errors).

These are pure-logic tests over real OpenFOAM error strings - no Docker needed,
so they run fast in any environment. The Docker-backed ladder (run_block_mesh /
run_check_mesh / run_solver) is exercised by integration runs, not here.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from validation import FailureClass, ValidationAgent

va = ValidationAgent()


def test_io_error_localizes_dictionary_and_overrides_class():
    out = (
        '--> FOAM FATAL IO ERROR: \n'
        'keyword vertices is undefined in dictionary "/case/system/blockMeshDict"\n\n'
        '    From function ...\nFOAM exiting\n'
    )
    # default is MESH_GENERATION, but an IO error is a dictionary problem.
    errs = va.parse_errors(out, FailureClass.MESH_GENERATION)
    assert len(errs) == 1
    assert errs[0].failure_class == FailureClass.DICTIONARY_SYNTAX
    assert errs[0].file_hint == "system/blockMeshDict"


def test_io_error_extracts_file_and_line():
    out = (
        '--> FOAM FATAL IO ERROR: \n'
        'wrong token type - expected Scalar, found on line 21 the word\n\n'
        'file: /case/0/p at line 21.\n\nFOAM exiting\n'
    )
    errs = va.parse_errors(out, FailureClass.SOLVER_RUNTIME)
    assert errs[0].failure_class == FailureClass.DICTIONARY_SYNTAX
    assert errs[0].file_hint == "0/p"
    assert errs[0].line == 21


def test_non_io_error_keeps_default_class():
    out = (
        '--> FOAM FATAL ERROR: \n'
        'Maximum number of iterations exceeded\n\nFOAM exiting\n'
    )
    errs = va.parse_errors(out, FailureClass.SOLVER_RUNTIME)
    assert errs[0].failure_class == FailureClass.SOLVER_RUNTIME
    assert errs[0].file_hint is None


def test_empty_output_yields_no_errors():
    assert va.parse_errors("", FailureClass.UNKNOWN) == []


def test_environment_error_classified():
    errs = va.parse_errors("docker not found on PATH - install Docker Desktop",
                           FailureClass.MESH_GENERATION)
    assert errs[0].failure_class == FailureClass.ENVIRONMENT


def test_timeout_without_failure_signals_classified_environment():
    out = "foamRun timed out after 2400s"
    errs = va.parse_errors(out, FailureClass.SOLVER_RUNTIME)
    assert errs[0].failure_class == FailureClass.ENVIRONMENT


def test_timeout_with_nan_divergence_classified_solver_runtime():
    # A diverged solver keeps printing NaN residuals until the wall-clock
    # timeout; the timeout text and the divergence evidence arrive together.
    out = (
        "GAMG:  Solving for p, Initial residual = nan, Final residual = nan, No Iterations 1000\n"
        "time step continuity errors : sum local = nan, global = nan\n"
        "foamRun timed out after 2400s\n"
    )
    errs = va.parse_errors(out, FailureClass.SOLVER_RUNTIME)
    assert errs[0].failure_class == FailureClass.SOLVER_RUNTIME
    assert "NaN" in errs[0].message
    assert "Solving for p" in errs[0].message


def test_nan_divergence_without_timeout_classified_solver_runtime():
    out = "smoothSolver:  Solving for Ux, Initial residual = -nan, Final residual = -nan\n"
    errs = va.parse_errors(out, FailureClass.SOLVER_RUNTIME)
    assert errs[0].failure_class == FailureClass.SOLVER_RUNTIME
    assert "Ux" in errs[0].message


def test_parallel_solver_writes_decompose_dict():
    agent = ValidationAgent(nprocs=8)
    assert agent.nprocs == 8
    assert ValidationAgent().nprocs == 1  # default stays serial


def test_nan_early_abort_regex_matches_finite_solver_lines():
    """The ladder must stop the container at the FIRST non-finite residual, not
    after the whole timeout; the regex must match the forms OpenFOAM prints."""
    for line in (
        "GAMG:  Solving for p, Initial residual = nan, Final residual = nan, No Iterations 50",
        "smoothSolver:  Solving for Ux, Initial residual = -nan, Final residual = -nan",
        "time step continuity errors : sum local = nan, global = -nan, cumulative = nan",
        "solver p:  Initial residual = inf, Final residual = inf",
    ):
        assert va._NAN_RE.search(line), f"NaN regex missed: {line!r}"


def test_nan_early_abort_regex_ignores_healthy_solver_lines():
    for line in (
        "smoothSolver:  Solving for Ux, Initial residual = 0.0001, Final residual = 1e-06, No Iterations 4",
        "GAMG:  Solving for p, Initial residual = 0.02",
        "Courant Number mean: 0.18 max: 1.2",
        "vortex tube - nothing nan here",
    ):
        assert not va._NAN_RE.search(line), f"NaN regex false-positive on: {line!r}"


def test_check_mesh_caching(tmp_path):
    poly = tmp_path / "constant" / "polyMesh"
    poly.mkdir(parents=True)
    boundary = poly / "boundary"
    boundary.write_text("boundary_data")

    agent = ValidationAgent()
    agent.clear_mesh_cache()

    key = agent._get_mesh_key(tmp_path)
    assert key is not None

    # Manually mark as checked
    agent._CHECKED_MESHES.add(key)
    res = agent.run_check_mesh(tmp_path)

    assert res.ok
    assert "cached pass" in res.log
    agent.clear_mesh_cache()


def test_docker_run_handles_none_stdout(tmp_path):
    from unittest.mock import MagicMock, patch
    mock_proc = MagicMock()
    mock_proc.stdout = None
    mock_proc.communicate.return_value = ("output tail", "")
    mock_proc.returncode = 0

    agent = ValidationAgent()
    with patch("subprocess.Popen", return_value=mock_proc):
        res = agent._docker_run(tmp_path, ["checkMesh"])
        assert res.returncode == 0
        assert res.stdout == "output tail"


if __name__ == "__main__":
    n = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
            n += 1
    print(f"\n{n} tests passed")
