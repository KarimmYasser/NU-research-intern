# src/ — Phase II architecture spike

> **⚠️ Status: experimental spike, not the deliverable.** This code was written to
> *validate the Phase II architecture end-to-end* — and it does: a plain-English
> prompt becomes an OpenFOAM v13 case that actually runs (local LLM + Docker
> OpenFOAM, no VM, no external API). It is deliberately kept at spike maturity
> (one solver family, box/cavity scope, minimal error handling). **The real
> implementation will be built deliberately later**, once the research phase is
> complete and the design is settled with the supervisor. Treat everything here
> as proven evidence for the architecture, not as final code to extend in place.

Multi-agent OpenFOAM case-generation pipeline (concept note Phase II). Architecture: **structured IR + deterministic renderer** ("reason free, constrain late" - see `docs/phase-ii-architecture.md` §2): the LLM emits a compact validated intent, deterministic code renders guaranteed-valid v13 files, and the case is validated by *actually running it* in an OpenFOAM v13 Docker container. **The full path works end-to-end** - a plain-English prompt becomes a case that runs on real v13 (`foamRun` exits 0), driven by a local LM Studio model. Basic scope so far (incompressibleFluid, box/external mesh, laminar or k-epsilon), widening from here.

## Layout

- `agents/spec.py` - the **`CaseSpec` IR** (Pydantic) the LLM emits: geometry, solver, patches + BC intents, fields, turbulence, fluid props, time control (v13). The contract between the agents and the renderer.
- `agents/renderer.py` - **deterministic renderer**: `CaseSpec` -> v13 files. **Prototype #1 done and verified** on two cases (`tests/test_renderer.py`): (1) the **cavity** renders byte-for-byte identical to the golden OpenFOAM.org v13 tutorial, no LLM involved; (2) the **NU assessment** turbulent-channel case (external mesh, inlet/outlet, k-e wall functions, all 5 turbulence fields, adjustable-timestep control) renders as clean v13 - and structurally cannot reproduce the six errors planted in the assessment's hand-written version (`[ ]` FoamFile, `%%` comment, a field missing a patch, `application`/version mismatch, broken separator, missing `;`). Scope: box + `external_mesh` geometry, `incompressibleFluid` + RAS, two numeric presets.
- `agents/intent.py` - **`CaseIntent`** (the compact intent the LLM emits: regime, geometry, patch roles, a few numbers) plus `intent_to_spec()`, the deterministic expander that fills in fields, dimensions, per-role BCs, wall functions, and estimated turbulence values (its k/epsilon estimates reproduce the OpenFOAM cavity tutorial's). Keeps the model's job small enough for a tiny local model.
- `agents/pipeline.py` - **end-to-end**: `generate(nl, case_dir)` runs NL -> `extract_intent` (LLM, grammar-constrained via LM Studio `json_schema` + repair/validate/retry) -> `intent_to_spec` -> render -> validate. Verified: a lid-driven-cavity prompt through `ibm/granite-4-h-tiny` produces a case that runs on v13.
- `agents/llm_client.py` - shared OpenAI-compatible client, pointed at a local endpoint (Ollama/LM Studio/vLLM; set `LOCAL_LLM_BASE_URL`/`LOCAL_LLM_MODEL`). Swapping models is just a config change.
- `agents/case.py` - in-memory `CaseFiles` representation of an OpenFOAM case (`0/`, `constant/`, `system/`) plus a disk writer; the renderer's output type.
- `agents/meshing_agent.py` - **Meshing Agent**: owns `system/blockMeshDict`. See `docs/openfoam/blockmesh.md`.
- `agents/physics_agent.py` - **Physics Agent**: owns `0/` field files and `constant/physicalProperties` / `constant/momentumTransport` (v13 naming - see `docs/openfoam/case-structure.md`). Boundary patch names it writes must match what the Meshing Agent defined.
- `agents/validation_agent.py` - **Syntax Validation Agent**: runs the validation ladder (`run_ladder`: `blockMesh` -> `checkMesh` -> `foamRun`) against a rendered case **inside a Docker OpenFOAM v13 container** (`microfluidica/openfoam:13`), so no VM or local OpenFOAM install is needed. `parse_errors` classifies failures (`FailureClass`) and localizes the offending file/line from OpenFOAM's `FATAL IO ERROR` blocks (the FLARE finding: error specificity > retry mechanism). **Verified end-to-end**: a good cavity passes all three rungs on real v13; a corrupted `blockMeshDict` is caught, classified `DICTIONARY_SYNTAX`, and localized to `system/blockMeshDict` (`tests/test_validation.py` covers the parser).
- `agents/orchestrator.py` - wires the three agents together: Meshing drafts first (patch names come from geometry), Physics drafts fields against those names, Validation runs and routes failures back to the owning agent. All three agents' actual LLM-calling logic (`draft_*`, and a not-yet-added `redraft`) is stubbed with `NotImplementedError`/`TODO` - this is a skeleton, not a working pipeline.

> **Note:** `meshing_agent.py`, `physics_agent.py`, and `orchestrator.py` predate the IR decision - they were written to have the LLM emit *raw dict files*. Under the current architecture they will be refactored so the agents emit **`CaseSpec` fragments** (geometry intent, physics intent) that feed `renderer.py`, and the orchestrator becomes the LangGraph graph. `spec.py` + `renderer.py` are the new core; these three are the pending refactor.

## Open decisions (confirm with supervisor before committing further)

- **Agent framework**: `pyproject.toml` lists LangChain/AutoGen/CrewAI as optional extras but nothing imports any of them yet. The stubs are plain Python/OpenAI-client code on purpose, so the framework choice doesn't have to be made to keep scaffolding.
- **Handoff order**: orchestrator assumes Meshing → Physics (geometry decides patch names). Reasonable default, not verified against how the group actually wants this to work.
- **Who owns `system/controlDict`, `fvSchemes`, `fvSolution`**: not assigned to either agent yet - noted as a TODO in `orchestrator.py`.
- **checkMesh success detection**: some OpenFOAM versions return exit code 0 even with quality warnings; `validation_agent.py` flags this as unverified, needs checking against the actual VM's OpenFOAM install.
