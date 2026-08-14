"""Level C preflight: bounded solver dry-run + residual-slope divergence predictor.

Runs the solver for a small, bounded number of time steps (or until the first
non-finite residual, which the ValidationAgent already aborts on) and watches
the *initial* residual of each field step over step. The diagnostic signal is
the slope, not the magnitude: a field whose initial residual rises (ratio > 1)
for ≥ 3 consecutive steps is heading for divergence even though it has not gone
NaN yet. NaN anywhere inside the run is an immediate FAIL.

The dry run is cheap (≈ a few steps / the first NaN abort) compared with a full
validation ladder, so it slots in ahead of validate_case in the graph.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path

# OpenFOAM solver log: `GAMG:  Solving for p, Initial residual = 0.998493,
# Final residual = nan, No Iterations 50`. Time advances are delimited by
# `Time = 0.0002` lines.
_SOLVE_RE = re.compile(
    r"Solving for\s+(?P<field>\w+),\s*Initial residual\s*=\s*(?P<init>[-\w.]+|[+-]?nan|inf|[-+]?inf),"
)
_TIME_RE = re.compile(r"^\s*Time\s*=\s*(?P<t>\S+)", re.MULTILINE)

# Slope bands on log10(initial residual) vs step index. A residual that decays
# by ≥ ~12% per step is genuinely converging (slope ≤ -0.05); one that grows by
# ≥ ~12% per step is diverging (slope ≥ +0.05); anything in between is stalled
# (flat) and gets a WARN, not a pass.
DECAY_SLOPE = -0.05  # log10-units per step
RISE_SLOPE = 0.05
CONVERGED_ABS = 1e-6  # residual already at machine floor -> pass regardless of slope
# A rising residual only means *divergence* when it is rising at a substantial
# magnitude. Incompressible pressure-coupling transients (pseudo-transient
# runs starting from a near-uniform field) routinely see p's initial residual
# drift up from ~0.03 while staying small and then plateau — the solver is
# still converging each step (Final residuals are small). A 5-step dry run can
# not tell a modest rise from a real instability, so it must not FAIL a case
# whose residuals never reach a significant level. A genuine divergence (RHVT
# first-solve NaN, residual order 1 rising toward inf) clears the bar.
RISING_MAGNITUDE = 0.5  # peak initial residual above which a rise is real divergence


@dataclass
class DryRunPrediction:
    prediction: str  # "PASS" | "WARN" | "FAIL"
    steps: int  # number of Time steps captured by the dry run
    fields: dict[str, list[float]] = field(default_factory=dict)  # field -> initial residual per step
    trend_line: list[str] = field(default_factory=list)  # human-readable per-field verdicts
    message: str = ""

    def as_dict(self) -> dict:
        return {
            "prediction": self.prediction,
            "steps": self.steps,
            "fields": self.fields,
            "trend": self.trend_line,
            "message": self.message,
        }


def parse_residuals(log_text: str) -> tuple[list[dict[str, float]], int]:
    """Group initial residuals by Time-step.

    Returns (steps, n_steps) where each step maps field-name -> initial residual
    and n_steps is the number of `Time = ...` markers seen (0 for a log that
    never reached a step, e.g. a dictionary error before the first solve).
    NaN residuals are preserved as float('nan') so the predictor can detect them.
    """
    time_offs = [m.end() for m in _TIME_RE.finditer(log_text)]
    steps: list[dict[str, float]] = []
    for i, off in enumerate(time_offs):
        end = time_offs[i + 1] if i + 1 < len(time_offs) else len(log_text)
        block = log_text[off:end]
        step: dict[str, float] = {}
        for m in _SOLVE_RE.finditer(block):
            raw = m.group("init").strip()
            if raw in ("nan", "NAN", "+nan", "-nan") or "nan" in raw.lower():
                step[m.group("field")] = math.nan
            elif raw in ("inf", "+inf", "-inf"):
                step[m.group("field")] = math.copysign(float("inf"), -1 if raw.startswith("-") else 1)
            else:
                try:
                    step[m.group("field")] = float(raw)
                except ValueError:
                    continue
        steps.append(step)
    return steps, len(time_offs)


def predict_from_residuals(steps: list[dict[str, float]], n_steps: int) -> DryRunPrediction:
    """Turn parsed per-step residuals into a PASS/WARN/FAIL prediction."""
    if n_steps == 0:
        return DryRunPrediction("FAIL", 0, message="solver produced no Time steps — "
                               "likely a dictionary/field error before the first solve")

    # Any non-finite residual anywhere => already diverged (or about to).
    for step in steps:
        for f_name, val in step.items():
            if not math.isfinite(val):
                return DryRunPrediction(
                    "FAIL", n_steps,
                    message=f"{f_name}: initial residual went non-finite (nan/inf) — "
                            "diverged during the dry run")

    # Per-field series aligned to step index (missing -> nan).
    fields_series: dict[str, list[float]] = {}
    for step in steps:
        for f_name in step:
            fields_series.setdefault(f_name, [math.nan] * len(steps))
    for f_name in fields_series:
        fields_series[f_name] = [step.get(f_name, math.nan) for step in steps]

    trend_lines: list[str] = []
    worst = "PASS"
    last_field_rise = 0
    # A rise in one field only reads as a *coupling transient* (not divergence)
    # when at least one other field is genuinely decaying — i.e. the solver is
    # otherwise converging and this field is just settling into its coupled
    # position (seen live: the cavity's p residual drifting 0.03 -> 0.2 while
    # Ux/Uy decay). If NO field is decaying, every rise is the leading edge of
    # an instability, even at modest magnitude.
    slopes: dict[str, float] = {}
    peaks: dict[str, float] = {}
    series_map: dict[str, list[float]] = {}
    for f_name, series in fields_series.items():
        clean = [v for v in series if math.isfinite(v)]
        series_map[f_name] = clean
        if len(clean) >= 2:
            slopes[f_name] = _log_slope(clean)
            peaks[f_name] = max(clean)
    any_decaying = any(slopes.get(f, 0.0) <= DECAY_SLOPE for f in fields_series)

    for f_name, series in sorted(series_map.items()):
        if len(series) < 2:
            trend_lines.append(f"{f_name}: {len(series)} data point(s) — insufficient to judge")
            worst = "WARN" if worst == "PASS" else worst
            continue
        slope = slopes[f_name]
        trailing = " -> ".join(f"{v:.3g}" for v in series)
        # Already at the machine floor: converged, flat is fine.
        if series[-1] <= CONVERGED_ABS:
            trend_lines.append(f"{f_name}: converged ({trailing}) — OK")
            continue
        # Fail: a rise to a substantial magnitude is unambiguous divergence.
        # A rise at modest magnitude is only diverging when NOTHING else is
        # converging; otherwise it is the pressure-coupling start-up transient
        # (cavity live run) and is downgraded to WARN so the source-of-truth
        # ladder gets to decide instead of wasting a build_intent retry.
        if slope >= RISE_SLOPE:
            peak = peaks[f_name]
            if peak >= RISING_MAGNITUDE or not any_decaying:
                trend_lines.append(f"{f_name}: RISING (log-slope +{slope:.2f}/step, peak {peak:.3g}, {trailing}) — divergence predicted")
                worst = "FAIL"
                last_field_rise += 1
            else:
                trend_lines.append(f"{f_name}: rising but small (log-slope +{slope:.2f}/step, peak {peak:.3g}) — coupling transient, not yet diverging")
                worst = "WARN" if worst == "PASS" else worst
        elif slope <= DECAY_SLOPE:
            trend_lines.append(f"{f_name}: decaying (log-slope {slope:.2f}/step, {trailing}) — OK")
        else:
            trend_lines.append(f"{f_name}: flat/stalled (log-slope {slope:+.2f}/step, {trailing}) — not yet converging")
            worst = "WARN" if worst == "PASS" else worst

    if worst == "FAIL":
        msg = (f"{last_field_rise} field(s) on a rising residual trajectory "
               f"(log-slope ≥ +{RISE_SLOPE:g}/step, peak ≥ {RISING_MAGNITUDE:g}, "
               f"or no field decaying)" )
    elif worst == "WARN":
        msg = "fields not yet converging but no field is diverging — flat/stalled, insufficient data, or a small coupling transient"
    else:
        msg = "all fields' initial residuals decay (or have converged) across steps"
    return DryRunPrediction(worst, n_steps, fields=fields_series,
                            trend_line=trend_lines, message=msg)


def _log_slope(values: list[float]) -> float:
    """Least-squares slope of log10(residual) vs step index (log-units/step).

    A converging run has each initial residual *smaller* than the previous one,
    so on a log scale it is a line with negative slope. This is the stable
    trajectory signal — pairwise comparisons misread a single noisy uptick as
    divergence, and ratios compress once residuals approach zero.
    """
    pts = [(i, math.log10(max(v, 1e-300))) for i, v in enumerate(values)]
    n = len(pts)
    xm = sum(x for x, _ in pts) / n
    ym = sum(y for _, y in pts) / n
    num = sum((x - xm) * (y - ym) for x, y in pts)
    den = sum((x - xm) ** 2 for x, _ in pts)
    return num / den if den else 0.0


def bounded_run_predict(case_dir: Path, validation, dry_run_steps: int = 5) -> DryRunPrediction:
    """Run a bounded `foamRun` dry run on the case and predict its fate.

    `validation` is a callable(case_dir) -> ValidationResult that runs the
    solver (injected so this module stays Docker-free and the graph supplies a
    short-timeout ValidationAgent). The callable must already abort on the first
    NaN, which caps the cost even for a diverged case.
    """
    from validation.validation_agent import ValidationResult  # noqa: F401
    result = validation(case_dir)
    steps, n = parse_residuals(result.log)
    pred = predict_from_residuals(steps, n)
    # If the run was aborted early due to a non-finite residual but the parser
    # captured < dry_run_steps, surface why into the message.
    if pred.prediction == "PASS" and n < dry_run_steps:
        pred.prediction = "WARN"
        if pred.message:
            pred.message += f"; short run ({n}<{dry_run_steps} steps)"
        else:
            pred.message = f"dry run only reached {n} step(s) — truncated/aborted early"
    return pred