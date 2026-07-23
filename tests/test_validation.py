"""Tests for the validation agent's error parser (parse_errors).

These are pure-logic tests over real OpenFOAM error strings - no Docker needed,
so they run fast in any environment. The Docker-backed ladder (run_block_mesh /
run_check_mesh / run_solver) is exercised by integration runs, not here.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agents.validation_agent import FailureClass, ValidationAgent

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


if __name__ == "__main__":
    n = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
            n += 1
    print(f"\n{n} tests passed")
