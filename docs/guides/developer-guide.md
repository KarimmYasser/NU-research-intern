# Developer Guide — Extending the OpenFOAM Case-Generation Pipeline

This guide is for people who want to **understand and change the code** in `src/`.
If you just want to *use* the agent as an engineer, read
[`engineer-user-guide.md`](engineer-user-guide.md) instead — this file assumes
you are comfortable with Python, Pydantic models, and LangGraph.

> **Status first.** `src/` is a deliberately scoped **architecture spike**,
> not the production deliverable. It proves the Phase II design end-to-end
> against a local LLM + Docker. Expect pragmatic shortcuts. The authoritative
> engineering plan is [`../architecture/phase-ii-architecture.md`](../architecture/phase-ii-architecture.md).

---

## 1. The core idea: "reason free, constrain late"

The pipeline's central bet: **the LLM never writes OpenFOAM dictionary syntax.**
It only emits a small, typed *physics intent* (a `CaseIntent`), and deterministic
code turns that into guaranteed-valid v13 files. This is what makes a **weak,
local** model viable:

```
NL prompt ──LLM──▶ CaseIntent ──deterministic──▶ CaseSpec ──renderer──▶ files
                    (compact)      intent_to_spec()      render()      │
                                                                    ▼
                                              Docker v13 ladder (blockMesh /
                                              checkMesh / foamRun) + self-correction
```

- **`CaseIntent`** (`src/ir/intent.py`) — the small thing the model can get right:
  regime, geometry, patch roles, a few numbers. Validated by Pydantic.
- **`CaseSpec`** (`src/ir/spec.py`) — the complete structural description: every
  field, dimension, boundary condition, property dictionary, and numerics preset.
- **`intent_to_spec()`** (`src/ir/intent.py:503`) — deterministic expander; fills
  in every mechanical detail (fields a solver needs, dimensions, wall functions,
  turbulence estimates, timestep sizing).
- **`render()`** (`src/rendering/renderer.py:966`) — deterministic `CaseSpec → files`.

Two more architectural anchors:

1. **The case-type registry** (`src/ir/case_types.py`) — one `CaseTypeDef` per
   solver family. This is where "18 solver families are supported" comes from,
   and it is the main extension point (see §7).
2. **The validation ladder** (`src/validation/validation_agent.py`) — the case is
   judged by *actually running the real solver* in Docker, and failures are
   classified and routed back to whichever stage owns them (see §6).

---

## 2. Repository layout

```
src/
├── __main__.py          CLI entry point (`python -m src run ...`)
├── ir/
│   ├── case_types.py    THE case-type registry (18 solver families)
│   ├── intent.py        CaseIntent + deterministic intent_to_spec()
│   └── spec.py          CaseSpec + all sub-models (Field, Patch, FieldBC, ...)
├── rendering/
│   ├── case.py          CaseFiles in-memory container + apply_file_overrides()
│   └── renderer.py      per-dictionary renderers + top-level render()
├── mesh/
│   └── mesh_io.py       external polyMesh reading: boundary parse + copy
├── preflight/
│   ├── audit.py         Level A: static dictionary audit (no Docker)
│   ├── metrics.py       Level B: parsed checkMesh mesh-quality metrics
│   ├── dry_run.py       Level C: residual-slope divergence predictor
│   ├── runner.py        run_preflight_with_agent(): A→B→C orchestration
│   └── __init__.py      PreflightResult / PreflightVerdict / run_preflight
├── knowledge/
│   ├── rag.py           zero-dep keyword-overlap RAG over the JSON registries
│   ├── skills.py        deterministic "skills" that enrich a CaseIntent
│   ├── intent_validator.py  semantic checks before the LLM's JSON is trusted
│   ├── envelopes.py     numeric physical-envelope constraint (Stage 1.5)
│   └── *.json           solvers / boundary_conditions / thermophysics /
│                        turbulence_models / bc_types / v13_tutorials /
│                        physical_envelopes
├── llm/
│   └── llm_client.py    OpenAI-compatible client (Ollama / vLLM / gateway)
├── validation/
│   └── validation_agent.py  Docker ladder (serial or mpirun -parallel) +
│                            error parsing/classification, NaN abort
└── orchestration/
    ├── graph.py         LangGraph StateGraph (the CURRENT primary pipeline)
    └── pipeline.py      extract_intent / extract_file_patches / generate (deprecated)
tests/                   pytest suite (currently 141 tests, run offline in ~1s)
```

