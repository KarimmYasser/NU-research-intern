"""Preflight gate: cheap static + bounded dynamic checks that run BEFORE the full
Docker validation ladder, so a case with a config defect or a diverging setup
never pays the cost of a full (minutes-long) `foamRun`.

Three levels, run in order (fail-fast short-circuits later, more expensive ones):

  A. Static dictionary audit (no Docker). Walks the rendered 0/, system/,
     constant/ files and flags config-level defects: missing solver/loop blocks,
     missing relaxationFactors (the ρ-relaxation that was a root cause of the
     RHVT first-solve NaN), fvSchemes/fvSolution consistency, internalField
     positivity for solved scalars, gas-property sanity for compressible solvers.

  B. checkMesh metrics parser (cheap Docker: blockMesh/checkMesh rungs only).
     Pulls quantitative mesh-quality numbers out of checkMesh stdout so the
     report says *how* bad a mesh is, not just that it failed.

  C. Bounded solver dry-run + residual-slope predictor. Runs the solver for a
     small number of steps (or until the first non-finite residual) and predicts
     divergence from the trajectory of each field's *initial* residual across
     steps, before spending the full validation timeout.

Verdicts: PASS / WARN / FAIL. A preflight FAIL routes the case back to
build_intent (or patch_files) with the audit report as evidence — see
src/orchestration/graph.py `preflight_case`.

This mirrors the design in docs/architecture/phase-ii-architecture.md §2.2
(the validation ladder stays the source of truth for the actual Docker run; the
preflight gate only *predicts* which cases will fail and why).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path

from .audit import AuditFinding, audit_case
from .dry_run import DryRunPrediction, parse_residuals, predict_from_residuals
from .metrics import MeshMetrics, parse_checkmesh_metrics

__all__ = [
    "AuditFinding",
    "DryRunPrediction",
    "MeshMetrics",
    "PreflightResult",
    "PreflightVerdict",
    "audit_case",
    "parse_checkmesh_metrics",
    "parse_residuals",
    "predict_from_residuals",
    "run_preflight",
]


class PreflightVerdict(Enum):
    PASS = auto()
    WARN = auto()
    FAIL = auto()


@dataclass
class PreflightResult:
    """Aggregate result of a preflight gate run."""
    verdict: PreflightVerdict
    audit: list[AuditFinding] = field(default_factory=list)
    metrics: MeshMetrics | None = None
    dry_run: DryRunPrediction | None = None
    message: str = ""
    report_path: Path | None = None  # set when the report is written to disk

    def as_evidence(self) -> str:
        """Compact multi-line evidence for the retry loop's error_feedback."""
        lines: list[str] = []
        failures = [f for f in self.audit if f.level == "fail"]
        warnings = [f for f in self.audit if f.level == "warn"]
        if self.audit:
            lines.append(f"Preflight audit: {len(failures)} FAIL, {len(warnings)} WARN of {len(self.audit)} checks")
            for f in self.audit:
                lines.append(f"  [{f.level.upper()}] {f.message}")
        if self.metrics is not None:
            m = self.metrics
            lines.append(
                f"checkMesh metrics: cells={m.cells} "
                f"non-ortho(max={m.non_orthogonality_max if m.non_orthogonality_max is not None else '?'} "
                f"avg={m.non_orthogonality_avg if m.non_orthogonality_avg is not None else '?'}) "
                f"skewness(max={m.max_skewness if m.max_skewness is not None else '?'}) "
                f"aspect(max={m.max_aspect_ratio if m.max_aspect_ratio is not None else '?'}) "
                f"volumes(min={m.min_volume if m.min_volume is not None else '?'})"
            )
        if self.dry_run is not None:
            d = self.dry_run
            lines.append(f"Dry-run prediction: {d.prediction} after {d.steps} step(s)")
            for f_line in d.trend_line:
                lines.append("  " + f_line)
        if self.message:
            lines.append(self.message)
        return "\n".join(lines)

    def to_json(self) -> str:
        return json.dumps({
            "verdict": self.verdict.name,
            "audit": [f.as_dict() for f in self.audit],
            "metrics": self.metrics.as_dict() if self.metrics else None,
            "dry_run": self.dry_run.as_dict() if self.dry_run else None,
            "message": self.message,
        }, indent=2)


def run_preflight(case_dir: Path, dry_run_steps: int = 5) -> PreflightResult:
    """Level A static audit only (no Docker).

    The graph's preflight_case node runs A here, then optionally B/C via the
    ValidationAgent (see graph.py) — this function is the pure, testable core
    that never touches Docker.
    """
    findings = audit_case(case_dir)
    fails = sum(1 for f in findings if f.level == "fail")
    warns = sum(1 for f in findings if f.level == "warn")
    verdict = PreflightVerdict.FAIL if fails else (PreflightVerdict.WARN if warns else PreflightVerdict.PASS)
    return PreflightResult(verdict=verdict, audit=findings)