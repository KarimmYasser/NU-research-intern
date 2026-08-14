"""Tests for the Safe Exit mechanism with evidence-backed anti-laziness guardrails."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from ir.intent import CaseIntent
from ir.safe_exit import SafeExitCategory, SafeExitEvidence, SafeExitReport
from knowledge.intent_validator import IntentValidator
from orchestration.graph import CaseGraphState, build_graph
from validation.validation_agent import FailureClass, ValidationError, ValidationResult


@pytest.fixture
def validator():
    return IntentValidator()


# --------------------------------------------------------------------------
# Category Validation Tests (Positive / Valid Cases)
# --------------------------------------------------------------------------

def test_valid_corrupt_mesh_safe_exit_with_verified_checkmesh_log(validator):
    # Simulated checkMesh output containing fatal negative volumes
    val_res = SimpleNamespace(
        ok=False,
        stage="checkMesh",
        stdout="--> FOAM FATAL ERROR: ***Negative cell volume detected in cell 1024",
        stderr="",
        errors=[ValidationError(failure_class=FailureClass.MESH_GENERATION, message="Negative volume", file_hint=None)],
    )
    report = SafeExitReport(
        category=SafeExitCategory.CORRUPT_OR_UNCLOSED_MESH,
        error_summary="The external mesh contains fatal negative cell volumes that cannot be repaired.",
        evidence=[
            SafeExitEvidence(source="check_mesh", detail="Negative cell volume detected in cell 1024")
        ],
        exhausted_alternatives=[
            "Checked boundary definitions and attempted non-orthogonal correction up to 4"
        ],
        user_action_required=[
            "Repair the CAD boundary manifold and re-export a watertight polyMesh directory."
        ],
    )
    intent = CaseIntent(safe_exit=report)
    errors = validator.validate(intent, validation_result=val_res)
    assert not errors, f"Expected valid safe exit, got errors: {errors}"


def test_valid_contradictory_physics_safe_exit_with_prompt_quotes(validator):
    prompt = "Simulate incompressible flow at Mach 3 supersonic airflow with no pressure."
    report = SafeExitReport(
        category=SafeExitCategory.CONTRADICTORY_PHYSICS,
        error_summary="User requested incompressible flow at supersonic Mach 3, which is physically impossible.",
        evidence=[
            SafeExitEvidence(source="user_prompt", detail="incompressible flow"),
            SafeExitEvidence(source="user_prompt", detail="Mach 3 supersonic airflow"),
        ],
        exhausted_alternatives=[
            "Checked compressible solver module compatibility with requested zero-pressure constraint"
        ],
        user_action_required=[
            "Clarify and choose between incompressible subsonic flow or compressible high-speed flow."
        ],
    )
    intent = CaseIntent(safe_exit=report)
    errors = validator.validate(intent, prompt_text=prompt)
    assert not errors, f"Expected valid safe exit, got errors: {errors}"


def test_reject_contradictory_physics_with_single_prompt_quote(validator):
    prompt = "Simulate incompressible Mach 3 supersonic airflow with no pressure and no density."
    report = SafeExitReport(
        category=SafeExitCategory.CONTRADICTORY_PHYSICS,
        error_summary="User requested incompressible flow at supersonic Mach 3, which is physically impossible.",
        evidence=[
            SafeExitEvidence(source="user_prompt", detail="Mach 3 supersonic airflow"),
        ],
        exhausted_alternatives=[
            "Checked compressible solver module compatibility with requested zero-pressure constraint"
        ],
        user_action_required=[
            "Clarify and choose between incompressible subsonic flow or compressible high-speed flow."
        ],
    )
    intent = CaseIntent(safe_exit=report)
    errors = validator.validate(intent, prompt_text=prompt)
    assert any("at least two distinct evidence citations" in e.message for e in errors)


def test_reject_corrupt_mesh_when_only_non_orthogonality_reported(validator):
    val_res = SimpleNamespace(
        ok=False,
        stage="checkMesh",
        stdout="Mesh non-orthogonality Max: 78.5 average: 22.1. Severe non-orthogonality detected.",
        stderr="",
        errors=[ValidationError(failure_class=FailureClass.MESH_GENERATION, message="Non-orthogonality", file_hint=None)],
    )
    report = SafeExitReport(
        category=SafeExitCategory.CORRUPT_OR_UNCLOSED_MESH,
        error_summary="Severe non-orthogonality detected in mesh checks that may cause instability.",
        evidence=[
            SafeExitEvidence(source="check_mesh", detail="Severe non-orthogonality detected")
        ],
        exhausted_alternatives=[
            "Attempted non-orthogonal correctors up to 4"
        ],
        user_action_required=[
            "Repair CAD surface geometry and re-generate mesh."
        ],
    )
    intent = CaseIntent(safe_exit=report)
    errors = validator.validate(intent, validation_result=val_res)
    assert any("Mesh quality warnings alone (non-orthogonality/skewness) do not justify safe exit" in e.message for e in errors)


def test_reject_case_file_path_traversal(validator, tmp_path):
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    secret_file = tmp_path / "secret.txt"
    secret_file.write_text("top secret configuration")

    report = SafeExitReport(
        category=SafeExitCategory.MISSING_ESSENTIAL_DATA,
        error_summary="External mechanism parameter file escapes case directory boundaries.",
        evidence=[
            SafeExitEvidence(source="case_file", detail="top secret configuration", file_path="../secret.txt")
        ],
        exhausted_alternatives=[
            "Checked standard thermodynamics database"
        ],
        user_action_required=[
            "Provide reaction mechanism table inside case directory."
        ],
    )
    intent = CaseIntent(safe_exit=report)
    errors = validator.validate(intent, case_dir=case_dir)
    assert any("must be relative within the case directory" in e.message for e in errors)


# --------------------------------------------------------------------------
# Graph Routing & Orchestration Tests
# --------------------------------------------------------------------------

def test_graph_routes_to_end_on_validated_safe_exit(monkeypatch, tmp_path):
    report = SafeExitReport(
        category=SafeExitCategory.CONTRADICTORY_PHYSICS,
        error_summary="User requested incompressible Mach 3 supersonic airflow with conflicting physical settings.",
        evidence=[
            SafeExitEvidence(source="user_prompt", detail="incompressible Mach 3"),
            SafeExitEvidence(source="user_prompt", detail="supersonic airflow with no pressure"),
        ],
        exhausted_alternatives=[
            "Checked compressible solver module compatibility with requested settings"
        ],
        user_action_required=[
            "Clarify and choose between incompressible subsonic flow or compressible high-speed flow."
        ],
    )
    mock_intent = CaseIntent(safe_exit=report)

    # Monkeypatch extract_intent in graph module to return mock_intent directly
    monkeypatch.setattr("orchestration.graph.extract_intent", lambda *args, **kwargs: mock_intent)

    app = build_graph().compile()
    initial_state: CaseGraphState = {
        "nl": "Simulate incompressible Mach 3 supersonic airflow with no pressure and no density.",
        "case_dir": str(tmp_path / "case"),
        "max_attempts": 3,
    }
    final_state = app.invoke(initial_state)
    assert final_state.get("safe_exit") is not None
    assert final_state.get("safe_exit").category == SafeExitCategory.CONTRADICTORY_PHYSICS
    assert final_state.get("done") is True
    # Verify that downstream rendering/validation nodes were bypassed
    assert "spec" not in final_state
    assert "validation" not in final_state


def _load_main():
    import importlib.util
    main_path = Path(__file__).resolve().parent.parent / "src" / "__main__.py"
    spec = importlib.util.spec_from_file_location("src_main", main_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load module spec from {main_path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.main


def test_main_cli_returns_2_on_validated_safe_exit(monkeypatch, tmp_path, capsys):
    main = _load_main()
    report = SafeExitReport(
        category=SafeExitCategory.MISSING_ESSENTIAL_DATA,
        error_summary="Custom proprietary chemical kinetic mechanism parameters are missing from prompt.",
        evidence=[
            SafeExitEvidence(source="user_prompt", detail="custom proprietary chemical kinetic")
        ],
        exhausted_alternatives=[
            "Checked single-species transport models"
        ],
        user_action_required=[
            "Provide the Arrhenius reaction rate coefficients and species heat capacities."
        ],
    )
    mock_final_state = {
        "safe_exit": report,
        "attempt": 1,
        "done": True,
    }
    monkeypatch.setattr("orchestration.graph.run", lambda **kwargs: mock_final_state)
    monkeypatch.setattr("sys.argv", [
        "__main__.py", "run",
        "--prompt", "Simulate burner with custom proprietary chemical kinetic mechanism.",
        "--case-dir", str(tmp_path / "case"),
    ])

    exit_code = main()
    assert exit_code == 2
    captured = capsys.readouterr().out
    assert "SAFE EXIT — HUMAN INTERVENTION REQUIRED" in captured
    assert "Missing Essential Data" in captured
    assert "What You Need To Do:" in captured


def test_main_cli_returns_1_on_standard_failure(monkeypatch, tmp_path):
    main = _load_main()
    val_res = ValidationResult(
        ok=False,
        stage="checkMesh",
        errors=[ValidationError(failure_class=FailureClass.MESH_GENERATION, message="checkMesh failed", file_hint=None)],
    )
    mock_final_state = {
        "validation": val_res,
        "attempt": 3,
        "done": False,
    }
    monkeypatch.setattr("orchestration.graph.run", lambda **kwargs: mock_final_state)
    monkeypatch.setattr("sys.argv", [
        "__main__.py", "run",
        "--prompt", "Simulate cavity flow.",
        "--case-dir", str(tmp_path / "case"),
    ])

    exit_code = main()
    assert exit_code == 1


def test_validate_safe_exit_returns_structured_result(validator):
    report = SafeExitReport(
        category=SafeExitCategory.MISSING_ESSENTIAL_DATA,
        error_summary="Reacting gas mixture requires detailed chemical mechanism tables.",
        evidence=[
            SafeExitEvidence(source="user_prompt", detail="custom reacting fuel blend")
        ],
        exhausted_alternatives=[
            "Evaluated standard thermodynamic models"
        ],
        user_action_required=[
            "Provide reaction mechanism table and species molar masses."
        ],
    )
    intent = CaseIntent(safe_exit=report)
    prompt = "Simulate burner with custom reacting fuel blend."
    res = validator.validate_safe_exit(intent, prompt_text=prompt, attempt=2)
    assert res.is_valid is True
    assert len(res.rejection_reasons) == 0
    assert len(res.verified_evidence) == 1
    assert res.verified_evidence[0].source == "user_prompt"
    assert res.validated_at_attempt == 2


def test_run_pipeline_returns_pipeline_result_on_safe_exit(monkeypatch, tmp_path):
    from orchestration import run_pipeline
    report = SafeExitReport(
        category=SafeExitCategory.UNSUPPORTED_PHYSICS,
        error_summary="Relativistic plasma magnetohydrodynamics is outside supported solver capabilities.",
        evidence=[
            SafeExitEvidence(source="user_prompt", detail="relativistic plasma magnetohydrodynamics")
        ],
        exhausted_alternatives=[
            "Checked CASE_TYPES registry for supported fluid modules"
        ],
        user_action_required=[
            "Select a supported approximation using standard compressible fluid modules."
        ],
    )
    mock_intent = CaseIntent(safe_exit=report)
    monkeypatch.setattr("orchestration.graph.extract_intent", lambda *args, **kwargs: mock_intent)

    result = run_pipeline(
        nl="Run relativistic plasma magnetohydrodynamics simulation.",
        case_dir=tmp_path / "case",
        max_attempts=3,
    )
    assert result.success is False
    assert result.exit_code == 2
    assert result.safe_exit is not None
    assert result.safe_exit.category == SafeExitCategory.UNSUPPORTED_PHYSICS
    assert result.safe_exit_validation is not None
    assert result.safe_exit_validation.is_valid is True


def test_reject_safe_exit_with_invalid_remediation_attempt_ids(validator):
    from ir.safe_exit import RemediationAttempt
    history = [
        RemediationAttempt(
            attempt=1,
            issue_class="PREFLIGHT_AUDIT",
            action="preflight_case",
            configuration_changes={},
            outcome="failed",
            evidence_reference=None,
        )
    ]
    report = SafeExitReport(
        category=SafeExitCategory.MISSING_ESSENTIAL_DATA,
        error_summary="Reacting gas mixture requires detailed chemical mechanism tables.",
        evidence=[
            SafeExitEvidence(source="user_prompt", detail="custom reacting fuel blend")
        ],
        exhausted_alternatives=[
            "Evaluated standard thermodynamic models"
        ],
        user_action_required=[
            "Provide reaction mechanism table and species molar masses."
        ],
        remediation_attempt_ids=[1, 99],  # Attempt 99 does not exist in history
    )
    intent = CaseIntent(safe_exit=report)
    prompt = "Simulate burner with custom reacting fuel blend."
    errors = validator.validate(intent, prompt_text=prompt, remediation_history=history)
    assert any("cited remediation attempt #99 was not found" in e.message for e in errors)


def test_valid_safe_exit_with_preflight_numeric_negative_volume(validator):
    from preflight import PreflightResult, PreflightVerdict
    from preflight.metrics import MeshMetrics
    metrics = MeshMetrics(
        cells=5000,
        non_orthogonality_max=45.0,
        non_orthogonality_avg=15.0,
        max_skewness=1.2,
        max_aspect_ratio=3.0,
        min_volume=-0.005,  # Fatal negative volume!
        max_volume=0.1,
        min_face_area=0.001,
        max_face_area=0.05,
        failed_checks=1,
        ok=False,
        raw="***Negative cell volume detected in cell 1024: -0.005",
    )
    preflight = PreflightResult(
        verdict=PreflightVerdict.FAIL,
        metrics=metrics,
        message="checkMesh detected fatal negative volumes",
    )
    report = SafeExitReport(
        category=SafeExitCategory.CORRUPT_OR_UNCLOSED_MESH,
        error_summary="External polyMesh contains negative cell volumes that prevent solution initialization.",
        evidence=[
            SafeExitEvidence(source="check_mesh", detail="Negative cell volume detected in cell 1024")
        ],
        exhausted_alternatives=[
            "Attempted non-orthogonal correctors up to 4"
        ],
        user_action_required=[
            "Repair CAD boundary manifold and re-export a watertight polyMesh directory."
        ],
    )
    intent = CaseIntent(safe_exit=report)
    errors = validator.validate(intent, preflight_result=preflight)
    assert not errors, f"Expected valid safe exit with preflight negative volume proof, got: {errors}"
