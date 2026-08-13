# Overview — Architecture & Data Flow

This document explains the *whole pipeline* in one pass: the central bet, the
stages a prompt moves through, and where each piece of code lives. Read this
first; the other docs in this folder go into each stage in depth.

## 1. The core bet: "reason free, constrain late"

The pipeline's central claim — and the reason a **weak, local** LLM works — is:

> **The LLM never writes OpenFOAM dictionary syntax.** It emits only a small,
> typed *physics intent* (`CaseIntent`). Deterministic code expands that into a
> complete structural description (`CaseSpec`), renders guaranteed-valid v13
> files, and then validates the case by *actually running it* in Docker.

```
NL prompt ──LLM──▶ CaseIntent ──deterministic──▶ CaseSpec ──renderer──▶ files
                    (compact)      intent_to_spec()      render()      │
                                                                     ▼
                                               Docker v13 ladder (blockMesh /
                                               checkMesh / foamRun) + self-correction
```

Three consequences worth internalizing:

1. **The LLM's job is small and constrained.** It picks a `case_type` from an
   enum registry, patch roles, and a handful of numbers — all bounded by Pydantic
   enums, the IntentValidator, and the physical envelopes. It never guesses
   field lists, dimensions, wall functions, or timestep sizing; those are code.
2. **Everything mechanical is deterministic and unit-testable.** The renderer
   produces the same bytes for the same `CaseSpec`, so correctness is verified
   against golden tutorials, not by model behavior.
3. **The Docker ladder is the source of truth.** The static/cheap checks
   (`IntentValidator`, envelopes, preflight) only *predict*; the case is only
   "done" when `foamRun` actually runs in OpenFOAM v13.

## 2. The data flow, stage by stage

The primary entry point is `orchestration.graph.run(...)` — a LangGraph
state machine. (An older, deprecated `pipeline.generate()` exists and lacks the
self-correction node; use `graph.run`.)

```
read_mesh → build_intent → expand_spec → render_case → preflight_case → validate_case → END
                ↑            │                              │                │
                │            └─ DICTIONARY_SYNTAX → patch_files ─────────────┘
                │ preflight FAIL / MESH_* / SOLVER_RUNTIME → replay to build_intent
                └─ intent_error → END
```

### Stage 0 — `read_mesh`
- **No `--mesh`:** box geometry. The renderer emits a `blockMeshDict`; the full
  ladder (blockMesh → checkMesh → foamRun) will run.
- **With `--mesh`:** the external mesh's `boundary` file is parsed for real patch
  names/types (`mesh/mesh_io.py`); the heavy polyMesh files are copied
  byte-for-byte into `constant/polyMesh/`; the ladder skips blockMesh.

### Stage 1 — `build_intent` (the LLM call)
`orchestration/pipeline.py:extract_intent()`:
1. Builds the system prompt from `_SYSTEM` (schema `_SCHEMA` + one-shot
   `_EXAMPLE` + BC guidance), appended RAG context and real patch names.
2. **Three-stage validation in a retry loop (≤3, no Docker):**
   - **Stage 1** — Pydantic `CaseIntent(**json)` (repairs fences / isolates the
     JSON span first).
   - **Stage 1.5** — `EnvelopeConstraint.apply()`: clamps every LLM-emitted
     number into a physically-plausible range (see `knowledge-layer.md` §5).
   - **Stage 2** — `IntentValidator.validate()`: BC types, required keys, field
     applicability, turbulence model, relaxation-factor ranges.
   All failure modes are fed back to the model in the same loop.
3. `SkillRegistry.apply_skills()` enriches the intent deterministically
   (mesh patch → role mapping; reading "4 bar"/"5 atm" out of the prompt).

If the inner loop exhausts without a valid intent, it raises `ValueError`, which
`graph.build_intent` catches and turns into a structured `intent_error` so the
graph ends cleanly instead of crashing on an empty intent.

