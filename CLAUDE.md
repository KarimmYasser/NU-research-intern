# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repository is

This is the working directory for a Nile University (NU) Summer Research Internship project, **SESC-2026-02**, under the Smart Engineering Systems Research Center (SESC). It holds reference/onboarding documentation (`docs/`) plus an early Phase II agent scaffold (`src/`) — but no runnable pipeline yet: the scaffold is stubs, pending the local-LLM VM and the first supervisor meeting. Update the build/run/test guidance here once the pipeline actually executes.

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

## Repo layout & key docs

- `docs/ConceptNote-1.pdf` — authoritative source for the project scope, timeline, and KPIs above; consult it directly for anything not summarized here.
- `docs/openfoam-onboarding.md` — start here for OpenFOAM; indexes `docs/openfoam/{case-structure,blockmesh,command-reference}.md` (all grounded in the real v13 repo, not blog paraphrase).
- `docs/vm-ollama-setup.md` — Phase I Ubuntu VM + OpenFOAM v13 + Ollama walkthrough (the Week 2 KPI).
- `docs/local-llm-backend-comparison.md` — Ollama vs vLLM vs alternatives; recommends starting with Ollama.
- `docs/first-meeting-prep.md` — status + open questions for the supervisor. OpenFOAM version RESOLVED → v13; still open: VM/GPU, work split with Malak, deliverable cadence.
- `docs/phase-ii-architecture.md` — the Phase II engineering plan: LangGraph pipeline built on a **structured IR + deterministic renderer** ("reason free, constrain late" — the LLM emits a typed `CaseSpec`, code renders guaranteed-valid v13 files), the validation ladder + self-healing loop, and the meshing verdict. Grounded in the Foam-Agent teardown + the Constraint Tax finding; departs from raw-text generation because our model is local/weak.
- `docs/agent-framework-comparison.md` — framework decision: recommends LangGraph (Pydantic AI runner-up); explains why off-the-shelf agents (Hermes, pi) don't fit.
- `docs/rl-for-agent-skills.md` — exploratory research thread: applying RL / verifiable-reward self-improvement, using the validation ladder as the reward signal.
- `docs/self-correction-loop.md` — design of the dry-run self-correction loop: it corrects *intent* not text, and routes failures by owner (LLM-fixable vs renderer bug vs infra vs out-of-scope).
- `docs/OpenFOAM® Introductory Training.pdf`, `docs/Introduction to the FVM method.pdf` — Wolf Dynamics training decks the supervisor assigned as onboarding (OpenFOAM 9 — old convention; mind the v13 gap). Tracked via Git LFS.
- `src/` — **experimental architecture spike (NOT the deliverable)**. Proves the Phase II design end-to-end: a plain-English prompt becomes an OpenFOAM v13 case that actually runs, via a local LLM + Docker OpenFOAM (no VM, no external API). Kept at spike maturity on purpose; the real implementation is (re)built deliberately later. See `src/README.md` for the status banner.
- `docs/Internship Orientation Opening.pdf`, `docs/Using AI in Scientific Research.pdf` — general NU material, not project-specific.
- `temp/paper-research-agentic-cfd-llm/` — 40-paper literature review (gitignored); see its `findings.md`.
- `temp/rl-agentic-research/` — RL-for-agents research scratch (gitignored); feeds `docs/rl-for-agent-skills.md`.
- `temp/repr-arch-research/` — case-representation (IR vs raw-text) research scratch (gitignored); feeds `docs/phase-ii-architecture.md` §2.

## `temp/` — scratch space

`temp/` holds working artifacts that don't belong in the repo proper (downloaded reference pages, one-off scripts, logs, exploratory output). Before writing anything there, create a subdirectory named for what it contains (e.g. `temp/sesc-site-html/` for scraped SESC website pages) and put the scripts, logs, or files for that task inside it, so it stays organized and reusable in future sessions instead of dumping loose files into `temp/` directly.
