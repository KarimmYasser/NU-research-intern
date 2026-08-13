# Progress Log

Running log of concrete milestones on the `src/` architecture spike. Append new
entries at the top. See `src/README.md` for the code layout and
`docs/architecture/phase-ii-architecture.md` for the design this validates.

## 2026-08-13 — Velocity Seeding Fix for Compressible Solver Stability

**What changed:**
- **Velocity Seeding (`_initial_U` in `src/ir/intent.py`):** Fixed the step-discontinuity shock on high-speed compressible flows (RHVT h-NaN fix). Instead of initializing the internal field to quiescent `uniform (0 0 0)` against fast nozzle inlets (e.g., 200 m/s), `_initial_U(intent)` seeds the domain `U` internal field using the dominant inlet velocity.
- **Regression Testing:** Added `test_initial_U_seeded_from_dominant_inlet()` in `tests/test_intent.py` asserting that `U` internalField is correctly populated from dominant inlet velocity vectors.

## 2026-08-13 — Chunkless RAG: semantic case-family taxonomy + optional-file generation

**What changed this session (three linked capabilities so the agent handles
arbitrary / optional OpenFOAM files and reasons over case *hierarchy*, not
word matches):**

1. **`extra_files` map (IR → renderer).** `CaseIntent.extra_files` /
   `CaseSpec.extra_files` now carry arbitrary case-relative files
   (`system/fvConstraints`, `system/functions`, `constant/thermo/...`); the
   renderer writes them with subdir creation (`CaseFiles.extra`), the LLM schema
   exposes them, and `IntentValidator._check_extra_files` blocks path traversal
   while allowing any depth. Extra files write after the standard layers, so an
   agent can also ship a whole-file first-render override.

2. **`file_tree` in `v13_tutorials.json` (252 cases).** Each tutorial record now
   carries its case file tree (0/, constant/, system/), extracted from
   `temp/openfoam-repo/OpenFOAM-13/tutorials` by the new reproducible
   `src/knowledge/rebuild_tutorials.py`, plus a machine-written one-line
   `description`. The RAG deep path surfaces the *optional* files a reference
   case ships (functions, fvConstraints, setFieldsDict, residuals).

