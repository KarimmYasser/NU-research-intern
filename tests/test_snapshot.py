"""Snapshotting a run that worked.

Case directories are re-rendered in place, so a good configuration is destroyed by
the next attempt. This nearly lost the RHVT reference result; these tests pin the
behaviour that replaced "remember to archive it" guidance.
"""

import json
from typing import ClassVar

import pytest

from validation.snapshot import SNAPSHOT_DIRNAME, list_snapshots, snapshot_run


@pytest.fixture
def case(tmp_path):
    c = tmp_path / "case"
    (c / "system").mkdir(parents=True)
    (c / "0").mkdir()
    (c / "constant" / "polyMesh").mkdir(parents=True)
    (c / "system" / "controlDict").write_text("solver fluid;\n", encoding="utf-8")
    (c / "system" / "fvSolution").write_text("PIMPLE { nCorrectors 2; }\n", encoding="utf-8")
    (c / "0" / "U").write_text("internalField uniform (0 0 0);\n", encoding="utf-8")
    (c / "constant" / "physicalProperties").write_text("nu 1e-05;\n", encoding="utf-8")
    (c / "constant" / "polyMesh" / "owner").write_text("x" * 50_000, encoding="utf-8")
    return c


def test_snapshot_keeps_inputs_and_log(case):
    dest = snapshot_run(case, "Time = 1\nresidual 0.1\n", stage="all", nprocs=10)

    assert (dest / "system" / "controlDict").read_text().strip() == "solver fluid;"
    assert (dest / "0" / "U").is_file()
    assert (dest / "constant" / "physicalProperties").is_file()
    assert "Time = 1" in (dest / "solver.log").read_text()

    manifest = json.loads((dest / "manifest.json").read_text())
    assert manifest["nprocs"] == 10
    assert manifest["stage"] == "all"
    assert "system/controlDict" in manifest["files"]


def test_snapshot_excludes_the_mesh(case):
    """The RHVT mesh is 374 MB; copying it per run would fill the disk."""
    dest = snapshot_run(case, "")

    assert not (dest / "constant" / "polyMesh").exists()
    assert all("polyMesh" not in f for f in json.loads((dest / "manifest.json").read_text())["files"])


def test_snapshots_accumulate_without_overwriting(case):
    first = snapshot_run(case, "run one", stage="all")
    (case / "system" / "controlDict").write_text("solver incompressibleFluid;\n", encoding="utf-8")
    second = snapshot_run(case, "run two", stage="checkMesh")

    assert first != second
    assert len(list_snapshots(case)) == 2
    # the earlier configuration must survive the later render
    assert "fluid;" in (first / "system" / "controlDict").read_text()
    assert "incompressibleFluid" in (second / "system" / "controlDict").read_text()


def test_unreadable_file_is_recorded_not_fatal(case, monkeypatch):
    import validation.snapshot as snap

    real_copy = snap.shutil.copy2

    def flaky(src, dst):
        if "fvSolution" in str(src):
            raise OSError(13, "Permission denied")
        return real_copy(src, dst)

    monkeypatch.setattr(snap.shutil, "copy2", flaky)
    manifest = json.loads((snapshot_run(case, "") / "manifest.json").read_text())

    assert any("fvSolution" in s for s in manifest["skipped"])
    assert "system/controlDict" in manifest["files"]


def test_clean_case_directory_does_not_delete_snapshots(case):
    from harness.tools import OpenFOAMToolbox

    snapshot_run(case, "keep me")
    (case / "0.1").mkdir()
    (case / "log.foamRun").write_text("x", encoding="utf-8")

    result = OpenFOAMToolbox(workspace_dir=case.parent).clean_case_directory(str(case))

    assert (case / SNAPSHOT_DIRNAME).is_dir(), "archived runs were cleaned away"
    assert SNAPSHOT_DIRNAME not in json.dumps(result)
    assert not (case / "0.1").exists()


def test_pruning_keeps_only_the_most_recent(case):
    """An unattended self-correction loop can validate many times; .runs must not grow
    without bound."""
    from validation.snapshot import prune_snapshots

    for i in range(6):
        snapshot_run(case, f"run {i}", stage="all", keep=100)
    assert len(list_snapshots(case)) == 6

    removed = prune_snapshots(case, keep=2)

    kept = list_snapshots(case)
    assert len(kept) == 2
    assert len(removed) == 4
    # the survivors must be the newest, i.e. the last written
    assert "run 5" in (kept[-1] / "solver.log").read_text()


def test_snapshot_prunes_as_it_writes(case):
    for i in range(5):
        snapshot_run(case, f"run {i}", stage="all", keep=3)

    kept = list_snapshots(case)
    assert len(kept) == 3
    assert "run 4" in (kept[-1] / "solver.log").read_text(), "pruned the snapshot it just made"


