"""Tests for the LangGraph orchestration (graph.py).

The LLM call (build_intent -> extract_intent) and Docker validation
(validate_case -> ValidationAgent.run_ladder) are monkeypatched so these run
fast, anywhere, with no local model endpoint or Docker needed - mirroring how
test_validation.py/test_intent.py isolate pure logic from those externals.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import orchestration.graph as graph_mod
from ir import CaseIntent, PatchIntent
from validation import ValidationResult


# A preflight result that always passes, so graph tests don't touch Docker
# through the preflight gate (they already stub the ladder's run_ladder).
def _preflight_pass(case_dir, agent=None, dry_run_steps=5):
    from preflight import PreflightResult, PreflightVerdict
    return PreflightResult(verdict=PreflightVerdict.PASS)

_BOUNDARY = """FoamFile
{
    version     2.0;
    format      ascii;
    class       polyBoundaryMesh;
    object      boundary;
}

3
(
    inlet
    {
        type patch;
        nFaces 10;
        startFace 100;
    }
    outlet
    {
        type patch;
        nFaces 10;
        startFace 110;
    }
    walls
    {
        type wall;
        nFaces 40;
        startFace 120;
    }
)
"""


def _make_mesh_dir(tmp_path: Path) -> Path:
    mesh_dir = tmp_path / "mesh"
    mesh_dir.mkdir()
    (mesh_dir / "boundary").write_text(_BOUNDARY)
    for name, content in [("points", "p"), ("faces", "f"), ("owner", "o"), ("neighbour", "n")]:
        (mesh_dir / name).write_text(content)
    return mesh_dir


def _fake_intent(nl, config=None, max_retries=3, known_patches=None, **kwargs):
    assert known_patches is not None and len(known_patches) == 3
    return CaseIntent(
        regime="laminar", nu=1e-5, geometry="external_mesh",
        patches=[
            PatchIntent(name="inlet", role="inlet", velocity=(1, 0, 0)),
            PatchIntent(name="outlet", role="outlet", pressure=0.0),
            PatchIntent(name="walls", role="wall"),
        ],
    )


def test_build_graph_compiles():
    app = graph_mod.build_graph().compile()
    assert app is not None


def test_route_after_validate_success_ends():
    state = {"validation": ValidationResult(True, "foamRun"), "attempt": 1, "max_attempts": 3}
    assert graph_mod.route_after_validate(state) is graph_mod.END


def test_route_after_validate_retries_then_stops():
    state = {"validation": ValidationResult(False, "checkMesh"), "attempt": 1, "max_attempts": 3}
    assert graph_mod.route_after_validate(state) == "build_intent"
    state["attempt"] = 3
    assert graph_mod.route_after_validate(state) is graph_mod.END


def test_route_after_validate_dictionary_syntax_goes_to_patch_files():
    """DICTIONARY_SYNTAX → targeted file patch, not full intent re-extraction."""
    from validation.validation_agent import FailureClass, ValidationError
    err = ValidationError(FailureClass.DICTIONARY_SYNTAX, "system/fvSolution", "bad keyword")
    result = ValidationResult(False, "foamRun", errors=[err])
    state = {"validation": result, "attempt": 1, "max_attempts": 3}
    assert graph_mod.route_after_validate(state) == "patch_files"


def test_route_after_validate_environment_goes_to_end():
    """ENVIRONMENT = infra failure; stop immediately, don't burn retries."""
    from validation.validation_agent import FailureClass, ValidationError
    err = ValidationError(FailureClass.ENVIRONMENT, None, "docker not found")
    result = ValidationResult(False, "checkMesh", errors=[err])
    state = {"validation": result, "attempt": 1, "max_attempts": 3}
    assert graph_mod.route_after_validate(state) is graph_mod.END


def test_route_after_validate_intent_error_ends():
    """Inner intent-extraction exhaustion must end the run, not crash the graph."""
    state = {"intent_error": "Could not extract a valid CaseIntent: boom", "attempt": 1, "max_attempts": 3}
    assert graph_mod.route_after_validate(state) is graph_mod.END


def test_route_after_preflight_pass_goes_to_validate():
    from preflight import PreflightResult, PreflightVerdict
    state = {"preflight": PreflightResult(verdict=PreflightVerdict.PASS), "attempt": 0, "max_attempts": 3}
    assert graph_mod.route_after_preflight(state) == "validate_case"


def test_route_after_preflight_fail_goes_to_build_intent():
    from preflight import PreflightResult, PreflightVerdict
    state = {"preflight": PreflightResult(verdict=PreflightVerdict.FAIL), "attempt": 0, "max_attempts": 3}
    assert graph_mod.route_after_preflight(state) == "build_intent"