`knowledge/*.json` are **data, not code** — the RAG engine, intent validator, and
skills all read from them. They are the cheapest place to make the agent "know"
more.

---

## 3. The LangGraph orchestration

`src/orchestration/graph.py` is the current primary entry point
(`graph.run(...)`). The graph is a validation-gated state machine:

```
read_mesh → build_intent → expand_spec → render_case → preflight_case → validate_case → END
                ↑            │                              │                │
                │            └─ DICTIONARY_SYNTAX → patch_files ─────────────┘
                │ preflight FAIL / MESH_* / SOLVER_RUNTIME → replay to build_intent
                └─ intent_error → END
```

- **`read_mesh`** — if `mesh_dir` given, parse the boundary file for real patch
  names; else box geometry (blockMeshDict will be generated).
- **`build_intent`** — LLM call → `CaseIntent` (with RAG context + patch names +
  on retry, the previous error + failing file content). If the inner retry loop
  exhausts without a valid intent, it surfaces a structured `intent_error` and
  the graph ends cleanly instead of crashing on an empty intent.
- **`expand_spec`** — deterministic `intent_to_spec()`.
- **`render_case`** — `render()` → write files → copy `polyMesh` → apply
  `file_overrides` (from the patch node). Nested override keys whose parent is
  an existing file are skipped + counted (`case.py:apply_file_overrides`),
  so a mis-shaped patch can't crash the graph with `NotADirectoryError`.
- **`preflight_case`** — the cheap gate (`src/preflight/`, see
  [`../architecture/preflight-gate.md`](../architecture/preflight-gate.md)):
  static audit (free) → checkMesh metrics → bounded dry run. FAIL routes back
  to `build_intent` with the audit report as `error_feedback`; env-skip defers
  to `validate_case`.
- **`patch_files`** — LLM reads the **exact failing file** and returns corrected
  content as `file_overrides`. Triggered only for `DICTIONARY_SYNTAX`.
- **`validate_case`** — runs the ladder in Docker; routes by `FailureClass`.

Routing rules (`route_after_validate` / `route_after_preflight`, `graph.py`):

| Failure class | Next node | Rationale |
|---|---|---|
| `DICTIONARY_SYNTAX` | `patch_files` (≤3×) | targeted file fix; keeps a good intent |
| preflight `FAIL` | `build_intent` | catch a doomed case *before* the expensive ladder; audit report as evidence |
| preflight env-skip / `PASS` / `WARN` | `validate_case` | the ladder is the source of truth |
| `MESH_GENERATION`, `MESH_QUALITY`, `SOLVER_RUNTIME` | `build_intent` | physics/geometry intent re-extraction |
| `ENVIRONMENT` | `END` | Docker/infra — nothing the agent can fix |
| `intent_error` set | `END` | LLM extraction failed — surface cleanly |

State lives in a `TypedDict` (`CaseGraphState`); `max_attempts` (default 3)
bounds the full build intent loop, `patch_attempt` bounds the per-intent patch
loop, and `patch_stats` tracks targeted-patch attempts vs resolutions (printed
after each `validate_case` so patch resolution rate is measurable).

---

## 4. What happens in each stage (key files)

### 4.1 The LLM call — `orchestration/pipeline.py`

`extract_intent(nl, config, known_patches)`:
1. Builds the system prompt from `_SYSTEM` (schema `_SCHEMA` + one-shot `_EXAMPLE`
   + BC guidance), appended RAG context + real patch names.
2. Three-stage validation in a retry loop (≤3, no Docker):
   - **Stage 1** — Pydantic `CaseIntent(**json)`.
   - **Stage 1.5** — `EnvelopeConstraint.apply()`: clamps every LLM-emitted
     number into a physically-plausible range (`physical_envelopes.json`;
     species-derived for `nu` — see
     `docs/architecture/deterministic-constraint-layers.md`). Modest outliers
     are clamped with a printed note; unit-level outliers are rejected and fed
     back to the model with the valid range.
   - **Stage 2** — `IntentValidator.validate()`: BC types, required BC keys,
     field applicability, turbulence model name, relax factor ranges.
   All failure modes are fed back to the model in the same loop.
3. `SkillRegistry.apply_skills()` runs deterministic enriching passes:
   `ExternalMeshPatchSkill` (patch name/type → role mapping) and
   `PromptPressureSkill` (reads "4 bar" / "5 atm" out of the prompt).

