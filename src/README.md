# src/ — Phase II agent scaffold

Early skeleton for the multi-agent OpenFOAM case-generation pipeline (concept note Phase II). The architecture is now **structured IR + deterministic renderer** ("reason free, constrain late" - see `docs/phase-ii-architecture.md` §2): the LLM emits a validated `CaseSpec`, and deterministic code renders guaranteed-valid v13 files. The renderer (`renderer.py`) is real and tested; the LLM-facing agents are still stubs pending the local endpoint (`docs/vm-ollama-setup.md`).

## Layout

- `agents/spec.py` - the **`CaseSpec` IR** (Pydantic) the LLM emits: geometry, solver, patches + BC intents, fields, turbulence, fluid props, time control (v13). The contract between the agents and the renderer.
- `agents/renderer.py` - **deterministic renderer**: `CaseSpec` -> v13 files. **Prototype #1 done and verified** on two cases (`tests/test_renderer.py`): (1) the **cavity** renders byte-for-byte identical to the golden OpenFOAM.org v13 tutorial, no LLM involved; (2) the **NU assessment** turbulent-channel case (external mesh, inlet/outlet, k-e wall functions, all 5 turbulence fields, adjustable-timestep control) renders as clean v13 - and structurally cannot reproduce the six errors planted in the assessment's hand-written version (`[ ]` FoamFile, `%%` comment, a field missing a patch, `application`/version mismatch, broken separator, missing `;`). Scope: box + `external_mesh` geometry, `incompressibleFluid` + RAS, two numeric presets.
- `agents/llm_client.py` - shared OpenAI-compatible client, pointed at a local endpoint (Ollama by default per `docs/local-llm-backend-comparison.md`; swapping to vLLM/SGLang later is just a base_url/model change).
- `agents/case.py` - in-memory `CaseFiles` representation of an OpenFOAM case (`0/`, `constant/`, `system/`) plus a disk writer; the renderer's output type.
- `agents/meshing_agent.py` - **Meshing Agent**: owns `system/blockMeshDict`. See `docs/openfoam/blockmesh.md`.
- `agents/physics_agent.py` - **Physics Agent**: owns `0/` field files and `constant/physicalProperties` / `constant/momentumTransport` (v13 naming - see `docs/openfoam/case-structure.md`). Boundary patch names it writes must match what the Meshing Agent defined.
- `agents/validation_agent.py` - **Syntax Validation Agent**: runs `blockMesh`/`checkMesh`, classifies failures (`FailureClass`), and is meant to own the retry/feedback loop. Error parsing (`parse_errors`) is currently a naive passthrough - implementing real, localized error extraction here is the single highest-value TODO, per the literature review's FLARE finding that error specificity matters more than the retry mechanism itself.
- `agents/orchestrator.py` - wires the three agents together: Meshing drafts first (patch names come from geometry), Physics drafts fields against those names, Validation runs and routes failures back to the owning agent. All three agents' actual LLM-calling logic (`draft_*`, and a not-yet-added `redraft`) is stubbed with `NotImplementedError`/`TODO` - this is a skeleton, not a working pipeline.

> **Note:** `meshing_agent.py`, `physics_agent.py`, and `orchestrator.py` predate the IR decision - they were written to have the LLM emit *raw dict files*. Under the current architecture they will be refactored so the agents emit **`CaseSpec` fragments** (geometry intent, physics intent) that feed `renderer.py`, and the orchestrator becomes the LangGraph graph. `spec.py` + `renderer.py` are the new core; these three are the pending refactor.

## Open decisions (confirm with supervisor before committing further)

- **Agent framework**: `pyproject.toml` lists LangChain/AutoGen/CrewAI as optional extras but nothing imports any of them yet. The stubs are plain Python/OpenAI-client code on purpose, so the framework choice doesn't have to be made to keep scaffolding.
- **Handoff order**: orchestrator assumes Meshing → Physics (geometry decides patch names). Reasonable default, not verified against how the group actually wants this to work.
- **Who owns `system/controlDict`, `fvSchemes`, `fvSolution`**: not assigned to either agent yet - noted as a TODO in `orchestrator.py`.
- **checkMesh success detection**: some OpenFOAM versions return exit code 0 even with quality warnings; `validation_agent.py` flags this as unverified, needs checking against the actual VM's OpenFOAM install.