def test_route_after_preflight_fail_exhausts_attempts():
    from preflight import PreflightResult, PreflightVerdict
    state = {"preflight": PreflightResult(verdict=PreflightVerdict.FAIL), "attempt": 3, "max_attempts": 3}
    assert graph_mod.route_after_preflight(state) is graph_mod.END


def test_route_after_preflight_env_skip_delegates_to_ladder():
    from preflight import PreflightResult, PreflightVerdict
    result = PreflightResult(verdict=PreflightVerdict.FAIL,
                             message="Docker/OpenFOAM unavailable — preflight B/C skipped, deferring to the validation ladder")
    state = {"preflight": result, "attempt": 0, "max_attempts": 3}
    assert graph_mod.route_after_preflight(state) == "validate_case"


def test_preflight_case_node_returns_preflight_and_feedback(tmp_path, monkeypatch):
    """preflight_case stores the result and feeds audit evidence back on FAIL."""
    from preflight import PreflightResult, PreflightVerdict
    case_dir = tmp_path / "case"
    case_dir.mkdir()

    def _fake_preflight(case_dir, agent=None, dry_run_steps=5):
        return PreflightResult(verdict=PreflightVerdict.FAIL,
                               message="missing_rho_relaxation")

    monkeypatch.setattr(graph_mod, "run_preflight_with_agent", _fake_preflight)
    state = {"case_dir": str(case_dir)}
    out = graph_mod.preflight_case(state)
    assert out["preflight"].verdict is PreflightVerdict.FAIL
    assert out["error_feedback"] and "missing_rho_relaxation" in out["error_feedback"]


def test_build_intent_surfaces_extraction_failure(monkeypatch):
    """When extract_intent exhausts its inner retries, build_intent returns a
    structured intent_error instead of letting the exception crash LangGraph."""
    def _boom(nl, config=None, known_patches=None, **kwargs):
        raise ValueError("Could not extract a valid CaseIntent after 3 tries: bad JSON")
    monkeypatch.setattr(graph_mod, "extract_intent", _boom)
    state = {"nl": "dummy", "attempt": 0, "max_attempts": 3}
    result = graph_mod.build_intent(state)
    assert result["intent_error"], "intent_error should be set on extraction failure"
    assert "bad JSON" in result["intent_error"]
    assert result["file_overrides"] is None


def test_build_intent_clears_stale_overrides(monkeypatch):
    monkeypatch.setattr(graph_mod, "extract_intent", _fake_intent)
    state = {
        "nl": "dummy",
        "attempt": 0,
        "file_overrides": {"system/fvSolution": "stale patch"},
        "known_patches": [
            graph_mod.MeshPatch(name="inlet", type="patch", n_faces=10, start_face=0),
            graph_mod.MeshPatch(name="outlet", type="patch", n_faces=10, start_face=10),
            graph_mod.MeshPatch(name="walls", type="wall", n_faces=40, start_face=20),
        ],
    }
    result = graph_mod.build_intent(state)
    assert result["file_overrides"] is None, "build_intent must clear stale file_overrides"


def test_full_run_writes_case_and_copies_polymesh(tmp_path, monkeypatch):
    mesh_dir = _make_mesh_dir(tmp_path)
    case_dir = tmp_path / "case"

    monkeypatch.setattr(graph_mod, "extract_intent", _fake_intent)
    monkeypatch.setattr(
        graph_mod.ValidationAgent, "run_ladder",
        lambda self, case_dir, skip_mesh_generation=False: ValidationResult(True, "foamRun"),
    )
    monkeypatch.setattr(graph_mod, "run_preflight_with_agent", _preflight_pass)

    final = graph_mod.run("dummy nl", case_dir, mesh_dir=mesh_dir)

    assert final["validation"].ok
    assert final["attempt"] == 1
    assert (case_dir / "constant" / "polyMesh" / "boundary").is_file()
    assert (case_dir / "constant" / "polyMesh" / "points").read_text() == "p"
    assert (case_dir / "0" / "U").is_file()
    assert (case_dir / "system" / "controlDict").is_file()
    assert not (case_dir / "system" / "blockMeshDict").exists()  # external mesh: no blockMesh


