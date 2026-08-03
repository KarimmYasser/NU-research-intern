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

import agents.graph as graph_mod
from agents.intent import CaseIntent, PatchIntent
from agents.validation_agent import ValidationResult

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


def _fake_intent(nl, config=None, max_retries=3, known_patches=None):
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


def test_full_run_writes_case_and_copies_polymesh(tmp_path, monkeypatch):
    mesh_dir = _make_mesh_dir(tmp_path)
    case_dir = tmp_path / "case"

    monkeypatch.setattr(graph_mod, "extract_intent", _fake_intent)
    monkeypatch.setattr(
        graph_mod.ValidationAgent, "run_ladder",
        lambda self, case_dir, skip_mesh_generation=False: ValidationResult(True, "foamRun"),
    )

    final = graph_mod.run("dummy nl", mesh_dir, case_dir)

    assert final["validation"].ok
    assert final["attempt"] == 1
    assert (case_dir / "constant" / "polyMesh" / "boundary").is_file()
    assert (case_dir / "constant" / "polyMesh" / "points").read_text() == "p"
    assert (case_dir / "0" / "U").is_file()
    assert (case_dir / "system" / "controlDict").is_file()
    assert not (case_dir / "system" / "blockMeshDict").exists()  # never generated (R1)


def test_full_run_retries_on_failure_then_stops(tmp_path, monkeypatch):
    mesh_dir = _make_mesh_dir(tmp_path)
    case_dir = tmp_path / "case"

    monkeypatch.setattr(graph_mod, "extract_intent", _fake_intent)
    monkeypatch.setattr(
        graph_mod.ValidationAgent, "run_ladder",
        lambda self, case_dir, skip_mesh_generation=False: ValidationResult(False, "checkMesh"),
    )

    final = graph_mod.run("dummy nl", mesh_dir, case_dir, max_attempts=2)

    assert not final["validation"].ok
    assert final["attempt"] == 2  # stopped after max_attempts, did not loop forever


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