`extract_file_patches()` is the targeted-fix variant (`file_overrides`).
`generate()` is **deprecated** — it lacks the patch node.

### 4.2 The intent → spec expander — `ir/intent.py`

`intent_to_spec()` resolves the case type from the registry, computes the field
set for that family, assigns box faces to patches, builds per-role BCs
(`_bc_for_role`), looks up species thermo data from `knowledge/thermophysics.json`
(via `_RETRIEVER`), and picks a numerics preset. Notable deterministic logic:

- `_case_fields(def_, intent, ...)` — the exact `0/` field set for the family.
- `_bc_for_role(...)` — standard per-role boundary conditions + wall functions.
- `_turbulence_values(intent)` — estimated `k/epsilon` from velocity dimensions.
- `_time_control(intent)` / `_cfl_delta_t(intent)` — adaptive timestep policy
  (Courant-based first guess, clamped `[1e-6, 0.1]`).

Solids and films are **forced laminar** regardless of regime.

### 4.3 The renderer — `rendering/renderer.py`

Pure functions, one per dictionary: `render_blockmeshdict`, `render_controldict`,
`render_fvschemes`, `render_fvsolution`, `render_physicalproperties`,
`render_momentumtransport`, `render_per_phase_physical`, `render_phaseproperties`,
`render_g`, `render_dynamicmeshdict`, `render_field`. `render(spec)` assembles a
`CaseFiles` in memory; `case.write(case_dir)` writes `0/`, `constant/`, `system/`
to disk and `apply_file_overrides` splices direct LLM patches on top.

### 4.4 Mesh handling — `mesh/mesh_io.py`

Repository rule R1: **this repo never generates or repairs meshes.** A mesh is
externally supplied; only the lightweight polyMesh `boundary` file is parsed
(regex-based — no full OpenFOAM parser) to get patch names/types/face counts.
The heavy files (`points`/`faces`/`owner`/`neighbour`) are copied byte-for-byte.

### 4.5 Validation — `validation/validation_agent.py`

Runs the ladder inside Docker: `docker run --rm -v <case>:/case -w /case
microfluidica/openfoam:13 <foamCmd>`.

```
blockMesh ──▶ checkMesh ──▶ foamRun      (box geometry full ladder)
checkMesh ──▶ foamRun                    (external mesh: skip blockMesh)
```

The solver rung is **parallel-capable**: with `nprocs > 1` it writes a scotch
`system/decomposeParDict`, runs `decomposePar -force`, then
`mpirun --allow-run-as-root -np N foamRun -parallel`. This cut a 10.6M-cell
RHVT run from ~20+ min to ~1.8 min. Serial (default) is unchanged.

Two env vars tune the ladder (see also §8):

| Env var | Default | Meaning |
|---|---|---|
| `FOAM_DOCKER_IMAGE` | `microfluidica/openfoam:13` | OpenFOAM v13 container |
| `FOAM_VALIDATION_TIMEOUT` | `600` | wall-clock per-command timeout (seconds) |
| `FOAM_VALIDATION_PROCS` | `1` | ranks for `foamRun -parallel` (1 = serial) |

**NaN/divergence handling.** A solver that diverges prints NaN residuals
forever instead of crashing, so it used to burn the full timeout and be
misclassified `ENVIRONMENT` (dead-ending the graph). Now:
1. `_docker_run(stop_on_nan=True)` kills the container at the **first**
   non-finite residual — no time wasted on a doomed run.
2. stdout+stderr is retained even on timeout/NaN, so evidence is never thrown
   away.
3. `parse_errors()` detects `nan`/`inf` residual lines and classifies the
   failure `SOLVER_RUNTIME`, so it routes back to `build_intent` for a physics
   retry instead of `END`.

`parse_errors()` localizes failures: extracts the `FOAM FATAL ERROR` block, names
the offending file+line when OpenFOAM gives it, and classifies into
`FailureClass`. `checkMesh` pass keys are cached per static mesh to skip repeated
runs.

### 4.6 The LLM client — `llm/llm_client.py`

OpenAI-compatible, so the backend (Ollama, vLLM, SGLang, an OpenAI-compatible
gateway) can swap without code changes. Configured purely via env:
`LOCAL_LLM_BASE_URL`, `LOCAL_LLM_MODEL`, optional `LLM_GATEWAY_API_KEY`
(read from `.env`; `.env.example` shows all three).

