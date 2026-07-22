# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repository is

This is the working directory for a Nile University (NU) Summer Research Internship project, **SESC-2026-02**, under the Smart Engineering Systems Research Center (SESC). It currently contains only reference documents (`docs/`) — no source code has been added yet. When implementation starts, update this file with real build/run/test instructions instead of assuming any.

- **Intern:** Karim Yasser (Computer Engineering, Cairo University)
- **Supervisor:** Dr. Mahmoud Mohamed Shaaban (SESC, Mashaaban@nu.edu.eg)
- **Co-intern on the same track:** Malak Wahid Farid
- **Duration:** 15 July – 1 Sept 2026 (8 weeks)

## Project goal (per `docs/ConceptNote-1.pdf`)

Build an autonomous **multi-agent AI pipeline** that translates plain-English descriptions of a physical problem into a ready-to-run **OpenFOAM** CFD case directory, using a locally hosted LLM (no external API calls — infrastructure must stay on an isolated Ubuntu VM).

Key domain facts to keep in mind when working on this project:
- OpenFOAM has no GUI; a case is a strict directory structure (`0/`, `constant/`, `system/`) of text-based C++ dictionary files defining mesh, boundary conditions, fluid properties, and numerical schemes. Small syntax errors break the case.
- The target is the OpenFOAM.org foundation version (not ESI/OpenCFD).
- Local LLM serving is expected via Ollama or vLLM; agent orchestration via a framework such as LangChain, AutoGen, or CrewAI.
- Planned agent topology: a **Meshing Agent**, a **Physics Agent**, and a **Syntax Validation Agent** collaborating to produce the case.
- The workflow must support a self-correction loop: attempt a "dry run" (e.g. `blockMesh`) and feed terminal error output back to the agents.

## Planned phases / milestones (8-week program)

1. **Weeks 1–2 — Infrastructure & Domain Onboarding:** learn OpenFOAM case structure and `blockMesh`; provision the isolated Ubuntu VM; deploy local LLM and confirm the API responds.
2. **Weeks 3–4 — Agentic Framework Initialization:** set up the Python environment and agent framework; design the multi-agent topology; write initial system prompts and file read/write tools.
3. **Weeks 5–6 — Workflow Refinement & Iterative Testing:** feed natural-language simulation prompts through the pipeline; tune prompts/memory/tool-use so generated dictionaries are valid OpenFOAM C++ syntax; implement the dry-run self-correction loop.
4. **Weeks 7–8 — Documentation, Packaging, Handoff:** finalize the codebase to reliably produce runnable case structures; package the environment (Docker, Git, or a frozen virtualenv); deliver a technical report (tools used, agent architecture, failure modes, scalability recommendations).

Corresponding KPIs: working Ubuntu VM + local LLM API (end of Week 2), pipeline produces a basic 3-folder OpenFOAM case from a text prompt (end of Week 4), generated case passes native OpenFOAM syntax checks e.g. `blockMesh` runs without fatal dictionary errors (end of Week 6), and a fully reproducible codebase with README + dependency list plus a technical report (end of Week 8).

## Reference docs in `docs/`

- `ConceptNote-1.pdf` — authoritative source for the project scope, timeline, and KPIs above; consult it directly for anything not summarized here.
- `Internship Orientation Opening.pdf` — general NU internship onboarding/agenda material, not project-specific.
- `Using AI in Scientific Research.pdf` — general guidance on AI use in research, not project-specific.

## `temp/` — scratch space

`temp/` holds working artifacts that don't belong in the repo proper (downloaded reference pages, one-off scripts, logs, exploratory output). Before writing anything there, create a subdirectory named for what it contains (e.g. `temp/sesc-site-html/` for scraped SESC website pages) and put the scripts, logs, or files for that task inside it, so it stays organized and reusable in future sessions instead of dumping loose files into `temp/` directly.
