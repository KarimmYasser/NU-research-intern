# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repository is

This is the working directory for a Nile University (NU) Summer Research Internship project, **SESC-2026-02**, under the Smart Engineering Systems Research Center (SESC). It holds reference/onboarding documentation (`docs/`) plus the Phase II architecture spike (`src/`): an NL→OpenFOAM-v13 pipeline that already runs end-to-end against a local LLM + Docker, kept at spike maturity until the VM deployment and the remaining supervisor decisions land.

- **Intern:** Karim Yasser (Computer Engineering, Cairo University)
- **Supervisor:** Dr. Mahmoud Mohamed Shaaban (SESC, Mashaaban@nu.edu.eg)
- **Co-intern on the same track:** Malak Wahid Farid
- **Duration:** 15 July – 1 Sept 2026 (8 weeks)

## Project goal (per `docs/project/ConceptNote-1.pdf`)

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

Docs are organized into topic folders under `docs/`; `docs/README.md` is the index.

- `docs/project/ConceptNote-1.pdf` — authoritative source for the project scope, timeline, and KPIs above; consult it directly for anything not summarized here. **Nothing is ever moved out of `docs/project/`.**
- `docs/domain/openfoam-onboarding.md` — start here for OpenFOAM; indexes `docs/domain/openfoam/{case-structure,blockmesh,command-reference}.md` (all grounded in the real v13 repo, not blog paraphrase).
- `docs/infrastructure/vm-ollama-setup.md` — Phase I Ubuntu VM + OpenFOAM v13 + Ollama walkthrough (the Week 2 KPI).
- `docs/infrastructure/vm-deploy-bundle.md` — the `deploy/` bundle: docker-compose (ollama + pipeline, docker-outside-of-docker) + offline-save/load scripts for one-command bring-up on the isolated VM.
- `docs/infrastructure/local-llm-backend-comparison.md` — Ollama vs vLLM vs alternatives; recommends starting with Ollama.
- `docs/supervision/first-meeting-prep.md` — status + open questions for the supervisor. OpenFOAM version RESOLVED → v13; still open: VM/GPU, work split with Malak, deliverable cadence.
- `docs/architecture/phase-ii-architecture.md` — the Phase II engineering plan: LangGraph pipeline built on a **structured IR + deterministic renderer** ("reason free, constrain late" — the LLM emits a typed `CaseSpec`, code renders guaranteed-valid v13 files), the validation ladder + self-healing loop, and the meshing verdict. Grounded in the Foam-Agent teardown + the Constraint Tax finding; departs from raw-text generation because our model is local/weak.
- `docs/architecture/agent-framework-comparison.md` — framework decision: recommends LangGraph (Pydantic AI runner-up); explains why off-the-shelf agents (Hermes, pi) don't fit.
- `docs/architecture/rl-for-agent-skills.md` — exploratory research thread: applying RL / verifiable-reward self-improvement, using the validation ladder as the reward signal.
- `docs/architecture/self-correction-loop.md` — design of the dry-run self-correction loop: it corrects *intent* not text, and routes failures by owner (LLM-fixable vs renderer bug vs infra vs out-of-scope).
- `docs/architecture/deterministic-constraint-layers.md` — the **physical-envelope constraint layer**: LLM-emitted numbers are clamped into plausible ranges instead of trusted; `nu` is species-derived. Feeds `src/knowledge/envelopes.py`.
- `docs/architecture/preflight-gate.md` — the **preflight gate**: Level A static audit + Level B checkMesh metrics + Level C dry-run divergence prediction, run before the Docker ladder. Feeds `src/preflight/`.
- `docs/progress/progress-log.md` — running milestone log for the `src/` spike; append new sessions at the top.
- `docs/guides/` — how-to guides for two audiences: `engineer-user-guide.md` (use the agent as an engineer, no coding), `developer-guide.md` (extend the pipeline; entry point into `docs/development/`), plus `agent-usage-guide.md` (technical CLI/API quickstart) and `new-workflow.md` (current working decisions).
- `docs/development/` — the **split-by-concern developer reference** for `src/`: `README.md` (index + reading paths), `overview.md` (architecture/data flow), `ir-and-rendering.md`, `orchestration.md`, `knowledge-layer.md`, `validation-and-preflight.md`, `config-and-llm.md`, `extending.md` (extension walkthroughs), `testing-and-conventions.md`.
- `docs/training/` — the Wolf Dynamics OpenFOAM training decks + general NU orientation material (PDFs, OpenFOAM 9 — old convention; mind the v13 gap). Tracked via Git LFS.
- `src/` — **experimental architecture spike (NOT the deliverable)**. Proves the Phase II design end-to-end: a plain-English prompt becomes an OpenFOAM v13 case that actually runs, via a local LLM + Docker OpenFOAM (no VM, no external API). Kept at spike maturity on purpose; the real implementation is (re)built deliberately later. See `src/README.md` for the status banner.
  - `src/ir/case_types.py` — the **case-type registry**: 18 solver families (incompressibleFluid, fluid, fluid_buoyant, incompressible/compressible(-Multiphase)VoF, incompressibleDriftFlux, multiphaseEuler, incompressibleDenseParticleFluid, film/isothermalFilm, isothermalFluid, shockFluid, solidDisplacement, multicomponentFluid, XiFluid, movingMesh). `intent_to_spec` dispatches on it to emit the exact 0/ field set and constant/ layer per family, grounded in the v13 tutorials.
  - `src/knowledge/envelopes.py` + `physical_envelopes.json` — the **physical-envelope constraint layer** (docs/architecture/deterministic-constraint-layers.md): clamps LLM-emitted numbers into plausible ranges; `nu` is derived from species thermo data. Runs as Stage 1.5 in `extract_intent`.
  - `src/preflight/` — the **preflight gate** (docs/architecture/preflight-gate.md): Level A static audit + Level B checkMesh metrics + Level C bounded dry-run divergence prediction, run as `preflight_case` before the full Docker ladder so doomed cases route back to `build_intent` cheaply.
- `deploy/` — the **VM deployment bundle** (docs/infrastructure/vm-deploy-bundle.md): `Dockerfile` (pipeline image with docker CLI), `docker-compose.yml` (ollama + pipeline, path-aligned `/srv/sesc` workspace, docker-outside-of-docker validation), and `offline-save.sh`/`offline-load.sh` for air-gapped transfer.
- `temp/paper-research-agentic-cfd-llm/` — 40-paper literature review (gitignored); see its `findings.md`.
- `temp/rl-agentic-research/` — RL-for-agents research scratch (gitignored); feeds `docs/architecture/rl-for-agent-skills.md`.
- `temp/repr-arch-research/` — case-representation (IR vs raw-text) research scratch (gitignored); feeds `docs/architecture/phase-ii-architecture.md` §2.

## `temp/` — scratch space

`temp/` holds working artifacts that don't belong in the repo proper (downloaded reference pages, one-off scripts, logs, exploratory output). Before writing anything there, create a subdirectory named for what it contains (e.g. `temp/sesc-site-html/` for scraped SESC website pages) and put the scripts, logs, or files for that task inside it, so it stays organized and reusable in future sessions instead of dumping loose files into `temp/` directly.