3. **Semantic case-family taxonomy (`case_families.json`) + re-ranked
   `retrieve_case_graph`.** 17 hand-described families (compressible
   internal/external, VOF, Euler multiphase, CHT, thermal-buoyant, rotating,
   particles, combustion, ...) group all tutorials via explicit overrides →
   longest include cue → category base (exclude veto). Retrieval walks
   **query → family (matched on the family's *description*, not case names) →
   member cases**, with family-aware solver selection and a `ground` block that
   explains *why* a case was retrieved and what it ships. Matching fixes: stop
   words, underscore ids ("thermal_buoyant" → buoyant), plural/prefix hits
   ("exchanger" → "exchangers"), primary-family dominance over keyword ties
   ("column" doesn't beat an Euler bubble column).

**Verified:** vortex-tube prompts (no literal v13 tutorial) resolve to
`flow_compressible_internal` + shockTube-style cases; "mach 2 airfoil" →
compressible external; "bubble column" → Euler; "sloshing fuel tank" → VOF;
flat cavity prompts stay on the cheap path. Full suite **156 passed**.

## 2026-08-13 — Developer reference split into per-concern docs (`docs/development/`)

**What changed:** the monolithic `docs/guides/developer-guide.md` (~420 lines) is
now a short entry page pointing into a new **split-by-concern reference** under
`docs/development/` (~1,270 lines, 9 docs), so a developer reads only the doc for
their concern:

- `README.md` — index + "which doc do you need?" table + full module map
- `overview.md` — architecture + end-to-end data flow in one pass
- `ir-and-rendering.md` — `CaseIntent`/`CaseSpec`, the 18-family `case_types.py`
  registry, `intent_to_spec()` internals, renderer + preset pairing
- `orchestration.md` — LangGraph nodes, routing tables, LLM extraction stages
- `knowledge-layer.md` — JSON registries, RAG, `IntentValidator`, physical
  envelopes, skills
- `validation-and-preflight.md` — Docker ladder, `FailureClass` routing,
  NaN/parallel ladder, preflight Levels A/B/C
- `config-and-llm.md` — LLM client, env vars, mesh I/O, CLI
- `extending.md` — concrete recipes (new solver family, species, BC, envelope,
  validation check)
- `testing-and-conventions.md` — suite map + conventions/gotchas

`docs/README.md` and `CLAUDE.md` index the new folder. All cross-links verified;
full suite still **141 passed**. Committed as `abfd910`.

## 2026-08-13 — Deterministic physical-envelope constraint layer ("a range of numbers, not model experience")

**What changed this session (new `src/knowledge/envelopes.py` + registry):**

- **New `physical_envelopes.json` registry:** curated, physically-plausible
  ranges for every number the LLM can emit — `nu`, `p_ambient`, `temperature`,
  `velocity`, `pressure`, `box_size`, `cells`, `surface_tension` — each with
  min/max, a *clamp* band, and a *reject* band, plus a `rationale` string
  (which fluid/regime each bound comes from, so the values are defensible to
  the supervisor rather than arbitrary).

- **New `EnvelopeConstraint` (`envelopes.py`, zero Docker, pure registry +
  arithmetic):** runs in `extract_intent` as **Stage 1.5**, between Pydantic
  parsing and the IntentValidator. Three-way deterministic decision per value:
  - inside the envelope → passed through untouched;
  - modestly outside (within the clamp band) → **clamped** to the nearest bound
    and a machine-readable note printed — the pipeline does *not* trust the
    model's single guess;
  - outside the reject band (a units/dimension slip-up, e.g. `nu=1e5`) → an
    `IntentValidationError` fed back into the existing LLM retry loop with the
    valid range, mirroring IntentValidator feedback.
  Velocity vectors are rescaled **direction-preserving** (magnitude clamped,
  components scaled), so clamping never bends a prescribed flow angle.

- **Species-derived `nu` envelope (determined, not hard-coded):** when
  `fluid_species` is named, the `nu` envelope is computed from the species'
  dynamic viscosity and molecular weight in `thermophysics.json` via
  ideal-gas `rho = p·Mw/(R·T)` at the case's p_ambient/temperature, then a
  decade band around that reference (`nu_ref/10 .. nu_ref·10`, reject beyond
  ±2 decades). Verified numerically: air at 1 atm / 300 K derives nu_ref
  ≈ 1.58e-5 m²/s. So `nu=1e-5` (air's true value) passes with zero notes,
  while the same `nu=0.5` that the *static* envelope would have clamped to
  0.1 is now **rejected** because it is ~4 decades above the real fluid.
  Unknown species and all case-parameter fields (temperature, pressure,
  box_size, cells, velocity) fall back to the curated static ranges. This
  directly answers "hard-coded or determined?": fluid-property bounds are
  determined from data; case-regime bounds stay curated constants.

**Why this matters:** the LLM (a small local model) already passes structural
checks, but nothing bounded its raw numbers — `nu=0.5` (a cP-style slip) or
`temperature=2e5` would have reached the renderer as-is. Now the model only
chooses *within* a physically plausible envelope; anything outside is
deterministically corrected or rejected.

- **Tests:** `tests/test_envelopes.py` — 16 cases (in-range pass-through,
  nu/temperature/velocity/cells clamping, direction-preserving velocity
  rescale, zero-velocity no-op, reject-band unit errors carry the
  `IntentValidationError` shape, plus air-derived-envelope acceptance,
  out-of-band rejection, unknown-species static fallback, and T-dependence
  of the derived band). Full suite: **141 passed** (was 137).

## 2026-08-13 — Preflight gate (Levels A/B/C): static audit + checkMesh metrics + dry-run divergence prediction

**What changed this session (new `src/preflight/` package + graph wiring):**

- **New `preflight_case` node in the LangGraph**, inserted between `render_case`
  and `validate_case`. A preflight FAIL routes back to `build_intent` (with the
  audit/dry-run evidence as `error_feedback`) *before* the full Docker ladder is
  ever run — so a known-bad case never pays the cost of a minutes-long
  `foamRun`. New `route_after_preflight` respects `max_attempts` and defers to
  the ladder when Docker is unavailable (env skip, not a case failure).

- **Level A — static dictionary audit (`audit.py`, zero Docker):** inspects the
  rendered 0/, system/, constant/ files and flags config-level defects mapped to
  known failure classes:
  - missing `rho` field relaxation for compressible transient flow (the root
    cause of the RHVT first-solve NaN; supervisor recipe `rho 0.05`)
  - missing energy-field solver entry for compressible `fluid`
    ("keyword h is undefined" DICTIONARY_SYNTAX root cause)
  - missing PIMPLE/SIMPLE loop block, missing/non-`none` ddtSchemes default
  - non-positive internalFields (p/T/k/epsilon/...), endTime ≤ startTime, deltaT ≤ 0
  - patch-coverage: every mesh boundary patch must appear in every 0/ field
    (catches the patchNotInMesh read-error class) + cross-field patch-set consistency

- **Level B — checkMesh metrics parser (`metrics.py`):** parses quantitative
  mesh quality (cells, non-orthogonality max/avg, skewness, aspect ratio, cell
  volumes, `Failed N mesh checks`) out of checkMesh stdout and scores each
  against OpenFOAM's own warn thresholds (non-ortho > 70, skewness > 4).

- **Level C — bounded dry-run + residual-slope predictor (`dry_run.py`):**
  patches controlDict to a few steps and runs the solver; `predict_from_residuals`
  then classifies each field's trajectory by the **least-squares slope of
  log10(initial residual) vs step index** — the right signal because residuals
  decay exponentially:
  - slope ≥ +0.05/step → **FAIL** (rising → divergence predicted)
  - slope ≤ −0.05/step → **PASS** (decaying) — a single noisy uptick no longer
    flips a genuinely downward run to FAIL
  - flat/stalled between the bands → **WARN** (not converging, but not diverging)
  - residual already at machine floor (≤ 1e-6) → PASS regardless of slope
  - any nan/inf anywhere, or zero captured steps → **FAIL**
  Rule of thumb implemented: *no NaN + downward trend = pass*.

- **`ofdict.py`:** tolerant OpenFOAM dictionary helpers (strip_comments,
  section-by-name with brace matching, scalar `entries()`, top-level `keys()`,
  `boundary_fields()` for per-patch BCs) — deliberately not a full parser; the
  audit only needs stable, well-formed sections, even after an LLM patch.

**Test suite:** 125 tests, all passing (was 105). New tests cover: audit
positivity/ρ-relaxation/energy-solver/patch-coverage findings, checkMesh metric
parsing (good + bad + empty), predictor trajectories (diverging / decaying /
NaN / no-steps / stalled-flat / noisy-uptick / machine-floor), preflight graph
routing, and the `preflight_case` node feeding evidence back on FAIL.

**Status:** preflight gate wired into the graph but not yet exercised against a
live Docker run. The RHVT divergence (first-solve NaN) should now be caught by
Level C's dry run in ~a few steps instead of the full ladder; Level A would have
flagged the missing-ρ-relaxation defect statically. Live Docker verification is
the next step.

---

## 2026-08-13 — Validation ladder: parallel solvers, NaN detection, early abort

**What changed this session (all `src/validation/validation_agent.py` unless noted):**

- **Parallel validation ladder:** `run_solver` now decomposes and runs `foamRun -parallel`
  instead of always serial. `_run_solver_parallel()` auto-generates a scotch
  `system/decomposeParDict` (canonical v13 header + `object` block, rewritten every run to
  match `nprocs`), runs `decomposePar -force`, then `mpirun --allow-run-as-root -np N
  foamRun -parallel`. `DEFAULT_PROCS` reads `FOAM_VALIDATION_PROCS` (default 1 = unchanged
  serial behavior). This cut RHVT validation from ~20+ min to ~1.8 min on a 10.6M-cell mesh.

- **NaN/divergence classified as `SOLVER_RUNTIME`:** previously a timeout with partial solver
  output was mislabeled `ENVIRONMENT` (dead-ending the graph). Two fixes:
  1. `_docker_run()` now retains `stdout+stderr` in the returned `output` even when the run
     times out, so evidence is never discarded.
  2. `parse_errors()` detects `nan`/`inf` residuals in solver log lines (e.g.
     `GAMG: Solving for p, Initial residual = ..., Final residual = nan`) and classifies the
     failure `SOLVER_RUNTIME` → routes back to `build_intent` for a physics retry instead of
     `END`.

- **Early NaN abort:** the container is killed at the *first* non-finite residual (via a
  `reason=` callback into `_docker_run`), instead of burning the full `FOAM_VALIDATION_TIMEOUT`
  on a run that is already diverged. Verified in the loop test suite.

- **`checkMesh` smoke trap:** `run_ladder` runs `checkMesh` (cached) and `decomposePar` before
  the solver rung; errors there are pulled into the ladder result so a missing/bad `polyMesh`
  is caught early rather than surfacing as a mysterious `foamRun` failure.

- **Breakage fixes surfaced by the live pipeline run:**
  - `apply_file_overrides` crashed with `NotADirectoryError` when the LLM emitted nested patch
    keys like `system/fvSolution/solvers` — now skipped+logged (regression test added).
  - Intent validator now enforces the energy-field solver entry (`h`/`he`/`e` or a regex) for
    the compressible `fluid` family's custom fvSolution — this was the root of the "keyword h
    is undefined" DICTIONARY_SYNTAX failure; also fixed a latent bug where `flow_type=
    compressible` never resolved to `case_type=fluid`, silently disabling the PIMPLE + energy
    checks.

**Test suite:** 105 tests, all passing. New tests for NaN classification, partial-output
retention on timeout, nested-override rejection, and the energy-solver enforcement.

**Live run (RHVT, `FOAM_VALIDATION_PROCS=10`):** `box_incomp` verified end-to-end with 8 procs
(parallel `foamRun` completes, mesh preserved). RHVT pipeline run demonstrated the corrected
loop: parallel `foamRun(-parallel)` → NaN → `SOLVER_RUNTIME` → routed back to `build_intent`
with the NaN evidence; each retry now aborts at first NaN (~110 s) instead of serial looping.
RHVT still diverges to NaN at the first solve (`Time = 0.0002`) — the retry mechanics are
sound; the underlying physics setup (initial fields / wall functions / numerics) still needs a
resolution pass.

---

## 2026-08-12 — Generalization, file-patching node, and architecture bug-triage

**What changed this session:**

- **Generalization (multi-species thermophysics):** Added `fluid_species` field to `CaseIntent`
  (and to `_SCHEMA`/`_RESPONSE_FORMAT` in `pipeline.py` so the LLM can actually emit it).
  `intent_to_spec()` resolves species tokens to thermophysical constants (Cp, μ, Pr, Mw) via
  `KnowledgeRetriever.get_species_thermo()`. Species library: air (default), N₂, CO₂, O₂, He,
  H₂O vapour in `knowledge/thermophysics.json`.

- **`patch_files` node (LangGraph):** New graph node for `DICTIONARY_SYNTAX` failures. Instead
  of discarding a good intent and re-running the full pipeline, the node reads the exact failing
  file(s) and calls `extract_file_patches()` — a separate, targeted LLM call that returns
  `{"file_overrides": {"path": "corrected content"}}`. These overrides are then applied by the
  next `render_case` call. Routing: `DICTIONARY_SYNTAX → patch_files`, `ENVIRONMENT → END`,
  `MESH_*/SOLVER_* → build_intent`.

- **`extract_file_patches()` in pipeline.py:** New function with its own `_PATCH_SYSTEM` prompt
  and `_PATCH_RESPONSE_FORMAT` JSON schema. Returns `{}` on failure (non-raising) so the graph
  can fall back gracefully.

- **Stale `file_overrides` fix:** `build_intent` now explicitly returns `file_overrides: None`
  to clear patches from a previous attempt before a fresh full render.

- **`KnowledgeRetriever` singleton:** Replaced two fresh `KnowledgeRetriever()` instantiations
  per `intent_to_spec()` call with a module-level `_RETRIEVER` singleton, eliminating repeated
  disk reads during retries.

- **`generate()` deprecated:** Marked with `DeprecationWarning`; `orchestration.graph.run()` is
  the correct entry point for production use.

- **Docs updated:** `agent-usage-guide.md` rewritten; `self-correction-loop.md` routing table
  updated to reflect `patch_files` node; `findings-2026-08-12.md` written as a full session
  findings log.

**Test suite:** 41 tests, all passing (0.60 s). New tests added for the `patch_files` routing,
`ENVIRONMENT → END` stop, stale-override clearing, species thermo resolution, and file override
application.

**Status:** End-to-end run not re-attempted this session (Docker validation pending). The
`alphatJayatillekeWallFunction` failure from the previous session should now be resolved by
the `patch_files` node on the next live run.

---

## 2026-08-03 — First real end-to-end run against the mentor's RHVT mesh

**What changed this session:**

- Added a real (non-mocked) LLM backend option: the **Siemens SDC LLM Gateway**
  (`https://llm.sdc.siemens.cloud`), an OpenAI-compatible cloud endpoint used
  for dev/testing only — the eventual deliverable still targets the isolated,
  local-only Ollama VM per the mentor's mandate. Config lives in `.env`
  (gitignored; see `.env.example`), read by `src/agents/llm_client.py` via
  `python-dotenv`. Auth uses a custom `x-api-key` header (not the OpenAI SDK's
  default `Authorization: Bearer`).
- Landed on `gpt-5-mini` as the model: Claude/Gemini/Bedrock models on this
  gateway only work through Anthropic-native/other endpoints, not the
  OpenAI-compatible `/v1/chat/completions` path our `openai`-SDK client uses.
- `gpt-5-mini` (a reasoning model) rejects an explicit `temperature=0`.
  `pipeline.py` now has `_chat_create()`, which tries a few progressively
  degraded parameter sets (with/without `temperature`, with/without
  `response_format=json_schema`) until one is accepted.
- Added a CLI entry point, `src/agents/__main__.py` (`python -m agents run
  ...`) — previously the pipeline could only be exercised from tests.
- Verified Docker Desktop (v29.6.2) and pulled `microfluidica/openfoam:13`.

**The run:** a real prompt describing a Ranque-Hilsch vortex tube (RHVT), NOT
a canned test, against the mentor-provided mesh
`temp/provided-mesh/3D RHVT (OpenFOAM Files)/` (~370 MB, ~10.6M faces,
patches `inlet_1..inlet_6`, `cold_outlet`, `hot_outlet`, `walls`).

**Result, stage by stage:**

| Stage | Result | Notes |
|---|---|---|
| `read_mesh` | ✅ pass | parsed only the small `boundary` file for patch names/types |
| `build_intent` (LLM) | ✅ pass | `gpt-5-mini` returned a valid `CaseIntent` reusing the real patch names |
| `expand_spec` + `render_case` | ✅ pass | deterministic; wrote `0/`, `system/`, `constant/` incl. a byte-copy of the real `polyMesh/` |
| `checkMesh` (Docker, real v13) | ✅ pass | the real 10.6M-face mesh checked out |
| `foamRun` (Docker, real v13) | ❌ fail | **numerical divergence**, not an environment/rendering bug (see below) |

**Root cause of the `foamRun` failure:** the solver crashed with a `SIGFPE`
(floating-point exception) inside `GaussSeidelSmoother` while solving the
momentum matrix, one timestep in (`Time = 0.005s`). The log showed
`Courant Number mean: 0.186601 max: 1252.13` — a max Courant number that high
means `deltaT` is far too large relative to the local cell size somewhere in
the mesh (likely near the small tangential inlet nozzles) given the 50 m/s
inlet velocity. The rendered `system/controlDict` used a **fixed** `deltaT`
(no `adjustTimeStep`/`maxCo`), so nothing throttled the timestep down. This is
a case-setup/physics issue, correctly classified `SOLVER_RUNTIME` — the mesh,
file rendering, and Docker plumbing are all working correctly.

**Bug found (not yet fixed):** `ValidationAgent.parse_errors()` only
recognizes OpenFOAM's `--> FOAM FATAL [IO ]ERROR` text block. A SIGFPE crash
produces a raw stack trace instead, so the parser fell through to its
fallback and surfaced only the first 6 lines of the *whole* output (the
banner), hiding the actual Courant-number/SIGFPE detail. Needs a second
pattern for stack-trace-style crashes.

**Other loose ends:**
- `LLMConfig.base_url`/`.model` read `os.environ.get(...)` as plain
  dataclass-field defaults (evaluated once at class-definition time), unlike
  `.api_key` which uses `field(default_factory=...)`. Editing `.env` won't
  affect an already-running process — restart it. Should be made consistent.
- `anthropic` Python package got installed mid-session while briefly
  evaluating Claude, then unused after switching to GPT. Harmless, but no code
  depends on it.
- Docker Desktop's install doesn't always propagate to `PATH` in new
  PowerShell terminals spawned by tooling in this environment — refresh with:
  `$env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")`

**Next steps:**
1. Fix `parse_errors()` to recognize SIGFPE/stack-trace crashes so the retry
   loop (and humans) can see the real error instead of the banner.
2. Have the renderer/intent default to `adjustTimeStep yes;` + a `maxCo` cap
   whenever the inlet velocity is high relative to mesh scale, so a retry has
   a real chance of getting past the first timestep.
3. Re-run with `--max-attempts 2` (or 3) once the above are in so the
   self-correction loop actually sees the real error and can act on it.
