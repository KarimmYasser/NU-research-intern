"""ValidationAgent logic that does not need Docker.

The agent was 67% covered and the gaps were the rungs themselves, not just the
container plumbing. These stub `_docker_run` and test the decisions the agent
makes about its output — including the checkMesh pass cache, which would hand
back a stale PASS for an edited mesh if its key were wrong.
"""

import subprocess

import pytest

from validation.validation_agent import FailureClass, ValidationAgent

BOUNDARY = """\
2
(
    inlet { type patch; nFaces 10; startFace 0; }
    walls { type wall; nFaces 20; startFace 10; }
)
"""

CHECKMESH_OK = "Checking geometry...\nMesh OK.\nEnd\n"
CHECKMESH_BAD = """\
Checking geometry...
 ***Number of severely non-orthogonal faces: 12.
Failed 1 mesh checks.
End
"""


@pytest.fixture(autouse=True)
def _clear_mesh_cache():
    ValidationAgent._CHECKED_MESHES.clear()
    yield
    ValidationAgent._CHECKED_MESHES.clear()


@pytest.fixture
def case(tmp_path):
    c = tmp_path / "c"
    poly = c / "constant" / "polyMesh"
    poly.mkdir(parents=True)
    (poly / "boundary").write_text(BOUNDARY, encoding="utf-8")
    return c


def _stub(agent, stdout="", returncode=0):
    """Replace the container call, recording the argv it was asked to run."""
    seen = []

    def fake(case_dir, args, **kwargs):
        seen.append(list(args))
        return subprocess.CompletedProcess(args=args, returncode=returncode,
                                           stdout=stdout, stderr="")

    agent._docker_run = fake
    return seen


def test_checkmesh_failure_is_detected_despite_exit_code_zero(case):
    """checkMesh exits 0 even when quality checks fail; only the text says so."""
    agent = ValidationAgent()
    _stub(agent, CHECKMESH_BAD, returncode=0)

    result = agent.run_check_mesh(case)

    assert result.ok is False
    assert result.stage == "checkMesh"


def test_checkmesh_pass_is_cached_but_not_across_a_changed_mesh(case):
    """A cached PASS keyed on the mesh *content* — editing the mesh must re-check,
    otherwise a broken mesh inherits the previous mesh's verdict."""
    agent = ValidationAgent()
    seen = _stub(agent, CHECKMESH_OK)

    assert agent.run_check_mesh(case).ok is True
    assert agent.run_check_mesh(case).ok is True
    assert len(seen) == 1, "cache did not prevent the second container call"

    (case / "constant" / "polyMesh" / "boundary").write_text(
        BOUNDARY.replace("nFaces 20", "nFaces 21"), encoding="utf-8")
    agent.run_check_mesh(case)

    assert len(seen) == 2, "a modified mesh reused the cached PASS"


def test_force_bypasses_the_cache(case):
    agent = ValidationAgent()
    seen = _stub(agent, CHECKMESH_OK)

    agent.run_check_mesh(case)
    agent.run_check_mesh(case, force=True)

    assert len(seen) == 2


def test_a_failed_checkmesh_is_never_cached(case):
    agent = ValidationAgent()
    seen = _stub(agent, CHECKMESH_BAD)

    agent.run_check_mesh(case)
    agent.run_check_mesh(case)

    assert len(seen) == 2, "a failing mesh was cached as checked"


def test_all_checks_adds_the_expensive_flags(case):
    agent = ValidationAgent()
    seen = _stub(agent, CHECKMESH_OK)

    agent.run_check_mesh(case, all_checks=True)

    assert "-allTopology" in seen[0] and "-meshQuality" in seen[0]


def test_default_checkmesh_stays_cheap_on_large_meshes(case):
    agent = ValidationAgent()
    seen = _stub(agent, CHECKMESH_OK)

    agent.run_check_mesh(case)

    assert seen[0] == ["checkMesh"], "expensive flags on by default"


def test_blockmesh_failure_is_classified_as_mesh_generation(case):
    agent = ValidationAgent()
    _stub(agent, "--> FOAM FATAL ERROR:\nCannot find file blockMeshDict\n", returncode=1)

    result = agent.run_block_mesh(case)

    assert result.ok is False
    assert result.errors
    assert result.errors[0].failure_class is FailureClass.MESH_GENERATION


def test_list_times_falls_back_to_scanning_the_case(case):
    """foamListTimes needs a working container; the disk scan is what makes
    get_case_status usable when Docker is not there."""
    agent = ValidationAgent()
    _stub(agent, "", returncode=1)
    for name in ("0", "0.5", "2", "constant", "system"):
        (case / name).mkdir(exist_ok=True)

    times = agent.list_times(case)

    assert times == ["0", "0.5", "2"]
    assert "constant" not in times


def test_read_dictionary_entry_returns_none_when_the_query_fails(case):
    agent = ValidationAgent()
    _stub(agent, "", returncode=1)

    assert agent.read_dictionary_entry(case, "system/controlDict", "solver") is None


def test_read_dictionary_entry_strips_the_value(case):
    agent = ValidationAgent()
    _stub(agent, "  fluid  \n", returncode=0)

    assert agent.read_dictionary_entry(case, "system/controlDict", "solver") == "fluid"
