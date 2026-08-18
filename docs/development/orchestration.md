# Orchestration — the LangGraph Pipeline

This document explains `src/orchestration/` — the graph that glues the whole
pipeline together, the retry/routing rules, and the two LLM entrypoints. If you
are changing **control flow**, **retry policy**, or **how the pipeline reacts to
failure**, this is the doc.

## 1. Files & roles

| File | Role |
|---|---|
| `graph.py` | the LangGraph `StateGraph` — the **current primary pipeline** (`graph.run`) |
| `pipeline.py` | `extract_intent()` / `extract_file_patches()` — the LLM-call entrypoints the graph wraps; plus the deprecated `generate()` |

## 2. The graph (`graph.py`)

### State

All state lives in a `TypedDict`, `CaseGraphState` (`graph.py:56`). Key fields:

- Inputs: `nl`, `mesh_dir`, `case_dir`, `config`.
- Produced: `known_patches`, `intent`, `spec`, `case_files`, `file_overrides`,
  `validation`, `preflight`.
- Loop control: `attempt`, `patch_attempt`, `max_attempts` (default 3),
  `error_feedback`, `intent_error`, `patch_stats`.
- `done` (set by `validate_case`).

### Node layout

```
read_mesh → build_intent → expand_spec → render_case → preflight_case → validate_case → END
                ↑            │                              │                │
                │            └─ DICTIONARY_SYNTAX → patch_files ─────────────┘
                │ preflight FAIL / MESH_* / SOLVER_RUNTIME → replay to build_intent
                └─ intent_error → END
```

Each node is a plain function taking and returning dicts (or mutating state):

- **`read_mesh`** (`graph.py:108`) — parse the external `boundary` file into
  `known_patches`, or emit box-geometry mode. Prints `[1/5]`.
- **`build_intent`** (`graph.py:123`) — calls `extract_intent`. On `ValueError`
  (inner retry exhausted) it **does not crash**; it returns `{"intent_error": ...}`
  and the router ends cleanly. Clears stale `file_overrides` so re-renders are
  clean. On a retry it appends the previous failure + the failing file contents
  to the NL prompt.
- **`expand_spec`** (`graph.py:167`) — `intent_to_spec()`.
- **`render_case`** (`graph.py:178`) — `render()` → `CaseFiles.write()` → copy
  the external polyMesh → `apply_file_overrides()` (skips nested overrides, logs
  count).
- **`patch_files`** (`graph.py:231`) — the targeted self-correction node. Builds
  a prompt embedding the previous error + the failing file content, calls
  `extract_file_patches()`, and increments `patch_stats["attempts"]`.
- **`preflight_case`** (`graph.py:209`) — runs Levels A/B/C; FAIL puts a
  `[preflight] ...` evidence string into `error_feedback` (see
  `validation-and-preflight.md` §3).
- **`validate_case`** (`graph.py:258`) — the Docker ladder
  (`skip_mesh_generation = bool(mesh_dir)`); records patch resolution stats. On success
  it also archives the configuration and log to `<case>/.runs/<stamp>/` via
  `validation.snapshot.snapshot_run` — the loop re-renders the case in place on the next
  attempt, so a passing configuration would otherwise be destroyed by the retry that
  follows it. Archiving failures are printed, never raised
  (`validation-and-preflight.md` §3b).

### Routing

Three conditional-edge functions:

- **`route_after_build_intent`** (inline lambda, `graph.py:356`) — fail to
  `END` if `intent_error`, else continue to `expand_spec`.
- **`route_after_preflight`** (`graph.py:287`) — `FAIL` → `build_intent`
  (unless env-skip: "Docker/OpenFOAM unavailable" → defer to the ladder; unless
  max attempts reached → `END`). PASS/WARN → `validate_case`.
- **`route_after_validate`** (`graph.py:306`) — the main recovery switch:

  | Condition | Next node | Rationale |
  |---|---|---|
  | `intent_error` | `END` | LLM extraction failed — surface cleanly |
  | `validation.ok` | `END` | done |
  | `attempt ≥ max_attempts` | `END` | budget exhausted |
  | `ENVIRONMENT` in classes | `END` | infra failure — nothing the agent can fix |
  | classes == `{DICTIONARY_SYNTAX}` and `patch_attempt < 3` | `patch_files` | targeted whole-file fix, keeps a good intent |
  | anything else | `build_intent` | full intent re-extraction |

