# Progress Log

Running log of concrete milestones on the `src/` architecture spike. Append new
entries at the top. See `src/README.md` for the code layout and
`docs/architecture/phase-ii-architecture.md` for the design this validates.

## 2026-08-14 — Solver Performance Bounds, Docker Daemon Cleanup & BC Registry Expansion

**Focus:** Large-mesh linear solver iteration bounding (`maxIter`), explicit Docker container lifetime cleanup, `totalTemperature` support, and `system/fvConstraints` validation.

### 1. Linear Solver `maxIter` Capping (`src/rendering/renderer.py`)
- **Root Cause Addressed:** Custom `fvSolution` emitted by the LLM frequently omitted `maxIter`, causing `GAMG` and `smoothSolver` on $p$ and $h$ to iterate up to the default 1,000 iterations per step on massive 5M-cell meshes (~190s per time step, hitting timeouts).
- **Fix:** Implemented `_cap_solver_maxiter()` in `render_fvsolution()` to automatically inject bounded iterations (`maxIter 30` for `GAMG`, `maxIter 50` for `smoothSolver`) when omitted, achieving a **$15\times$ speedup** (~10–13s per time step on 8 cores).

### 2. Docker Daemon Container Cleanup (`src/validation/validation_agent.py`)
- **Root Cause Addressed:** On timeout or NaN abort, `proc.kill()` only terminated the host `docker` CLI process, leaving the container running inside the Docker daemon in the background and burning CPU.
- **Fix:** Assigned unique container names (`--name foam_run_<id>`) and implemented explicit daemon-level cleanup via `_cleanup_container()` (`docker rm -f <id>`) on all timeout and abort paths.

### 3. Knowledge Registry & BC Validation (`src/knowledge/bc_types.json`, `src/knowledge/intent_validator.py`)
- **`totalTemperature` Support:** Added complete `totalTemperature` boundary condition specification (`T0`, `gamma`, `psi`, `phi`, `U`) to `bc_types.json` for compressible gas inlets.
- **`fvConstraints` Syntax Guardrail:** Added validation in `IntentValidator._check_extra_files()` to reject empty `constraints { }` blocks that lack a `type` declaration, preventing fatal OpenFOAM v13 startup crashes.

### 4. Test Suite Status
- Added registry and configuration unit tests across `tests/test_knowledge_registries.py` and `tests/test_llm_config.py`.
- Full repository test suite passed cleanly: **202 passed in 2.71s**.

## 2026-08-14 — Evidence-Backed Safe Exit, OpenFOAM Foundation v13 Capability Alignment & Code Quality

**Focus:** Safe exit anti-laziness guardrails, OpenFOAM Foundation v13 diagnostic tool integration, graph ledger tracking, CLI exit-code contract, and full codebase dead-code and quality audit.

### 1. Structured Safe Exit IR & Verifiable Evidence Contract (`src/ir/safe_exit.py`)
- **SafeExitCategory:** Defined 4 deterministic categories: `CORRUPT_OR_UNCLOSED_MESH`, `CONTRADICTORY_PHYSICS`, `MISSING_ESSENTIAL_DATA`, `UNSUPPORTED_PHYSICS`.
- **SafeExitEvidence:** Required every claim to point to an authoritative source (`solver_log`, `check_mesh`, `user_prompt`, `case_file`) with exact, verifiable normalized substrings and case-relative path isolation.
- **RemediationAttempt & SafeExitValidationResult:** Structured remediation history tracking (`attempt`, `issue_class`, `action`, `configuration_changes`, `outcome`) and formal validation results (`is_valid`, `rejection_reasons`, `verified_evidence`).

### 2. Deterministic Anti-Laziness Guardrails (`src/knowledge/intent_validator.py`)
- **Strict Evidence Verification:** Validated evidence references against recorded remediation history, raw user prompts, solver stdout/stderr logs, checkMesh logs, and numeric preflight metrics.
- **Anti-Laziness Filter Rules:** Rejected recoverable parameters from triggering safe exits (e.g. Courant/deltaT warnings, solver relaxation factors, patch typos, non-orthogonality/skewness quality warnings). Only non-positive cell volumes (`min_volume <= 0`) or fatal unclosed topology qualify for mesh exits.
- **Contradictory Physics:** Required $\ge 2$ distinct quotes demonstrating mutually exclusive requirements.
- **Unsupported Physics:** Grounded against the `CASE_TYPES` registry.

### 3. Graph Routing & CLI Contract (`src/orchestration/graph.py`, `src/__main__.py`)
- **Deterministic Ledger:** `CaseGraphState` tracks `remediation_history`, `safe_exit_validation`, and retry counters.
- **Bypass & Feedback:** Positively validated safe exits bypass downstream rendering and route to `END` with **exit code 2** (prerequisites unmet) alongside a diagnostic banner. Invalid safe exits feed rejection diagnostics back to the LLM until retry budget exhaustion (**exit code 1**).
- **Public API:** Added `run_pipeline(prompt, ...) -> PipelineResult`.

