# Preflight Gate: Cheap Prediction Before the Expensive Ladder

The validation ladder (`docs/architecture/phase-ii-architecture.md` §2.2) is the
**source of truth** — but a full ladder run costs minutes (`foamRun` on a large
mesh), and a case with a config defect or a diverging setup pays that cost every
time even though it can never pass. The **preflight gate** is the cheap,
fail-fast predictor that runs *before* the ladder so known-bad cases are caught
before they pay.

It implements the architecture's core principle — *validate cheaply and early,
heal locally before promoting* — as an actual node in the LangGraph
(`preflight_case`), not just a design note.

## The three levels

| Level | Cost | What it runs | Catches (mapped to known failure classes) |
|---|---|---|---|
| **A — static audit** | free, no Docker | walks the rendered `0/`, `system/`, `constant/` files (`src/preflight/audit.py`) | missing solver / PIMPLE/SIMPLE loop block; missing relaxationFactors incl. the ρ-relaxation that was a root cause of the RHVT first-solve NaN; missing energy-field solver entry (the "keyword h is undefined" DICTIONARY_SYNTAX root cause); ddtSchemes consistency; non-positive internal fields; endTime ≤ startTime |
| **B — checkMesh metrics** | seconds (blockMesh/checkMesh) | parses quantitative mesh quality out of checkMesh stdout (`src/preflight/metrics.py`) | non-orthogonality > 70 warn / > 140 fail; skewness > 4 warn / > 8 fail; aspect ratio > 1000 warn; negative cell volumes fail |
| **C — bounded dry run** | tens of seconds | patches controlDict to a few steps, runs the solver, then classifies each field's residual trajectory (`src/preflight/dry_run.py`) | divergence prediction from the least-squares slope of log10(initial residual) vs step index |

Fail-fast: any FAIL at a level short-circuits the rest — a Level-A FAIL never
even starts Docker.

## How the dry-run predictor works

Level C is the interesting one. It patches `system/controlDict` to
`endTime = startTime + steps·deltaT` and `writeControl none` (so the dry run
doesn't litter time directories), runs the solver, restores the file in a
`finally` block, and classifies each field's trajectory:

- **slope ≥ +0.05 / step** (log10 residual rising) → **FAIL** (divergence)
- **slope ≤ −0.05 / step** (decaying) → **PASS** — a single noisy uptick does
  not flip a decaying trend to FAIL
- **flat / stalled** between the bands → **WARN**
- **residual ≤ 1e-6** (machine floor) → **PASS** regardless of slope
- **any NaN/inf**, or **zero captured steps** → **FAIL**

This uses log-slope rather than raw residual because residuals decay
exponentially; the *trend* is the signal, not the instantaneous value. The
predictor is deliberately conservative about marking FAIL — it only flags a
case when the divergence signal is unambiguous, keeping false positives that
waste a build_intent retry low.

## Where it sits in the graph

```
render_case → preflight_case → validate_case        (PASS / WARN)
                    │
                    └─── FAIL → build_intent   (audit evidence as error_feedback)
```

- `preflight_case` runs `run_preflight_with_agent()` (`src/preflight/runner.py`),
  which orchestrates Levels A → B → C, stopping at the first FAIL.
- On FAIL, the verdict's evidence (`PreflightResult.as_evidence()`) is carried
  into `error_feedback`, so the retry's LLM call sees *why* ("[preflight] audit:
  2 FAIL, 1 WARN ... missing ρ relaxation factor") instead of a raw log.
- **Environment skip, not a case failure:** if Docker/OpenFOAM is unavailable,
  preflight defers to the ladder with an env-skip verdict rather than failing
  the case on infra (`runner.py:_env_failure`). The ladder remains the source
  of truth; preflight only *predicts*.
- Routing respects `max_attempts` (`route_after_preflight`, `graph.py`) so a
  preflight FAIL loop can't spin forever.

## What changed because of it

- A case with a missing ρ relaxation (the RHVT root cause) is now caught at
  Level A — *minutes before* the ladder would have burned the full
  `FOAM_VALIDATION_TIMEOUT` on a doomed `foamRun`.
- A case whose mesh is known-bad (Level B) or whose residuals are already rising
  (Level C) routes back to `build_intent` with structured evidence, without a
  full ladder run.
- `PreflightResult.to_json()` gives a machine-readable artifact for the RL /
  reward-signal thread: the preflight verdict is a cheap, deterministic proxy
  for "this case is doomed" that the ladder later confirms.

## Tests and status

`tests/test_preflight.py` covers the Level-A audit, the checkMesh metrics
parser, the residual parser, and the predictor (rising / decaying / flat /
NaN / no-steps / machine-floor verdicts). The graph wiring is exercised in
`tests/test_graph.py` via the routing stubs. Status: gate wired into the graph;
the RHVT divergence (first-solve NaN) is the canonical case it should catch.