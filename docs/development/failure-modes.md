# Failure Modes

What actually goes wrong, why, and what now prevents it. This is the evidence base
for the report's "failure modes" section
([`../project/technical-report-outline.md`](../project/technical-report-outline.md)),
and the first place to look when something breaks.

Every entry is tagged:

- **[observed]** — it happened, in this repo, and is recorded in the git history or
  `../progress/progress-log.md`.
- **[anticipated]** — a known risk with a guard in place, not yet seen in the wild.
  Kept separate on purpose: a report that blurs the two is not trustworthy.

Grouped by **who owns the failure**, which is also how the self-correction loop
routes them (`../architecture/self-correction-loop.md`).

---

## 1. The model gets the physics or the syntax wrong

The central design bet is that the LLM never writes dictionary text — it emits a typed
`CaseIntent` and the renderer produces the files. These are the failures that survive
that bet, mostly through the escape hatches (`extra_files`, custom `fvSolution`, and,
in the harness, `write_case_file`).

| # | Failure | Consequence | Guard |
|---|---|---|---|
| 1.1 | **[observed]** Model writes pre-v13 conventions: `application icoFoam;`, `transportProperties`, `turbulenceProperties` | case will not start | v13 reference + always-on prompt guideline + regression test (§4.1) |
| 1.2 | **[observed]** Custom `fvSolution` omits `maxIter` | GAMG/smoothSolver run to the 1000-iteration default; ~190 s per step on a 5M-cell mesh, hitting the timeout | `_cap_solver_maxiter()` injects bounded iterations — **~15× speedup** |
| 1.3 | **[observed]** `PCG` proposed for an asymmetric equation | solver rejects the matrix | `IntentValidator` enforces PBiCGStab/smoothSolver/GAMG for transport fields |
| 1.4 | **[observed]** Empty `constraints { }` block with no `type` | fatal v13 startup crash | `_check_extra_files()` rejects it |
| 1.5 | **[observed]** `alpha.*` boundary values outside [0, 1] | unphysical VoF initialisation | bounds check in `IntentValidator` |
| 1.6 | **[observed]** Nested path in a file-patch override | `NotADirectoryError` crash | nested-override rejection + path-traversal block |
| 1.7 | **[observed]** Invalid GAMG smoother/agglomerator keywords | solver dictionary error | keyword whitelist validation |
| 1.8 | **[observed]** BC dictionary entries dropped or mis-ordered (`p0`, `inletValue`) | wrong or invalid BC | extra-parameter preservation in `FieldBC` + renderer |
| 1.9 | **[observed]** Implausible physical numbers (viscosity, pressure, temperature) | silently wrong physics | envelope clamping, `nu` derived from species (`knowledge/envelopes.py`) |
| 1.10 | **[anticipated]** Model invents a boundary condition that does not exist in v13 | case fails to start | `bc_types.json` registry + intent validation |

**Pattern.** Where the LLM is confined to the typed intent, failures are rare; nearly
all of the above entered through free-text escape hatches. That is the strongest
empirical support for the IR + renderer architecture — and the reason the harness
(where `write_case_file` is available) needs stronger prompt-level guardrails than the
pipeline does.

## 2. The case is valid but the numerics diverge

The hardest class, and the one that cannot be fixed by validation alone.