def test_same_second_snapshots_do_not_collide(case):
    """Two validations in one second happen inside a retry loop, and their names must
    stay chronologically ordered even after pruning has freed earlier names."""
    a = snapshot_run(case, "first", stage="all", keep=100)
    b = snapshot_run(case, "second", stage="all", keep=100)

    assert a != b
    assert (a / "solver.log").read_text() == "first"
    assert (b / "solver.log").read_text() == "second"
    assert list_snapshots(case) == [a, b], "snapshot names are not chronologically sorted"


def test_graph_validate_node_archives_a_passing_run(tmp_path, monkeypatch, capsys):
    """The pipeline re-renders the case in place on the next attempt, so the graph
    must archive too — not just the interactive harness."""
    from orchestration import graph

    case = tmp_path / "c"
    (case / "system").mkdir(parents=True)
    (case / "system" / "controlDict").write_text("solver fluid;\n", encoding="utf-8")

    class _Result:
        ok = True
        stage = "foamRun"
        errors: ClassVar[list] = []
        log = "Time = 1\nEnd\n"

    class _Agent:
        def run_ladder(self, *_a, **_k):
            return _Result()

    monkeypatch.setattr(graph, "_make_validation_agent", lambda _s: _Agent())
    out = graph.validate_case({"case_dir": str(case), "attempt": 2, "procs": 4})

    assert out["done"] is True
    snaps = list_snapshots(case)
    assert len(snaps) == 1, "a passing pipeline run was not archived"
    manifest = json.loads((snaps[0] / "manifest.json").read_text())
    assert manifest["attempt"] == 2
    assert manifest["nprocs"] == 4
    assert "Time = 1" in (snaps[0] / "solver.log").read_text()
    assert ".runs/" in capsys.readouterr().out


def test_graph_archive_failure_does_not_fail_a_passing_run(tmp_path, monkeypatch):
    from orchestration import graph

    case = tmp_path / "c"
    case.mkdir()

    class _Result:
        ok = True
        stage = "foamRun"
        errors: ClassVar[list] = []
        log = ""

    class _Agent:
        def run_ladder(self, *_a, **_k):
            return _Result()

    monkeypatch.setattr(graph, "_make_validation_agent", lambda _s: _Agent())
    monkeypatch.setattr(graph, "snapshot_run", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))

    out = graph.validate_case({"case_dir": str(case), "attempt": 1})

    assert out["done"] is True, "bookkeeping turned a passing run into a failure"


def test_long_solver_log_is_trimmed_head_and_tail(case):
    """A 1000-step cavity writes ~13k lines; ten of those per case is 15 MB of logs.

    The residuals and the outcome are at the tail, the build/case identity at the head.
    """
    log = "\n".join(
        ["HEADER line 0", *[f"Time = {i}" for i in range(1, 9000)], "End", "Finalising"]
    )
    dest = snapshot_run(case, log)

    saved = (dest / "solver.log").read_text()
    manifest = json.loads((dest / "manifest.json").read_text())

    assert manifest["log_trimmed"] is True
    assert manifest["log_lines"] == len(log.splitlines()), "reported the trimmed length"
    assert "HEADER line 0" in saved, "dropped the header"
    assert "Finalising" in saved and "End" in saved, "dropped the outcome"
    assert "lines omitted" in saved
    assert len(saved.splitlines()) < len(log.splitlines()) / 3


def test_short_log_is_stored_verbatim(case):
    log = "Time = 1\nresidual 0.1\nEnd\n"
    dest = snapshot_run(case, log)

    assert (dest / "solver.log").read_text() == log
    assert json.loads((dest / "manifest.json").read_text())["log_trimmed"] is False


def _status(case):
    from harness.tools import OpenFOAMToolbox

    return OpenFOAMToolbox(workspace_dir=case.parent).get_case_status(str(case))


def test_case_status_lists_archived_runs(case):
    assert "archived_runs" not in _status(case)

    snapshot_run(case, "run one")
    status = _status(case)

    assert len(status["archived_runs"]) == 1
    assert status["archived_runs_dir"].endswith(".runs")


def test_case_status_reads_the_v13_solver_entry(case):
    """The status tool used to look only for a pre-v13 `application` entry, so every
    correctly generated v13 case reported solver 'unknown'."""
    (case / "system" / "controlDict").write_text(
        'FoamFile { version 2.0; }\nsolver          incompressibleFluid;\nendTime  0.5;\n',
        encoding="utf-8")

    status = _status(case)

    assert status["solver"] == "incompressibleFluid"
    assert status["target_endTime"] == "0.5"
    assert "warning" not in status


def test_case_status_flags_a_legacy_application_entry(case):
    (case / "system" / "controlDict").write_text(
        "application     icoFoam;\nendTime  0.5;\n", encoding="utf-8")

    status = _status(case)

    assert status["solver"] == "icoFoam"
    assert "pre-v13" in status["warning"]
