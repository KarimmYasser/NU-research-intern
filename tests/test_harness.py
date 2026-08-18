from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from harness.mcp_server import (
    AGENT_RULES_FALLBACK,
    AGENT_RULES_PATH,
    MCPOpenFOAMServer,
    _parse_workspace_dir,
    load_agent_rules,
)
from harness.tools import OpenFOAMToolbox
from validation import ValidationResult


def test_module_launches_outside_repository(tmp_path: Path) -> None:
    src = Path(__file__).parents[1] / "src"
    result = subprocess.run(
        [sys.executable, "-m", "harness.mcp_server", "--list-tools"],
        cwd=tmp_path,
        env=os.environ | {"PYTHONPATH": str(src)},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "Registered 19 OpenFOAM tools" in result.stdout


def test_agent_rules_have_a_single_source_of_truth() -> None:
    """The Pi skill and the MCP prompt must not drift into two separate copies."""
    assert AGENT_RULES_PATH.is_file(), f"canonical rules file missing: {AGENT_RULES_PATH}"
    legacy = Path(__file__).parents[1] / "src/harness/openfoam_agent_rules.md"
    assert not legacy.exists(), "the superseded rules file reappeared; edit the canonical rules instead"

    # The pi skill points at the canonical file. Normally a symlink; checkouts that
    # cannot create symlinks get a copy materialised by ./setup.sh (pi_sync_skill).
    # Either form is fine, but the content must never diverge.
    skill = Path(__file__).parents[1] / "integrations/pi/skills/openfoam/SKILL.md"
    if skill.is_symlink():
        assert skill.resolve() == AGENT_RULES_PATH.resolve()
    else:
        assert skill.read_text(encoding="utf-8") == AGENT_RULES_PATH.read_text(encoding="utf-8"), (
            "SKILL.md is a copy that has drifted from src/harness/rules/openfoam.md; re-run ./setup.sh"
        )
    # Frontmatter lives in the canonical file so the skill loader still accepts it.
    assert AGENT_RULES_PATH.read_text(encoding="utf-8").startswith("---")

    rules = load_agent_rules()
    assert rules != AGENT_RULES_FALLBACK
    # Frontmatter is skill-loader metadata and must not leak into the prompt.
    assert not rules.startswith("---")
    assert "description:" not in rules.split("\n")[0]
    # Key behavioural rules survive the move.
    assert "never guess" in rules.lower()
    assert "run_preflight_audit" in rules


def test_agent_guidance_never_teaches_pre_v13_conventions() -> None:
    """Models are v9-biased; our own guidance must not reinforce that.

    Verified against src/knowledge/v13_tutorials.json: v13 uses a `solver <module>;`
    entry in controlDict (177 cases) and zero `application` entries, and the files are
    physicalProperties / momentumTransport, never transportProperties /
    turbulenceProperties (0 occurrences each).
    """
    rules_dir = Path(__file__).parents[1] / "src/harness/rules"
    sources = {path: path.read_text(encoding="utf-8") for path in rules_dir.rglob("*.md")}
    sources[Path("tools.py:diagnostics")] = (
        Path(__file__).parents[1] / "src/harness/tools.py"
    ).read_text(encoding="utf-8")
    assert sources, "no rules files found"

    for path, text in sources.items():
        lowered = text.lower()
        # These may only appear as an explicitly-labelled wrong/old example.
        for banned in ("application foamrun", "application icofoam", "application simplefoam"):
            for line in lowered.splitlines():
                if banned in line:
                    assert any(
                        marker in line
                        for marker in ("wrong", "not", "never", "pre-v12", "old", "|")
                    ), f"{path}: '{banned}' presented as v13-correct"
        for legacy, modern in (("transportproperties", "physicalProperties"),
                               ("turbulenceproperties", "momentumTransport")):
            if legacy in lowered:
                assert modern.lower() in lowered, (
                    f"{path}: mentions {legacy} without the v13 name {modern}"
                )


def test_v13_reference_is_reachable_from_the_skill() -> None:
    """The skill links to references/v13-vs-older.md; the link must resolve."""
    skill_dir = Path(__file__).parents[1] / "integrations/pi/skills/openfoam"
    reference = skill_dir / "references" / "v13-vs-older.md"
    assert reference.is_file(), "the v13-vs-older reference is not reachable from the skill"
    assert "references/v13-vs-older.md" in load_agent_rules(), "the skill does not link the reference"
    body = reference.read_text(encoding="utf-8")
    # Guidance must stay generic: a placeholder, not a module name to copy verbatim.
    assert "solver          <module>;" in body


def test_prompts_get_serves_the_skill_body() -> None:
    server = MCPOpenFOAMServer()
    response = server.handle_request({"jsonrpc": "2.0", "id": 1, "method": "prompts/get", "params": {}})
    assert response is not None
    text = response["result"]["messages"][0]["content"]["text"]
    assert text == load_agent_rules()
    assert "OpenFOAM v13 CFD Assistant" in text


def test_workspace_dir_argument_is_parsed() -> None:
    assert _parse_workspace_dir(["--workspace-dir", "/tmp/ws"]) == "/tmp/ws"
    assert _parse_workspace_dir(["--workspace-dir=/tmp/ws"]) == "/tmp/ws"
    assert _parse_workspace_dir(["--self-test"]) is None


def test_workspace_dir_flag_confines_tools(tmp_path: Path) -> None:
    """--workspace-dir makes the harness deterministic regardless of cwd."""
    src = Path(__file__).parents[1] / "src"
    workspace = tmp_path / "sim-workspace"
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    request = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "scaffold_case", "arguments": {"case_name": "ws_probe"}},
        }
    )
    result = subprocess.run(
        [sys.executable, "-m", "harness.mcp_server", "--workspace-dir", str(workspace)],
        cwd=elsewhere,
        input=request + "\n",
        env=os.environ | {"PYTHONPATH": str(src)},
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    # The workspace is created up front and the case lands inside it, not in cwd.
    assert workspace.is_dir()
    assert str(workspace) in result.stdout
    assert not (elsewhere / "ws_probe").exists()


def test_json_rpc_notifications_and_invalid_requests() -> None:
    server = MCPOpenFOAMServer()
    assert server.handle_request({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    invalid = server.handle_request({"id": 1, "method": "ping"})
    assert invalid is not None
    assert invalid["error"]["code"] == -32600


def test_case_paths_are_confined_to_workspace_and_case(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    toolbox = OpenFOAMToolbox(workspace_dir=workspace)
    outside = tmp_path / "outside"
    outside.mkdir()
    assert not toolbox.write_case_file(str(outside), "file", "content")["success"]
    case = workspace / "case"
    case.mkdir()
    assert not toolbox.write_case_file(str(case), "../escaped", "content")["success"]
    assert not toolbox.write_case_file(str(case), str(tmp_path / "absolute"), "content")["success"]


def test_scaffold_refuses_non_empty_target_without_overwrite(tmp_path: Path) -> None:
    toolbox = OpenFOAMToolbox(workspace_dir=tmp_path)
    target = tmp_path / "case"
    target.mkdir()
    marker = target / "keep.txt"
    marker.write_text("keep", encoding="utf-8")
    assert not toolbox.scaffold_case("case")["success"]
    assert marker.read_text(encoding="utf-8") == "keep"


def test_clean_requires_case_structure_and_supports_dry_run(tmp_path: Path) -> None:
    toolbox = OpenFOAMToolbox(workspace_dir=tmp_path)
    unsafe = tmp_path / "not-a-case"
    unsafe.mkdir()
    (unsafe / "1").mkdir()
    assert not toolbox.clean_case_directory(str(unsafe))["success"]
    assert (unsafe / "1").exists()
    case = tmp_path / "case"
    (case / "0").mkdir(parents=True)
    (case / "constant").mkdir()
    (case / "system").mkdir()
    (case / "system" / "controlDict").write_text("", encoding="utf-8")
    (case / "1").mkdir()
    preview = toolbox.clean_case_directory(str(case), dry_run=True)
    assert preview["success"] and "1" in preview["removed_items"]
    assert (case / "1").exists()
    toolbox.clean_case_directory(str(case))
    assert not (case / "1").exists()


def test_external_mesh_validation_skips_generation(tmp_path: Path, monkeypatch) -> None:
    case = tmp_path / "case"
    (case / "constant" / "polyMesh").mkdir(parents=True)
    (case / "constant" / "polyMesh" / "boundary").write_text("", encoding="utf-8")
    calls: list[bool] = []

    class Agent:
        def __init__(self, **_kwargs) -> None:
            pass

        def run_ladder(self, _case: Path, skip_mesh_generation: bool = False) -> ValidationResult:
            calls.append(skip_mesh_generation)
            return ValidationResult(True, "foamRun", [], "")

    monkeypatch.setattr("harness.tools.ValidationAgent", Agent)
    result = OpenFOAMToolbox(workspace_dir=tmp_path).validate_case_docker(str(case))
    assert result["success"]
    assert calls == [True]


def test_envelope_and_cfl_reject_invalid_inputs() -> None:
    toolbox = OpenFOAMToolbox()
    assert not toolbox.check_physical_envelope("air", "density", 1.2)["success"]
    assert not toolbox.check_physical_envelope("air", "nu", float("nan"))["success"]
    assert not toolbox.calculate_cfl_timestep([1, 1, 1], [1, 0, 1], 1)["success"]
    assert not toolbox.calculate_cfl_timestep([1, 1, 1], [1, 1, 1], float("nan"))["success"]


def test_mcp_tool_schemas_are_json_serializable() -> None:
    encoded = json.dumps(list(MCPOpenFOAMServer().tools.values()))
    assert '"default": false' in encoded

def test_agent_rules_teach_that_preflight_findings_are_not_verdicts():
    """A false FAIL from our own audit once hid the working RHVT configuration.

    The rules must tell the agent to believe a run that works over a static
    checker, and must not tell it to blindly 'fix what preflight reports'.
    """
    rules = load_agent_rules().lower()
    assert "evidence, not" in rules, "preflight findings not framed as evidence"
    assert "false positive" in rules
    assert "fix what it reports" not in rules, (
        "rules still instruct blind compliance with the audit"
    )


def test_agent_rules_cover_direct_case_editing_and_residual_triage():
    rules = load_agent_rules().lower()
    assert "editing a case directly" in rules
    assert "one change at a time" in rules
    # symptom-driven triage, not a copyable recipe
    assert "stalls" in rules and "relaxation" in rules
    # Archiving is enforced in code (validation.snapshot), so the rules should point
    # at the artefact rather than ask the model to remember to copy files.
    assert ".runs/" in rules, "rules never mention where a passing run is archived"
    assert "archived for you" in rules


def test_run_bounded_dry_run_invokes_the_solver_and_reports_trends(tmp_path, monkeypatch):
    """Level C was entirely broken from the harness for weeks.

    `bounded_run_predict` wants a callable(case_dir); the tool handed it the
    ValidationAgent object, so every call raised
    "'ValidationAgent' object is not callable" — and no test noticed, because
    nothing exercised this tool at all.
    """
    from harness.tools import OpenFOAMToolbox
    from preflight import runner

    case = tmp_path / "c"
    (case / "system").mkdir(parents=True)

    seen = {}

    class _Result:
        ok = True
        log = "\n".join(
            f"Time = {i}\nsmoothSolver:  Solving for Ux, Initial residual = {1.0 / (2**i)}, "
            f"Final residual = 1e-08, No Iterations 3"
            for i in range(1, 5)
        )

    def fake_bounded(case_path, agent, steps):
        seen["case"] = Path(case_path)
        seen["steps"] = steps
        seen["agent_is_callable_object"] = callable(agent)
        return _Result()

    monkeypatch.setattr(runner, "_bounded_foamrun", fake_bounded)

    out = OpenFOAMToolbox(workspace_dir=tmp_path).run_bounded_dry_run(str(case), dry_run_steps=4)

    assert seen["case"] == case.resolve(), "the solver was never invoked on the case"
    assert seen["steps"] == 4, "dry_run_steps was not forwarded"
    assert out["steps_captured"] == 4
    assert out["prediction"] in {"PASS", "WARN"}
    assert any("Ux" in line for line in out["per_field_trends"])


def test_run_bounded_dry_run_reports_a_missing_case(tmp_path):
    from harness.tools import OpenFOAMToolbox

    out = OpenFOAMToolbox(workspace_dir=tmp_path).run_bounded_dry_run(str(tmp_path / "nope"))

    assert out["success"] is False
    assert "not found" in out["error"].lower()


# ---------------------------------------------------------------------------
# Thin smoke coverage for the tools that had none. Level C shipped broken for
# weeks precisely because nothing called it; these are cheap sentinels that a
# tool still returns its documented shape, not deep behavioural tests.
# ---------------------------------------------------------------------------


def _toolbox(tmp_path):
    from harness.tools import OpenFOAMToolbox

    return OpenFOAMToolbox(workspace_dir=tmp_path)


def test_diagnose_case_failure_classifies_a_real_log(tmp_path):
    log = ('--> FOAM FATAL IO ERROR:\nkeyword nu is undefined in dictionary '
           '"/case/constant/physicalProperties"\nfile: /case/constant/physicalProperties at line 20.')
    out = _toolbox(tmp_path).diagnose_case_failure(log)

    assert out["failure_class"], "no failure class returned"
    assert out["root_cause"] and out["recommended_fix"]
    assert "physicalProperties" in str(out.get("target_file") or out["root_cause"])


def test_inspect_mesh_reports_a_missing_mesh(tmp_path):
    case = tmp_path / "c"
    (case / "system").mkdir(parents=True)

    out = _toolbox(tmp_path).inspect_mesh(str(case))

    assert out["has_polyMesh"] is False
    assert out["has_blockMeshDict"] is False


def test_inspect_mesh_reads_patch_names(tmp_path):
    case = tmp_path / "c"
    poly = case / "constant" / "polyMesh"
    poly.mkdir(parents=True)
    (poly / "boundary").write_text(
        "2\n(\n    inlet { type patch; nFaces 10; startFace 0; }\n"
        "    walls { type wall; nFaces 20; startFace 10; }\n)\n", encoding="utf-8")

    out = _toolbox(tmp_path).inspect_mesh(str(case))

    assert out["has_polyMesh"] is True
    names = {p.get("name") if isinstance(p, dict) else str(p) for p in out["patches"]}
    assert {"inlet", "walls"} <= names


def test_get_tutorial_numerics_returns_schemes_for_a_real_solver(tmp_path):
    out = _toolbox(tmp_path).get_tutorial_numerics("incompressibleFluid")

    assert out["has_schemes"] is True
    assert "ddtSchemes" in str(out["snippets"])


def test_search_openfoam_knowledge_resolves_a_solver_family(tmp_path):
    out = _toolbox(tmp_path).search_openfoam_knowledge("lid driven cavity laminar flow")

    assert out["recommended_solver"], "no solver family resolved"
    assert out["reference_cases"] or out["matched_families"]


def test_import_external_mesh_rejects_a_missing_source(tmp_path):
    case = tmp_path / "c"
    case.mkdir()

    out = _toolbox(tmp_path).import_external_mesh(str(case), str(tmp_path / "nope"))

    assert out["success"] is False


def test_read_postprocessing_data_reports_absence(tmp_path):
    case = tmp_path / "c"
    case.mkdir()

    out = _toolbox(tmp_path).read_postprocessing_data(str(case))

    assert out["success"] is False
    assert "postprocessing" in out["error"].lower()