---

## 5. The case-type registry (`ir/case_types.py`)

This is the map from a physics family to the structural facts the renderer needs.
Every `CaseTypeDef` records:

| Field | Meaning |
|---|---|
| `solver` | v13 solver **module** name written into `controlDict` |
| `flow_type` | `incompressible` / `compressible` / `solid` |
| `needs_gravity` | writes `constant/g` + switches to `p_rgh` |
| `phases` | default phase names → `alpha.<phase>` fields + `phaseProperties` |
| `per_phase_properties` | `physicalProperties.<phase>` dictionaries |
| `compressible_phases` | per-phase files carry thermo, not ν/ρ |
| `surface_tension` | default σ [N/m] |
| `relative_velocity_model` | drift-flux model |
| `species` | default species for multicomponent / combustion cases |
| `is_solid` / `is_film` | field + property-dict flavour |
| `default_numerics` | which preset applies |

The 18 families: `incompressibleFluid`, `fluid`, `fluid_buoyant`,
`incompressibleVoF`, `incompressibleMultiphaseVoF`, `compressibleVoF`,
`compressibleMultiphaseVoF`, `incompressibleDriftFlux`, `multiphaseEuler`,
`incompressibleDenseParticleFluid`, `film`, `isothermalFilm`, `isothermalFluid`,
`shockFluid`, `solidDisplacement`, `multicomponentFluid`, `XiFluid`, `movingMesh`.
Both `intent_to_spec()` and the renderer dispatch on the resolved `CaseTypeDef`.

---

## 6. The knowledge base (`knowledge/*`)

| File | Feeds | Contents |
|---|---|---|
| `solvers.json` | RAG solver guidance | v13 solver modules, regimes, required/turbulent fields |
| `boundary_conditions.json` | RAG BC guidance | role → BC guidance per patch type |
| `bc_types.json` | intent validation | 27 known BC type names |
| `thermophysics.json` | species thermo + `nu` envelope | per-species `mixture` → molWeight, Cp, hf, μ, Pr (6 species) |
| `physical_envelopes.json` | numeric constraint | plausible/reject ranges + rationale per numeric field |
| `turbulence_models.json` | intent validation | known RAS/LES model names |
| `v13_tutorials.json` | RAG numerics | fvSchemes/fvSolution snippets per tutorial solver/flow |

`rag.py` is deliberately zero-dependency: keyword-overlap scoring over the JSON
text (`search`), plus typed lookups (`get_species_thermo`, `get_tutorial_numerics`).
`format_prompt_context` turns retrieved items into a markdown grounding block
appended to the LLM system prompt.

---

## 7. Common extensions (walkthroughs)

### 7.1 Add a new case type / solver family

1. **`ir/case_types.py`** — add a `CaseTypeDef` to `CASE_TYPES`: correct `solver`
   module name, `flow_type`, and whichever of the booleans/tuples/numbers apply.
   Ground it in a real v13 tutorial, not assumption.
2. **`ir/intent.py`** — if the family needs a non-default field set or property
   dictionary, extend `_case_fields()` and `intent_to_spec()` for the new name.
3. **`rendering/renderer.py`** — ensure `render()` covers the new case: property
   layer (per-phase vs single), phaseProperties, gravity file, dynamicMeshDict,
   and that any new numerics preset referenced by `default_numerics` actually
   exists (all preset *keys* must be defined in the fvSchemes/fvSolution
   renderers).
4. **`knowledge/solvers.json`** — add the solver so RAG can recommend it.
5. **Tests** — add a rendering/vortex-tube-style test (see §8).

### 7.2 Add a fluid species (e.g. "methane")

1. Add its thermo block to `knowledge/thermophysics.json` (species + `mixture`
   with specie/thermodynamics/transport).
2. Add the token to the `fluid_species` enum in the `_SCHEMA` +
   `_RESPONSE_FORMAT` in `orchestration/pipeline.py`, and the JSON-schema enum.
3. Optionally update the table in `agent-usage-guide.md`.

### 7.3 Add a boundary-condition override / new BC type

- New generic BC name → add to `knowledge/bc_types.json`.
- New role guidance → `knowledge/boundary_conditions.json`.
- Stricter semantic checks → `knowledge/intent_validator.py`.
- The LLM-facing hints live in `_BC_GUIDANCE` in `orchestration/pipeline.py`.

### 7.4 Add a retrieval source

