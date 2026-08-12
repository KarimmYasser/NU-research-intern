# docs/ — Project Documentation Index

Reference/onboarding documentation for the SESC-2026-02 internship, classified by topic.

- `project/` — the authoritative scope, timeline, and KPIs. *Nothing is ever moved out of this folder.*
  - `ConceptNote-1.pdf` — the source of truth for the project scope, timeline, and KPIs.
- `domain/` — OpenFOAM domain knowledge (grounded in the real OpenFOAM Foundation v13 repo, not blog paraphrase).
  - `openfoam-onboarding.md` — start here for OpenFOAM; indexes `openfoam/{case-structure,blockmesh,command-reference}.md`.
- `infrastructure/` — Phase I infrastructure and the local-LLM decision.
  - `vm-ollama-setup.md` — Ubuntu VM + OpenFOAM v13 + Ollama walkthrough (the Week 2 KPI).
  - `local-llm-backend-comparison.md` — Ollama vs vLLM vs alternatives.
- `architecture/` — the Phase II engineering plan and its research threads.
  - `phase-ii-architecture.md` — the structured-IR + deterministic-renderer pipeline plan, validation ladder, and meshing verdict.
  - `agent-framework-comparison.md` — LangGraph decision and why off-the-shelf agents don't fit.
  - `self-correction-loop.md` — dry-run self-correction design (corrects intent, routes failures by owner).
  - `rl-for-agent-skills.md` — RL / verifiable-reward self-improvement research thread.
- `supervision/` — supervisor-meeting preparation and open questions.
  - `first-meeting-prep.md` — status + open questions (VM/GPU, Malak work split, cadence).
- `progress/` — session-by-session milestone and findings logs.
  - `progress-log.md` — running log for the `src/` spike; append new sessions at the top.
  - `findings-2026-08-12.md` — a detailed session/findings record.
- `guides/` — practical how-to-run guides for the `src/` pipeline.
  - `agent-usage-guide.md`, `new-workflow.md`.
- `training/` — Wolf Dynamics OpenFOAM training decks + general NU orientation PDFs (OpenFOAM 9 — old convention; mind the v13 gap). Tracked via Git LFS.

For the engineering implementation, see `src/` (the Phase II architecture spike) and its `src/README.md`. For overall project context, see the repo `README.md` and `CLAUDE.md`.