### 4. Upstream OpenFOAM Foundation v13 Tooling & Configurable Flags
- **Configurable `checkMesh` Flags (`src/validation/validation_agent.py`, `src/__main__.py`):** Made `-allTopology -allGeometry -meshQuality` optional (defaulting to standard fast `checkMesh`) to prevent timeout overhead on large meshes (e.g. multi-million cell 3D cases). Added `--check-mesh-all` CLI flag and `FOAM_CHECKMESH_ALL` environment variable.
- **`foamDictionary` & `foamListTimes` Helpers:** Containerized helpers `read_dictionary_entry` and `list_times` for robust syntax assertions and time progression verification.
- **Three-State Capability Model:** Documented `upstream_available` vs `pipeline_supported` vs `request_supported` in `docs/architecture/openfoam-v13-capabilities.md`.
- **Environment Template (`.env.example`):** Fully documented all runtime environment variables (`LOCAL_LLM_BASE_URL`, `LOCAL_LLM_MODEL`, `LLM_GATEWAY_API_KEY`, `FOAM_DOCKER_IMAGE`, `FOAM_VALIDATION_TIMEOUT`, `FOAM_VALIDATION_PROCS`, `FOAM_CHECKMESH_ALL`).

### 5. Code Quality & Dead Code Cleanup
- Cleaned unused variable assignments, unreferenced imports, and shadowed loop variables.
- Annotated class variables with `typing.ClassVar` and modernized union and enum type declarations.
- Verified test suite: **184 unit and integration tests passing in 1.27s**.

## 2026-08-14 — Pipeline Hardening, Preflight Box Fallback & CLI Flags

**Focus:** End-to-end pipeline robustness, non-blocking timeout handling in Docker, static preflight coverage for box cases, CLI execution controls, and dictionary header deduplication.

### 1. Non-Blocking Validation Timeout & MPI Flags (`validation/validation_agent.py`)
- **Root Cause Addressed:** `_docker_run` previously read process output using blocking `proc.stdout.readline()`. If a container or solver process stalled silently without emitting output, the timeout check was never reached.
- **Fix:** Switched to a background reader thread with a `queue.Queue`, allowing the main thread to sleep in short steps and unconditionally enforce the wall-clock timeout.
- **Docker Process Handling:** Added `--init` to `docker run` to ensure signals (SIGTERM/SIGKILL) propagate cleanly to child solver processes inside the container.
- **OpenMPI Oversubscribe:** Added `--oversubscribe` to `mpirun` in `_run_solver_parallel` to avoid slot allocation failures on systems where `nprocs` exceeds available Docker core assignments.

### 2. Preflight Patch Coverage for Box Cases (`preflight/audit.py`)
- **Enhancement:** Added `_blockmesh_patch_names` and fallback support to `_check_patch_coverage`. When `constant/polyMesh/boundary` is absent (standard for box cases prior to `blockMesh`), the audit parses `system/blockMeshDict` boundary patch definitions to verify field patch consistency before Docker runs.

### 3. Custom Dictionary Header Sanitization (`rendering/renderer.py`)
- **Enhancement:** Added `_sanitize_dict_body()` to strip duplicate `FoamFile` blocks, OpenFOAM banners, and separator lines from `custom_fv_schemes` and `custom_fv_solution` before wrapping them with canonical headers and footers.

### 4. CLI Execution Flags (`src/__main__.py`, `orchestration/graph.py`)
- **CLI Options:** Added `--procs`, `--timeout`, and `--image` flags to `python -m src run`, propagating them through `CaseGraphState` to configure `ValidationAgent` in both preflight and validation steps.

### 5. Physics & Numerical Stability Enhancements (`ir/intent.py`)
- **Compressible Outlet Backflow Protection:** Defaulted compressible outlet boundary conditions for velocity (`U`) and temperature (`T`) to `inletOutlet` with backflow guards (`inletValue uniform (0 0 0)` for `U` and `inletValue uniform T_ambient` for `T`), preventing unphysical reverse-flow numerical instability.
- **Velocity-Scaled Starting Timestep on External Meshes:** Added `_external_mesh_delta_t` which scales the initial `deltaT` inversely with peak prescribed velocity ($dt \sim 0.2 \cdot dx_{est} / V_{max}$), ensuring high-speed compressible cases (e.g. 50-200 m/s nozzle flows) start with sub-Courant timesteps ($10^{-6}$–$10^{-5}$ s) while `adjustTimeStep yes;` and `maxCo 0.5` ramp the step up dynamically.

### 6. Test Suite Status
- Added unit tests across `tests/test_validation.py`, `tests/test_preflight.py`, `tests/test_renderer.py`, and `tests/test_intent.py`.
- Full repository test suite passed cleanly: **172 passed in 1.21s**.

## 2026-08-13 — Pipeline Accuracy & Reliability Boosters

