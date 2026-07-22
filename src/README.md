# src/ — Phase II agent scaffold

Early skeleton for the multi-agent OpenFOAM case-generation pipeline (concept note Phase II). Nothing here runs yet - it's structure to fill in once the local LLM endpoint is up (`docs/vm-ollama-setup.md`) and direction is confirmed with the supervisor (`docs/first-meeting-prep.md`).

## Layout

- `agents/llm_client.py` - shared OpenAI-compatible client, pointed at a local endpoint (Ollama by default per `docs/local-llm-backend-comparison.md`; swapping to vLLM/SGLang later is just a base_url/model change).
- `agents/case.py` - in-memory `CaseFiles` representation of an OpenFOAM case (`0/`, `constant/`, `system/`) plus a disk writer.
- `agents/meshing_agent.py` - **Meshing Agent**: owns `system/blockMeshDict`. See `docs/openfoam/blockmesh.md`.
- `agents/physics_agent.py` - **Physics Agent**: owns `0/` field files and `constant/physicalProperties` / `constant/momentumTransport` (v13 naming - see `docs/openfoam/case-structure.md`). Boundary patch names it writes must match what the Meshing Agent defined.
- `agents/validation_agent.py` - **Syntax Validation Agent**: runs `blockMesh`/`checkMesh`, classifies failures (`FailureClass`), and is meant to own the retry/feedback loop. Error parsing (`parse_errors`) is currently a naive passthrough - implementing real, localized error extraction here is the single highest-value TODO, per the literature review's FLARE finding that error specificity matters more than the retry mechanism itself.
- `agents/orchestrator.py` - wires the three agents together: Meshing drafts first (patch names come from geometry), Physics drafts fields against those names, Validation runs and routes failures back to the owning agent. All three agents' actual LLM-calling logic (`draft_*`, and a not-yet-added `redraft`) is stubbed with `NotImplementedError`/`TODO` - this is a skeleton, not a working pipeline.

## Open decisions (confirm with supervisor before committing further)

- **Agent framework**: `pyproject.toml` lists LangChain/AutoGen/CrewAI as optional extras but nothing imports any of them yet. The stubs are plain Python/OpenAI-client code on purpose, so the framework choice doesn't have to be made to keep scaffolding.
- **Handoff order**: orchestrator assumes Meshing → Physics (geometry decides patch names). Reasonable default, not verified against how the group actually wants this to work.
- **Who owns `system/controlDict`, `fvSchemes`, `fvSolution`**: not assigned to either agent yet - noted as a TODO in `orchestrator.py`.
- **checkMesh success detection**: some OpenFOAM versions return exit code 0 even with quality warnings; `validation_agent.py` flags this as unverified, needs checking against the actual VM's OpenFOAM install.
