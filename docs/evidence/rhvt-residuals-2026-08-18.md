# RHVT Reference Case — Verified Residual Trace

Captured **2026-08-18** by re-running every preserved case in `temp/out/` through the
Docker validation ladder. Raw data: `temp/out/sweep_results.json` (gitignored scratch;
this file is the durable record).

## The working configuration

`temp/out/_w10_rhvt`, **10 MPI ranks**, 10 bounded steps, 130.9 s.

| Setting | Value |
|---|---|
| Solver module | `fluid` (v13, run by `foamRun`) |
| Mesh | supervisor's RHVT, ~10.6M faces, 9 patches |
| Turbulence | RAS |
| `ddtSchemes.default` | `steadyState` |
| Loop block | `PIMPLE` |
| Relaxation | `rho 0.05` (supervisor's recipe), `p 0.2`, `U`/`h`/`k`/`epsilon` 0.5 |
| `deltaT` | 1e-6 |

**Result: the case runs and converges slowly.** Initial residuals per step:

| step | Ux | Uy | Uz | p | h | k | epsilon |
|---|---|---|---|---|---|---|---|
| 1 | 1 | 1 | 1 | 0.05866 | 1 | 1 | 0.07313 |
| 2 | 0.06205 | 0.2701 | 0.2614 | 0.07007 | 0.3171 | 0.6119 | 0.3924 |
| 3 | 0.0408 | 0.1176 | 0.1061 | 0.1069 | 0.1615 | 0.2295 | 0.1342 |
| 4 | 0.03212 | 0.07993 | 0.06666 | 0.1161 | 0.1198 | 0.1717 | 0.1594 |
| 5 | 0.03019 | 0.05854 | 0.04621 | 0.09865 | 0.07662 | 0.1109 | 0.1031 |
| 6 | 0.03058 | 0.04355 | 0.0339 | 0.08354 | 0.05353 | 0.07609 | 0.07517 |
| 7 | 0.03148 | 0.0347 | 0.02765 | 0.07189 | 0.04281 | 0.05528 | 0.05805 |
| 8 | 0.02985 | 0.03109 | 0.02523 | 0.07737 | 0.03705 | 0.04323 | 0.04692 |
| 9 | 0.02801 | 0.03034 | 0.0246 | 0.08805 | 0.03315 | 0.03623 | 0.03918 |
| 10 | 0.02759 | 0.02915 | 0.02353 | 0.09075 | 0.03041 | 0.03179 | 0.03333 |

Log-slope per step: `Ux` −0.10, `Uy` −0.15, `Uz` −0.16, `h` −0.15, `k` −0.17,
`epsilon` −0.08 — all decaying.

**`p` is the laggard**: it falls to ~0.059 then drifts back up to ~0.091 while every
other field keeps dropping. That is the "slow decay" — the momentum, energy and
turbulence equations settle while pressure does not. Anyone continuing this case should
start with the pressure solve (relaxation, GAMG settings, outlet BC treatment).

Level C returns **WARN**, not PASS: the prediction correctly reflects that not
everything is converging.

## All cases swept

| case | procs | steps | outcome | error |
|---|---|---|---|---|
| `_w8_rhvt` | 10 | 1 | fail | keyword h is undefined in dictionary "//case/system/fvSolution/solvers |
| `_w10_rhvt` | 10 | 10 | **RUNS** | — |
| `e2e_rhvt_v3` | 4 | 0 | fail | solver diverged to NaN (no convergence): |
| `e2e_rhvt_v2` | 4 | 1 | fail | [2] keyword smoother is undefined in dictionary "IOstream/solvers/h" |
| `e2e_rhvt_v4` | 4 | 0 | fail | solver diverged to NaN (no convergence): |
| `e2e_rhvt` | 1 | 0 | fail | "ill defined primitiveEntry starting at keyword 'p' on line 16 and end |
| `vortextube_run` | 1 | 1 | fail | Unknown asymmetric matrix solver PCG |
| `divergence_case` | 8 | 10 | **RUNS** | — |
| `e2e_cavity` | 1 | 10 | **RUNS** | — |

Only `_w10_rhvt` (RHVT) and `divergence_case` / `e2e_cavity` complete 10 steps. The
other RHVT attempts fail for distinct, informative reasons: NaN on `h` (transient
Euler+PIMPLE variants), a missing `h` solver entry, an undefined `smoother`, and a
malformed `p` entry.

## Why this was nearly lost

`_w10_rhvt` was dismissed earlier because our own Level A audit **failed** it with
`loop_block_mismatch` (steadyState ddt + PIMPLE). That rule was wrong: the v13 `fluid`
module accepts the pairing, as the run above proves. It is now a warning, with a
regression test. A false FAIL that condemns the only working configuration is the most
expensive kind of bug this project can have.

Case directories are also **re-rendered in place**, so a working configuration is
overwritten by the next attempt, and no solver log is archived. Snapshot log + config
whenever a run behaves.