### Stage 2 — `expand_spec`
`ir/intent.py:intent_to_spec()` — deterministic expansion:
- resolves the case type from the registry (`ir/case_types.py`);
- computes the exact `0/` field set for that family (`_case_fields`);
- assigns box faces to patches (`_assign_faces`);
- builds per-role BCs for every field (`_bc_for_role`, with wall functions);
- looks up species thermo from `knowledge/thermophysics.json`;
- picks a numerics preset and time control (Courant-based `deltaT` guess).

### Stage 3 — `render_case`
`rendering/renderer.py:render(spec)` → in-memory `CaseFiles`, then
`case.write(case_dir)` writes `0/`, `constant/`, `system/`; the external mesh is
copied in; any `file_overrides` from the patch node are spliced on top.

### Stage 3.5 — `preflight_case` (cheap gate)
`preflight/runner.py:run_preflight_with_agent()`:
- **Level A** — static dictionary audit, no Docker (`audit.py`).
- **Level B** — blockMesh/checkMesh metrics (`metrics.py`).
- **Level C** — bounded solver dry-run + residual-slope divergence predictor
  (`dry_run.py`).

FAIL routes back to `build_intent` with the audit as evidence, so a known-bad
case never spends the full validation timeout. If Docker is unavailable, the
gate defers to the ladder (env-skip).

### Stage 4 — `validate_case`
`validation/validation_agent.py` runs the real OpenFOAM v13 ladder inside
Docker (`microfluidica/openfoam:13`):
```
blockMesh ──▶ checkMesh ──▶ foamRun      (box geometry)
checkMesh ──▶ foamRun                    (external mesh)
```
`parse_errors()` localizes failures (file + line when OpenFOAM names them) and
classifies them into `FailureClass`. The solver rung aborts on the first NaN
residual, retains partial output on timeout, and can run parallel
(`mpirun -parallel` via scotch `decomposeParDict`).

### Stage 5 — routing (self-correction)
`graph.route_after_validate()` routes by failure class:

| Failure class | Next node | Rationale |
|---|---|---|
| `DICTIONARY_SYNTAX` | `patch_files` (≤3×) | targeted file fix; keeps a good intent |
| preflight `FAIL` | `build_intent` | catch a doomed case *before* the expensive ladder |
| preflight env-skip / PASS / WARN | `validate_case` | the ladder is the source of truth |
| `MESH_GENERATION`, `MESH_QUALITY`, `SOLVER_RUNTIME` | `build_intent` | physics/geometry intent re-extraction |
| `ENVIRONMENT` | `END` | Docker/infra — nothing the agent can fix |
| `intent_error` set | `END` | LLM extraction failed — surface cleanly |

`patch_files` is the *targeted* self-correction path: the LLM reads the exact
failing file(s) and returns corrected whole-file content as `file_overrides`
(bounded to 3 per intent; a mis-shaped override is skipped, not fatal).

## 3. The two input modes (box vs external mesh)

| | Box geometry | External mesh |
|---|---|---|
| `geometry` | `"box"` | `"external_mesh"` |
| Patches | LLM-chosen roles + box faces (auto-assigned if omitted) | must match real `boundary` names verbatim |
| Mesh | `blockMeshDict` rendered | polyMesh copied byte-for-byte |
| Ladder | blockMesh → checkMesh → foamRun | checkMesh → foamRun (skip blockMesh) |

## 4. Where each concern is documented

| Concern | Doc |
|---|---|
| IR types, case registry, renderer | `ir-and-rendering.md` |
| LangGraph graph, retry/routing, LLM extraction | `orchestration.md` |
| RAG, IntentValidator, envelopes, skills, JSON data | `knowledge-layer.md` |
| Docker ladder, failure classes, preflight gate | `validation-and-preflight.md` |
| LLM client, env vars, mesh I/O, CLI | `config-and-llm.md` |
| Step-by-step extension walkthroughs | `extending.md` |
| Tests, conventions, gotchas | `testing-and-conventions.md` |

For the design *rationale* (why this architecture, the "Constraint Tax"
finding, meshing verdict), see
[`../architecture/phase-ii-architecture.md`](../architecture/phase-ii-architecture.md).