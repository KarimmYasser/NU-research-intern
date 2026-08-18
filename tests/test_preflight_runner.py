"""`preflight/runner.py` — the orchestration and the controlDict patching.

This module was 14% covered, which is how `run_bounded_dry_run` shipped broken.
`_bounded_foamrun` is the riskiest code in the repo by consequence: it *rewrites
the user's controlDict* to bound the run and must put it back byte-for-byte no
matter how the solver ends. Nothing tested that.
"""

from pathlib import Path

import pytest

from preflight import PreflightVerdict
from preflight.runner import _bounded_foamrun, _env_failure, run_preflight_with_agent
from validation.validation_agent import FailureClass, ValidationError, ValidationResult

CONTROL_DICT = """\
FoamFile
{
    format      ascii;
    class       dictionary;
    object      controlDict;
}

solver          fluid;
startTime       0;
stopAt          endTime;
endTime         100;
deltaT          0.002;
writeControl    runTime;
writeInterval   5;
adjustTimeStep  no;
"""


@pytest.fixture
def case(tmp_path):
    c = tmp_path / "c"
    (c / "system").mkdir(parents=True)
    (c / "system" / "controlDict").write_text(CONTROL_DICT, encoding="utf-8")
    return c


class _Agent:
    """Stand-in for ValidationAgent that records what the dict looked like mid-run."""

    def __init__(self, result=None, raises=None):
        self.result = result or ValidationResult(True, "foamRun", [], "Time = 1\nEnd\n")
        self.raises = raises
        self.seen_controldict = None
        self.calls = []

    def run_solver(self, case_dir):
        self.calls.append("run_solver")
        self.seen_controldict = (Path(case_dir) / "system" / "controlDict").read_text()
        if self.raises:
            raise self.raises
        return self.result

    def run_block_mesh(self, case_dir):
        self.calls.append("run_block_mesh")
        return ValidationResult(True, "blockMesh")

    def run_check_mesh(self, case_dir):
        self.calls.append("run_check_mesh")
        return ValidationResult(True, "checkMesh", [], "Mesh OK.\n")


# --------------------------------------------------------------------------
# _bounded_foamrun — controlDict integrity
# --------------------------------------------------------------------------


def test_bounds_endtime_to_start_plus_steps_times_deltat(case):
    agent = _Agent()

    _bounded_foamrun(case, agent, 5)

    # 0 + 5 * 0.002
    assert "endTime         0.01;" in agent.seen_controldict
    assert "deltaT          0.002;" in agent.seen_controldict, "deltaT must not be touched"


def test_original_controldict_is_restored_byte_for_byte(case):
    _bounded_foamrun(case, _Agent(), 5)

    assert (case / "system" / "controlDict").read_text() == CONTROL_DICT


def test_controldict_is_restored_even_when_the_solver_raises(case):
    """A timeout or a Docker crash must not leave the user's case truncated to
    five time steps — that would silently ruin the next real run."""
    agent = _Agent(raises=RuntimeError("docker died"))

    with pytest.raises(RuntimeError):
        _bounded_foamrun(case, agent, 5)

    assert (case / "system" / "controlDict").read_text() == CONTROL_DICT


def test_writes_are_suppressed_with_a_valid_writecontrol(case):
    """v13 rejects `writeControl none`; using it made the solver die before the
    first step, which the predictor then misread as 'no Time steps'."""
    agent = _Agent()

    _bounded_foamrun(case, agent, 5)

    assert "writeControl   timeStep;" in agent.seen_controldict
    assert "none" not in agent.seen_controldict
    interval = int(agent.seen_controldict.split("writeInterval")[1].split(";")[0])
    assert interval > 5, "writeInterval must exceed the dry-run horizon"


def test_start_time_is_respected(case):
    (case / "system" / "controlDict").write_text(
        CONTROL_DICT.replace("startTime       0;", "startTime       3;"), encoding="utf-8")
    agent = _Agent()

    _bounded_foamrun(case, agent, 10)

    assert "endTime         3.02;" in agent.seen_controldict


def test_missing_deltat_falls_back_without_crashing(case):
    (case / "system" / "controlDict").write_text(
        "startTime       0;\nendTime         100;\n", encoding="utf-8")
    agent = _Agent()

    _bounded_foamrun(case, agent, 3)

    assert "endTime         3;" in agent.seen_controldict


def test_missing_controldict_is_reported_and_creates_nothing(case):
    (case / "system" / "controlDict").unlink()
    agent = _Agent()

    with pytest.raises(FileNotFoundError):
        _bounded_foamrun(case, agent, 5)

    assert not (case / "system" / "controlDict").exists(), "invented a controlDict"
    assert agent.calls == [], "ran the solver without bounding it"


# --------------------------------------------------------------------------
# run_preflight_with_agent — level ordering and Docker deferral
# --------------------------------------------------------------------------


def test_level_a_failure_never_touches_docker(case, monkeypatch):
    """Fail-fast is the whole point of Level A: it must be free."""
    from preflight import runner

    monkeypatch.setattr(runner, "audit_case", lambda _c: [
        runner._to_finding("boom", "something is fatally wrong")])
    agent = _Agent()

    result = run_preflight_with_agent(case, agent)

    assert result.verdict is PreflightVerdict.FAIL
    assert agent.calls == [], "Docker was invoked despite a Level A failure"
    assert "not running Docker" in result.message


def test_docker_unavailable_defers_instead_of_failing_the_case(case, monkeypatch):
    """No Docker is an infrastructure problem, not a bad case."""
    from preflight import runner

    monkeypatch.setattr(runner, "audit_case", lambda _c: [])

    class _NoDocker(_Agent):
        def run_block_mesh(self, case_dir):
            return ValidationResult(
                False, "blockMesh",
                [ValidationError(FailureClass.ENVIRONMENT, None, "docker: command not found")])

    result = run_preflight_with_agent(case, _NoDocker())

    assert result.verdict is not PreflightVerdict.FAIL
    assert "unavailable" in result.message.lower()


def test_level_c_divergence_prediction_fails_the_preflight(case, monkeypatch):
    from preflight import runner

    monkeypatch.setattr(runner, "audit_case", lambda _c: [])
    monkeypatch.setattr(runner, "parse_checkmesh_metrics", lambda _log: {})
    monkeypatch.setattr(runner, "evaluate_mesh_metrics", lambda _m: [])

    nan_log = ("GAMG:  Solving for p, Initial residual = 0.99, "
               "Final residual = nan, No Iterations 50\n")
    monkeypatch.setattr(runner, "_bounded_foamrun",
                        lambda *_a: ValidationResult(False, "foamRun", [], nan_log))

    result = run_preflight_with_agent(case, _Agent())

    assert result.verdict is PreflightVerdict.FAIL
    assert "diverg" in (result.message + result.dry_run.message).lower()


def test_env_failure_only_matches_infrastructure_errors():
    env = ValidationResult(False, "foamRun",
                           [ValidationError(FailureClass.ENVIRONMENT, None, "no docker")])
    physics = ValidationResult(False, "foamRun",
                               [ValidationError(FailureClass.SOLVER_RUNTIME, None, "nan")])

    assert _env_failure(env) is True
    assert _env_failure(physics) is False
    assert _env_failure(ValidationResult(True, "foamRun")) is False