| # | Failure | Consequence | Guard |
|---|---|---|---|
| 2.1 | **[observed]** RHVT diverged at the first solve | reference case would not run | **resolved** — the working config (`_w10_rhvt`, 10 ranks) runs 10 steps with all fields decaying; only `p` lags. Verified 2026-08-18, see below |
| 2.2 | **[observed]** Quiescent `U` initial field against a fast inlet (200 m/s nozzle) | step discontinuity → energy-equation NaN | `_initial_U()` seeds the internal field from the dominant inlet velocity |
| 2.3 | **[observed]** Under-relaxation too loose for compressible density | divergence | `rho 0.05` (supervisor's recipe) + transient compressible presets |
| 2.4 | **[observed]** Divergence only discovered after a long expensive run | wasted minutes per attempt | **Level C** bounded dry run: residual-slope prediction (rising ≥ +0.05/step → FAIL) before committing |
| 2.5 | **[observed]** Retry loop re-running an already-diverged solve | wasted compute | abort at the **first** non-finite residual (~110 s instead of a full serial loop) |

### 2.1 in detail — the reference case

The Ranque-Hilsch vortex tube is genuinely hard: compressible, transonic, strongly
swirling, 10.6M faces. Early attempts NaN'd at the first solve; two real causes were
found and fixed (2.2, 2.3).

**Verified working, 2026-08-18.** Re-running every preserved case in `temp/out/`
through the ladder found the configuration that runs: `temp/out/_w10_rhvt` on **10 MPI
ranks** completes 10 bounded steps in 131 s with **every field's residual decaying**
(`Ux` −0.10/step, `Uy` −0.15, `Uz` −0.16, `h` −0.15, `k` −0.17, `epsilon` −0.08).

`p` is the laggard — it falls to ~0.059 then drifts back to ~0.091 while the others
keep dropping. That is the slow convergence: momentum, energy and turbulence settle,
pressure does not. Level C correctly returns **WARN** rather than PASS.

Full residual table and the sweep of all nine cases:
[`../evidence/rhvt-residuals-2026-08-18.md`](../evidence/rhvt-residuals-2026-08-18.md).

Tuning the pressure solve to full convergence is deliberately **out of scope** — that is
per-geometry CFD work, and the supervisor adjusts solver settings himself as the end
user. The deliverable is case *generation*.

**Two process failures nearly lost this result:**

1. Our own Level A audit **failed** the working case (2.6d), so it was excluded from
   testing on the strength of a wrong verdict.
2. Case directories are **re-rendered in place** and no solver log was archived, so a
   working configuration was silently overwritten by the next attempt. **Closed:**
   both the harness tool and the graph's validate node now snapshot dictionaries,
   initial fields and log into `<case>/.runs/<timestamp>/` on every success (64 kB for
   this 374 MB case), pruned to the 10 most recent.

Separately, the machinery around divergence is proven: NaN is caught at the first bad
residual, the container killed immediately, the failure classified `SOLVER_RUNTIME`
rather than mislabelled, and the evidence routed back to `build_intent`.

### 2.6 Bugs found *by* this re-run

Re-running the reference case exercised paths the test suite did not, and exposed three
defects — all fixed on 2026-08-18, all with regression tests:

| # | Failure | Consequence | Fix |
|---|---|---|---|
| 2.6a | **[observed]** `run_bounded_dry_run` passed the `ValidationAgent` object where a callable was expected | `'ValidationAgent' object is not callable` — **Level C never worked from the harness at all** | pass a `_bounded_foamrun` closure, as the graph does |
| 2.6b | **[observed]** Level A treated a regex `boundaryField` entry (`"inlet_.*"`) as a literal patch name | **6 false FAILs** on a valid case; a correct case reported unusable | match mesh patches against regex entries; compare *effective coverage* across fields |
| 2.6c | **[observed]** A first-solve NaN killed before any `Time =` marker was logged | reported as "no Time steps — likely a dictionary/field error", sending the agent hunting through valid dictionaries instead of the numerics | detect non-finite residuals in the log and report divergence with the offending field |
| 2.6e | **[observed]** `get_case_status` matched only a pre-v13 `application` entry in controlDict | **every correctly generated v13 case reported `solver: unknown`** — our own tool carried the v9 convention we warn the model about | read the v13 `solver` entry first; still read `application`, but return a warning that v13 will not run the case |
| 2.6d | **[observed]** Level A failed `steadyState` ddt paired with a `PIMPLE` block | **condemned the one RHVT configuration that actually runs** — the working case was skipped because the gate said it was broken | verified the v13 `fluid` module accepts the pairing; downgraded to a warning. The opposite direction (transient ddt, SIMPLE-only) still fails, as evidenced |

**Coverage was the root cause, not bad luck.** An audit after these fixes found **7 of
the 15 harness tools had no test mention at all** (`diagnose_case_failure`,
`get_mesh_quality_metrics`, `get_tutorial_numerics`, `import_external_mesh`,
`inspect_mesh`, `read_postprocessing_data`, `search_openfoam_knowledge`) and four more
were mentioned only once or twice. Level C did not fail because it was hard — it failed
because nothing ever called it. Smoke sentinels now cover them.

Writing those sentinels surfaced a smaller design smell: **tool return shapes are
inconsistent**. Some return `success`, others return domain keys only
(`inspect_mesh` → `has_polyMesh`, `get_tutorial_numerics` → `has_schemes`,
`search_openfoam_knowledge` → `recommended_solver`). The model must guess which
convention applies. Not fixed here — changing shapes would ripple through the skill and
adapter — but it belongs on the list for the rebuild.

2.6e is the quiet one: it never errored, it just reported "unknown" forever — the v9→v13
drift the supervisor warned about, found inside our own tooling rather than in model
output.

2.6b–2.6d are the dangerous kind: each produces a *confident, wrong* diagnosis. 2.6b and
2.6d condemn good cases outright — 2.6d demonstrably did, hiding the working reference
result — and 2.6c misdirects the self-correction loop toward syntax when the problem is
physics. A false FAIL is more costly here than a false PASS: the ladder catches a bad
case downstream, but nothing catches a good case that was thrown away.

## 3. Infrastructure and execution

| # | Failure | Consequence | Guard |
|---|---|---|---|
| 3.1 | **[observed]** `proc.kill()` killed only the host `docker` CLI | container kept running in the daemon, burning CPU after timeout/abort | named containers + `docker rm -f` on every timeout/abort path |
| 3.2 | **[observed]** Blocking `stdout.readline()` on a silently stalled container | wall-clock timeout never fired — pipeline hung | background reader thread + `queue.Queue`, timeout enforced unconditionally |
| 3.3 | **[observed]** `checkMesh -allTopology -allGeometry` on a 10.6M-cell mesh | timeout overhead | optional, off by default (`--check-mesh-all`, `FOAM_CHECKMESH_ALL`) |
| 3.4 | **[observed]** Serial validation on huge meshes | ~20+ min per attempt | parallel `mpirun -parallel` → **~1.8 min** |
| 3.5 | **[observed]** `SIGFPE` / stack-trace crash not matched by the error parser | crash misclassified because only `--> FOAM FATAL ERROR` text was recognised | parser extended to stack-trace-style crashes |
| 3.6 | **[observed]** Docker absent or daemon down | everything below Level A fails | classified `ENVIRONMENT` → routed to `END`, never retried (retrying cannot help); setup treats it as a warning |

## 4. The agent's own instructions

Failures in the *guidance*, not the code. These are the most dangerous class because
everything keeps looking healthy.

| # | Failure | Consequence | Guard |
|---|---|---|---|
| 4.1 | **[observed]** Our own rules file and a diagnostic taught `application foamRun;` | the guidance meant to correct a v9-biased model reinforced the error | fixed; regression test fails if pre-v13 conventions reappear |
| 4.2 | **[observed]** Skill guidance is not guaranteed to be read | model silently falls back to v9 training data; produced `application foamRun;` and an invented `constant/physicsProperties` | critical rules also injected as always-on `promptGuidelines` |
| 4.3 | **[observed]** A literal example (`solver incompressibleFluid;`) gets copied into the wrong case type | wrong solver module for e.g. a dam break | guidance kept structural (`solver <module>;`); test asserts no hardcoded module |
| 4.4 | **[anticipated]** Broken `SKILL.md` symlink | pi drops the skill (no frontmatter) → agent loses its whole workflow **while still loading all 19 tools and looking fine** | `pi_sync_skill()` repairs on every setup; test covers it |
| 4.5 | **[anticipated]** Missing rules file | `load_agent_rules()` falls back silently to a one-line prompt | first thing to check if a deployed agent ignores its workflow |
| 4.6 | **[anticipated]** Nothing enforces tool *order* in the harness | model may validate before auditing, or skip diagnosis | accepted trade-off: the harness targets a strong model. The pipeline compiles the order into the graph precisely because a weak model cannot be trusted with it |

## 5. Anticipated: self-hosted model

No case has yet been generated with a locally hosted model — all runs used a hosted
API behind the same OpenAI-compatible client. Recorded as an assumption, not a result.

| # | Risk | Existing guard |
|---|---|---|
| 5.1 | Small model emits malformed JSON for the intent | `_extract_json` / `_extract_fenced_json` repair, then a retry, then a clean `intent_error` exit |
| 5.2 | Small model picks an implausible solver or BC | RAG grounding + `IntentValidator` + envelope clamping |
| 5.3 | Small model ignores the schema entirely | typed `CaseIntent` — a non-conforming answer is rejected, never rendered |
| 5.4 | Throughput too low for the retry loop | bounded retries; preflight kills doomed cases cheaply |

The architecture was designed for exactly this case (see
`../architecture/phase-ii-architecture.md` — the "Constraint Tax" finding). A single
Ollama smoke run would convert this section from anticipated to observed and is the
cheapest available strengthening of the report.

---

## How to add an entry

When something breaks, add a row **the same day** with: what happened, the
consequence, the fix, and where the guard now lives. Tag `[observed]`. Detail that is
too long for a row goes in a subsection, as with §2.1. Most of this document was
reconstructed afterwards from git history and the progress log, and the reconstruction
lost detail — recording as you go is cheaper.