def test_full_run_box_geometry_generates_blockmesh(tmp_path, monkeypatch):
    """Without a mesh_dir the graph renders a box case (blockMeshDict) and runs
    the full ladder — the box path through the same orchestrator."""
    case_dir = tmp_path / "case"

    def _box_intent(nl, config=None, max_retries=3, known_patches=None, **kwargs):
        assert known_patches == []  # no external mesh -> no known patch names
        return CaseIntent(
            regime="laminar", nu=1e-5, geometry="box", box_size=(0.1, 0.1, 0.1),
            cells=(20, 20, 1),
            patches=[
                PatchIntent(name="movingWall", role="movingWall", velocity=(1, 0, 0)),
                PatchIntent(name="fixedWalls", role="wall"),
                PatchIntent(name="frontAndBack", role="empty"),
            ],
        )

    monkeypatch.setattr(graph_mod, "extract_intent", _box_intent)
    monkeypatch.setattr(
        graph_mod.ValidationAgent, "run_ladder",
        lambda self, case_dir, skip_mesh_generation=False: ValidationResult(True, "foamRun"),
    )
    monkeypatch.setattr(graph_mod, "run_preflight_with_agent", _preflight_pass)

    final = graph_mod.run("dummy box nl", case_dir)

    assert final["validation"].ok
    assert (case_dir / "system" / "blockMeshDict").is_file()  # box -> blockMeshDict rendered
    assert not (case_dir / "constant" / "polyMesh").exists()  # nothing copied
    assert (case_dir / "0" / "U").is_file()


def test_full_run_retries_on_failure_then_stops(tmp_path, monkeypatch):
    mesh_dir = _make_mesh_dir(tmp_path)
    case_dir = tmp_path / "case"

    monkeypatch.setattr(graph_mod, "extract_intent", _fake_intent)
    monkeypatch.setattr(
        graph_mod.ValidationAgent, "run_ladder",
        lambda self, case_dir, skip_mesh_generation=False: ValidationResult(False, "checkMesh"),
    )
    monkeypatch.setattr(graph_mod, "run_preflight_with_agent", _preflight_pass)

    final = graph_mod.run("dummy nl", case_dir, mesh_dir=mesh_dir, max_attempts=2)

    assert not final["validation"].ok
    assert final["attempt"] == 2  # stopped after max_attempts, did not loop forever


def test_render_case_applies_file_overrides(tmp_path):
    mesh_dir = _make_mesh_dir(tmp_path)
    case_dir = tmp_path / "case"

    intent = _fake_intent("dummy", known_patches=[
        graph_mod.MeshPatch(name="inlet", type="patch", n_faces=10, start_face=0),
        graph_mod.MeshPatch(name="outlet", type="patch", n_faces=10, start_face=10),
        graph_mod.MeshPatch(name="walls", type="wall", n_faces=40, start_face=20),
    ])
    spec = graph_mod.intent_to_spec(intent)

    state = {
        "spec": spec,
        "case_dir": str(case_dir),
        "mesh_dir": str(mesh_dir),
        "file_overrides": {"system/fvSolution": "// patched fvSolution\n"},
    }

    graph_mod.render_case(state)
    assert (case_dir / "system" / "fvSolution").read_text() == "// patched fvSolution\n"


def test_render_case_skips_nested_override_instead_of_crashing(tmp_path):
    mesh_dir = _make_mesh_dir(tmp_path)
    case_dir = tmp_path / "case"

    intent = _fake_intent("dummy", known_patches=[
        graph_mod.MeshPatch(name="inlet", type="patch", n_faces=10, start_face=0),
        graph_mod.MeshPatch(name="outlet", type="patch", n_faces=10, start_face=10),
        graph_mod.MeshPatch(name="walls", type="wall", n_faces=40, start_face=20),
    ])
    spec = graph_mod.intent_to_spec(intent)

    state = {
        "spec": spec,
        "case_dir": str(case_dir),
        "mesh_dir": str(mesh_dir),
        # An LLM sometimes emits a section of a file instead of the whole file;
        # "system/fvSolution/solvers" nests below the real file system/fvSolution,
        # so it cannot be applied as a whole-file override without crashing.
        "file_overrides": {
            "system/fvSolution": "// whole-file patch\n",
            "system/fvSolution/solvers": "solvers {}",
        },
    }

    graph_mod.render_case(state)  # must not raise
    assert (case_dir / "system" / "fvSolution").read_text() == "// whole-file patch\n"
    assert not (case_dir / "system" / "fvSolution" / "solvers").exists()


if __name__ == "__main__":
    import inspect
    import tempfile

    class _FakeMonkeypatch:
        def __init__(self):
            self._undo = []

        def setattr(self, obj, name, value):
            self._undo.append((obj, name, getattr(obj, name)))
            setattr(obj, name, value)

        def undo(self):
            for obj, name, old in self._undo:
                setattr(obj, name, old)

    n = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            params = inspect.signature(fn).parameters
            with tempfile.TemporaryDirectory() as d:
                args = []
                mp = _FakeMonkeypatch()
                if "tmp_path" in params:
                    args.append(Path(d))
                if "monkeypatch" in params:
                    args.append(mp)
                fn(*args)
                mp.undo()
            print(f"ok  {name}")
            n += 1
    print(f"\n{n} tests passed")