- **`route_after_patch`** (`graph.py:332`) — budget check: `END` if
  `attempt ≥ max_attempts`, `build_intent` if `patch_attempt ≥ 3` (patch loop
  fell back), else `render_case` (apply overrides + re-validate).

## 3. The LLM extraction (`pipeline.py`)

### `extract_intent(nl, config, max_retries=3, known_patches=None)`

The validate-and-retry loop. Steps:

1. **Prompt construction.** `_SYSTEM` = schema JSON (`_SCHEMA`) + one-shot
   example (`_EXAMPLE`) + BC guidance (`_BC_GUIDANCE`). The user content is `nl`
   + (if a mesh is given) a `_known_patches_hint` (must reuse patch names
   verbatim) + RAG grounding (`KnowledgeRetriever.format_prompt_context`).
2. **Structured output** — `_RESPONSE_FORMAT` is an LM-Studio-friendly JSON
   schema (`items` + enums, no tuples/`$refs`) so grammar-constrained backends
   can't emit malformed brackets. `_chat_create` (`pipeline.py:225`) degrades
   gracefully for backends that reject `temperature=0` or
   `response_format=json_schema` (tries 4 combos).
3. **Repair pass** — `_extract_json` (`pipeline.py:184`) strips fences, isolates
   the outermost `{...}`, drops `//` comments and trailing commas.

Then the **three-stage inner validation loop:**

- **Stage 1 — Pydantic.** `CaseIntent(**json)`; on `JSONDecodeError` /
  `PydanticValidationError` / `TypeError`, feed the error back and retry.
- **Stage 1.5 — EnvelopeConstraint.** `apply()` clamps out-of-range numbers and
  prints notes; unit-level outliers return `IntentValidationError`s that are
  fed back to the LLM with the valid range (`knowledge-layer.md` §5).
- **Stage 2 — IntentValidator.** Semantic checks against the registries; all
  errors fed back (see `knowledge-layer.md` §4).

Finally `SkillRegistry.apply_skills(intent, nl, known_patches)` runs
deterministic enrichment, and the validated intent is returned.

If all `max_retries` fail, raises `ValueError` (the graph turns this into
`intent_error`).

### `extract_file_patches(prompt, config, max_retries=2)`

The patch-node LLM call. Much simpler: one-shot, `_PATCH_SYSTEM` prompts for a
`{"file_overrides": {"<relpath>": "<content>"}}` object
(`_PATCH_RESPONSE_FORMAT`). Uses `_extract_fenced_json` (`pipeline.py:195`),
which **deliberately does NOT strip `//` comments** — OpenFOAM files are full of
them and stripping corrupts the JSON string values. Returns an empty dict on
failure (never raises), so the graph falls back to a full intent rebuild.

### `generate()` — deprecated

Kept for compatibility; lacks the patch node and file overrides. Prefer
`graph.run`.

## 4. Environment / entry point

`src/__main__.py` provides the `run` subcommand:

```bash
python -m src run \
  --mesh "temp/provided-mesh/3D RHVT (OpenFOAM Files)" \
  --case-dir temp/out/my_case \
  --prompt "Describe the flow here..."
# or --prompt-file prompt.txt ; or stdin (Ctrl-Z/D + Enter)
```

- `--mesh` optional; omit for box geometry.
- `--model` overrides `LOCAL_LLM_MODEL`; `--max-attempts` bounds the loop.
- Prints `[n/5]` stage banners, the validation stage + `ok`, error lines, and the
  attempts used. Exit code 0 on success.

## 5. Gotchas specific to this layer

- **UI honesty:** stage banners print `[1/5]...` but a preflight step is
  printed `[4.5/5]` and the ladder `[5/5]` — the numbering reflects the published
  five stages, not the node count.
- **`build_intent` must clear `file_overrides`** on every call, or a stale patch
  would be re-applied on a fresh render.
- **`route_after_preflight` checks for the env-skip string in the message** — if
  you reword the message in `preflight/runner.py`, update this test too.
- Both LLM entrypoints print stage progress to **stdout** (`print(..., flush=True)`)
  so the CLI feels interactive; tests mock the client at the boundary, so they
  don't depend on these strings.
- Never let the LLM write dictionary text except through `patch_files`, which is
  bounded (≤3) and route-gated — that is the whole "reason free, constrain late"
  contract.