- To make the case-type-specific numerics smarter, extend
  `get_tutorial_numerics()` + `v13_tutorials.json` with new tutorial snippets,
  but never add a preset key without defining it in the renderer.

### 7.5 Add or tune a numeric envelope (constraint range)

1. Edit `knowledge/physical_envelopes.json` — add an `envelopes` entry:
   `field`, `units`, `min`/`max` (plausible range), `reject_below`/`reject_above`
   (unit-error band), and a `rationale` string justifying the bounds. No code
   change: `EnvelopeConstraint` picks the new field up automatically.
2. If the envelope should be **derived from species data** (like `nu`), add a
   branch in `_envelope_for()` in `knowledge/envelopes.py` that computes the
   reference from the species' thermo block and returns a derived `Envelope`
   only when the species is known; keep the static fallback otherwise.
3. Wire the field into `apply()` if it is a scalar/tuple not yet handled.
4. Add a test in `tests/test_envelopes.py` for each of: in-range pass,
   clamp-with-note, reject-with-error.

---

## 8. Testing

```bash
# From the repo root, with your Python environment (venv, conda, ...) active
# and `pip install -r requirements.txt` done:
pytest -q

# or run against a specific interpreter regardless of what's active:
/path/to/your/python -m pytest -q
```

- 141 tests, offline (~1s, no Docker/LLM needed at test time — LLM/docker
  interactions are mocked at the client/ladder boundary).
- Coverage topics: intent extraction/validation, numeric envelope constraint
  (`tests/test_envelopes.py`), mesh `boundary` parsing, renderer output for the
  main families (including the compressible vortex-tube case), RAG + skills,
  preflight gate (audit/checkMesh/residual predictor), NaN classification +
  partial-output retention on timeout, nested-override rejection, the
  energy-solver fvSolution enforcement, and the graph's error routing/patching.
- To exercise the real parallel ladder locally, set `FOAM_VALIDATION_PROCS` (and
  `FOAM_VALIDATION_TIMEOUT`) and run a case through `graph.run` — the ladder is
  the only part tests deliberately mock.
- Run tests on new renderer branches even if the change "feels" obvious — the
  renderer is where silent typo regressions hide.

There is no separate lint/typecheck gate wired up, but the repo ships
`pyproject.toml` (`[tool.pyright] extraPaths = ["src"]`) — a pyright pass is a
free sanity check.

---

## 9. Conventions & gotchas

- **v13 first.** Files and field conventions must match OpenFOAM **Foundation
  v13**: solver *modules* named in `controlDict` (not old binaries like
  `icoFoam`), properties in `constant/physicalProperties` +
  `constant/momentumTransport` (not `transportProperties`). See
  [`../domain/openfoam/case-structure.md`](../domain/openfoam/case-structure.md).
- **Never let the LLM write dictionary text** unless it is the *targeted file
  patch* path (`patch_files`), which is bounded and route-gated for a reason.
- **Mesh boundary names are authoritative.** With an external mesh, patch names
  come from the real `boundary` file and the prompt/model must reuse them
  verbatim (`_known_patches_hint`).
- **Mutate `CaseIntent` only in deterministic code.** Skills and the expander
  may change it; the LLM JSON path is validated before it counts.
- **State resets on retry:** `file_overrides` are cleared in `build_intent` so a
  full re-render happens clean; the patch loop caps at 3 before falling back.
- **Docs live in classified topic folders** (`docs/domain`, `docs/architecture`,
  `docs/guides`, ...); `docs/README.md` is the index. New knowledge or renderer
  behaviour should be reflected there, not just in code comments.
- **This is a spike.** Prefer a slightly rough but *demonstrably running*
  improvement over a clean one you can't validate — the pipeline's whole claim
  to correctness rests on the Docker ladder.

---

## 10. Further reading

- [`../architecture/phase-ii-architecture.md`](../architecture/phase-ii-architecture.md) —
  the design rationale (constrained-late IR, validation ladder, meshing verdict).
- [`../architecture/self-correction-loop.md`](../architecture/self-correction-loop.md) —
  failure routing by owner and retry policy.
- [`../architecture/agent-framework-comparison.md`](../architecture/agent-framework-comparison.md) —
  why LangGraph.
- [`agent-usage-guide.md`](agent-usage-guide.md) — CLI + Python API reference
  (the practical run documentation).
- [`src/README.md`](../../src/README.md) — sub-package map as of the latest spike
  session.