"""Full preflight orchestration (Levels A + B + C) for the graph.

Level A is pure static (run_preflight in __init__). Levels B and C need Docker,
so they take a ValidationAgent instance and short-circuit:

  A. static audit  — free, no Docker. FAIL here means no Docker run at all.
  B. mesh quality  — blockMesh (box cases) + checkMesh (cached for the ladder),
                     parsed into quantitative MeshMetrics.
  C. solver dry-run — bounded `foamRun` (endTime capped to a few steps) with
                     residual-slope prediction.

Any FAIL ends the preflight early and returns the evidence so the graph can
route back to build_intent before spending the full validation timeout.
"""

from __future__ import annotations

from pathlib import Path

from validation.validation_agent import FailureClass, ValidationAgent

from . import PreflightResult, PreflightVerdict
from .audit import audit_case
from .dry_run import bounded_run_predict
from .metrics import evaluate_mesh_metrics, parse_checkmesh_metrics


def run_preflight_with_agent(case_dir: Path, agent: ValidationAgent,
                             dry_run_steps: int = 5) -> PreflightResult:
    """Run Levels A→B→C in order; stop at the first FAIL (fail-fast)."""
    # --- Level A: static (never touches Docker) ---
    findings = audit_case(case_dir)
    result = PreflightResult(
        verdict=PreflightVerdict.WARN if any(f.level == "warn" for f in findings)
        else PreflightVerdict.PASS,
        audit=findings,
    )
    fails = [f for f in findings if f.level == "fail"]
    if fails:
        result.verdict = PreflightVerdict.FAIL
        result.message = f"Level A audit failed ({len(fails)} failing check(s)) — not running Docker"
        return result

    # --- Level B: mesh quality via checkMesh ---
    mesh_ok = _ensure_mesh(case_dir, agent)
    if _env_failure(mesh_ok):
        result.message = "Docker/OpenFOAM unavailable — preflight B/C skipped, deferring to the validation ladder"
        return result
    if not mesh_ok.ok:
        result.verdict = PreflightVerdict.FAIL
        result.message = f"mesh not available ({mesh_ok.stage} failed)"
        result.audit.append(_to_finding("mesh_unavailable",
                                        f"{mesh_ok.stage} failed: "
                                        + "; ".join(e.message for e in mesh_ok.errors)))
        return result

    check = agent.run_check_mesh(case_dir)
    if _env_failure(check):
        result.message = "Docker/OpenFOAM unavailable — preflight B/C skipped, deferring to the validation ladder"
        return result
    if check.ok:
        metrics = parse_checkmesh_metrics(check.log)
        result.metrics = metrics
        for code, level, msg in evaluate_mesh_metrics(metrics):
            result.audit.append(_to_finding(code, msg, level))
            if level == "fail":
                result.verdict = PreflightVerdict.FAIL
    elif any(e.failure_class == FailureClass.ENVIRONMENT for e in check.errors):
        result.message = "checkMesh unavailable (Docker/OpenFOAM env) — skipping Level B/C"
        return result
    else:
        result.verdict = PreflightVerdict.FAIL
        result.message = "checkMesh failed — mesh quality not acceptable"
        result.audit.append(_to_finding("checkmesh_fail",
                                        "; ".join(e.message for e in check.errors)))
        return result

    if result.verdict == PreflightVerdict.FAIL:
        result.message = "Level B mesh-quality check failed"
        return result

    # --- Level C: bounded solver dry run ---
    def _run(case_dir: Path):
        return _bounded_foamrun(case_dir, agent, dry_run_steps)

    result.dry_run = bounded_run_predict(case_dir, _run, dry_run_steps)
    if result.dry_run.prediction == "FAIL":
        result.verdict = PreflightVerdict.FAIL
        result.message = "Level C dry run predicts divergence"
    elif result.dry_run.prediction == "WARN" and result.verdict != PreflightVerdict.FAIL:
        result.verdict = PreflightVerdict.WARN
    else:
        result.message = "preflight passed all levels"
    return result


def _ensure_mesh(case_dir: Path, agent: ValidationAgent):
    """Make sure a mesh exists. Box cases have no polyMesh until blockMesh runs,
    so generate it first; external-mesh cases already have constant/polyMesh."""
    from validation.validation_agent import ValidationResult
    boundary = case_dir / "constant" / "polyMesh" / "boundary"
    if boundary.is_file():
        return ValidationResult(True, "mesh-present")
    return agent.run_block_mesh(case_dir)


def _bounded_foamrun(case_dir: Path, agent: ValidationAgent, steps: int):
    """Patch controlDict's endTime to `startTime + steps*deltaT` and run the
    solver, restoring the original controlDict afterwards.

    `writeControl` is set to `none` so the dry run doesn't litter time
    directories in the case; the original file is restored in a finally block
    regardless of whether the run crashes, diverges, or times out.
    """
    control = case_dir / "system" / "controlDict"
    original = control.read_text(encoding="utf-8") if control.is_file() else None
    import re
    try:
        if original is None:
            raise FileNotFoundError("controlDict missing — cannot bound the dry run")
        delta_m = re.search(r"^\s*deltaT\s+([-\d.eE+]+);", original, re.M)
        start_m = re.search(r"^\s*startTime\s+([-\d.eE+]+);", original, re.M)
        delta = float(delta_m.group(1)) if delta_m else 1.0
        start = float(start_m.group(1)) if start_m else 0.0
        new_end = start + steps * delta
        patched = re.sub(r"^\s*endTime\s+[-\d.eE+]+;", f"endTime         {new_end:g};", original, count=1, flags=re.M)
        patched = re.sub(r"^\s*writeControl\s+\S+;", "writeControl   none;", patched, count=1, flags=re.M)
        control.write_text(patched, encoding="utf-8")
        return agent.run_solver(case_dir)
    finally:
        if original is not None:
            control.write_text(original, encoding="utf-8")


def _env_failure(result) -> bool:
    """True when a ladder result failed purely on infrastructure (Docker not
    available), in which case preflight should defer to the validation ladder
    rather than fail the case."""
    from validation.validation_agent import FailureClass
    return (result is not None and not result.ok
            and any(e.failure_class == FailureClass.ENVIRONMENT for e in result.errors))


def _to_finding(code: str, msg: str, level: str = "fail"):
    from .audit import AuditFinding
    return AuditFinding(code=code, level=level, message=msg)