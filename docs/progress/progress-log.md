# Progress Log

Running log of concrete milestones on the `src/` architecture spike. Append new
entries at the top. See `src/README.md` for the code layout and
`docs/architecture/phase-ii-architecture.md` for the design this validates.

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