**What changed:**
- **Courant Static Guardrail (`_check_courant_number` in `src/preflight/audit.py`):** Added static Courant number estimation ($Co = V_{\max} \Delta t / h$) in Preflight Level A. Unphysical timesteps on high-speed flows (e.g. `deltaT = 1.0 s` on 200 m/s flow) are caught in 1 ms before running Docker, providing instant feedback to correct `deltaT <= 1e-4s`.
- **Automated `setFieldsDict` Phase Initialization (`render_setfieldsdict` in `src/rendering/renderer.py`):** Automatically renders `system/setFieldsDict` for VoF cases (`incompressibleVoF`, `compressibleVoF`, etc.), ensuring phase fractions (`alpha.water`) start initialized.
- **Multiphase Alpha Field Validation (`src/knowledge/intent_validator.py`):** Added validation enforcing physical bounds $[0, 1]$ on `alpha.*` boundary override values.
- **Test Suite:** Added `tests/test_accuracy_harness.py`. All 165 tests pass cleanly.

## 2026-08-13 — Roadmap Enhancements: cyclicAMI support, GAMG validator checks & RAG family tests

**What changed:**
- **`cyclicAMI` Registry Support:** Added `cyclicAMI` to `src/knowledge/bc_types.json` and updated `render_field()` in `src/rendering/renderer.py` to default boundary values to `$internalField` for periodic patches (`cyclic`, `cyclicAMI`).
- **GAMG Smoother & Agglomerator Validation:** Expanded `IntentValidator._check_custom_fv_solution()` in `src/knowledge/intent_validator.py` to validate `GAMG` smoother keywords (`GaussSeidel`, `symGaussSeidel`, `DIC`, `DILU`, etc.) and agglomerators (`faceAreaPair`, `algebraicPair`, `pair`).
- **RAG & Skill Test Suite:** Added test coverage in `tests/test_rag_skills.py` for multiphase (`multiphase_vof`), CHT (`conjugate_heat_transfer`), and `cyclicAMI` mesh patch role enrichment.

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

---

### Session: 2026-08-14 — Safe Exit Protocol & Deterministic Evidence Validation Engine

**Goal:** Implement a verifiable Safe Exit escape hatch allowing the LLM agent to gracefully terminate with structured human intervention requests (exit code 2) when encountering genuinely unresolvable physical/topological blockers, while implementing strict deterministic anti-laziness guardrails to prevent escaping recoverable numerical/syntax errors.

**Key Deliverables & Architectural Contracts:**
1. **Safe Exit Data Models & Return Types (`src/ir/safe_exit.py`):**
   - `SafeExitCategory`: 4 strict unresolvable failure classes (`CORRUPT_OR_UNCLOSED_MESH`, `CONTRADICTORY_PHYSICS`, `MISSING_ESSENTIAL_DATA`, `UNSUPPORTED_PHYSICS`).
   - `SafeExitEvidence`: Strict provenance tracking (`source`: `user_prompt`, `check_mesh`, `solver_log`, `case_file`).
   - `SafeExitReport`: Structured terminal report requiring `category`, `error_summary`, `evidence`, `exhausted_alternatives`, `user_action_required`, and optional `remediation_attempt_ids`.
   - `RemediationAttempt`: Authoritative execution ledger tracking issue class, action, configuration changes, outcome, and evidence reference.
   - `SafeExitValidationResult`: Deterministic verification contract returning `is_valid`, `rejection_reasons`, `verified_evidence`, and `validated_at_attempt`.
   - `PipelineResult`: Programmatic CLI and API terminal contract returning `success`, `exit_code`, `safe_exit`, `safe_exit_validation`, `remediation_history`, etc.
2. **Deterministic Evidence Validator (`src/knowledge/intent_validator.py`):**
   - Anti-laziness filters rejecting Courant, timestep, under-relaxation, and scheme stiffness excuses.
   - Path-traversal protection on `case_file` citations enforcing containment within `case_dir`.
   - Exact normalized substring verification against trusted execution state (solver stdout/stderr, checkMesh logs, preflight findings, raw user prompt).
   - Fatal topology filtering on `CORRUPT_OR_UNCLOSED_MESH`: requires fatal defects (negative cell volume, zero-area faces, unclosed boundary, non-manifold); rejects non-orthogonality/skewness quality warnings.
   - Grounded capability validation on `UNSUPPORTED_PHYSICS` against `CASE_TYPES` registry.
   - Multi-point evidence requirement on `CONTRADICTORY_PHYSICS` ($\ge 2$ distinct quotes).
3. **Graph Orchestration & CLI Contract (`src/orchestration/graph.py`, `src/__main__.py`):**
   - Graph state tracking for `remediation_history`, `safe_exit_validation`, and `invalid_safe_exit_count`.
   - Positive validation bypasses downstream execution and routes to `END`.
   - Rejected safe exits consume retry budget with targeted diagnostic feedback, falling back to exit code 1 if budget exhausted.
   - CLI prints high-visibility diagnostic banner and terminates with exit code 2 on valid safe exits.

