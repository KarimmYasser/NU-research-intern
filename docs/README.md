# docs/ — Project Documentation Index

Reference/onboarding documentation for the SESC-2026-02 internship, classified by topic.

- `project/` — the authoritative scope, timeline, and KPIs. *Nothing is ever moved out of this folder.*
  - `technical-report-outline.md` — working outline for the final report, structured to
    the official NU template (`temp/report/`). Timeline in the ConceptNote is **not fixed**.
  - `ConceptNote-1.pdf` — the source of truth for the project scope, timeline, and KPIs.
- `domain/` — OpenFOAM domain knowledge (grounded in the real OpenFOAM Foundation v13 repo, not blog paraphrase).
  - `openfoam-onboarding.md` — start here for OpenFOAM; indexes `openfoam/{case-structure,blockmesh,command-reference}.md`.
- `infrastructure/` — Phase I infrastructure and the local-LLM decision.
  - `vm-ollama-setup.md` — Ubuntu VM + OpenFOAM v13 + Ollama walkthrough (the Week 2 KPI).
  - `vm-deploy-bundle.md` — the `deploy/` docker-compose + offline-transfer bundle for one-command bring-up on the isolated VM.
  - `local-llm-backend-comparison.md` — Ollama vs vLLM vs alternatives.
- `architecture/` — the Phase II engineering plan and its research threads.
  - `phase-ii-architecture.md` — the structured-IR + deterministic-renderer pipeline plan, validation ladder, and meshing verdict.
  - `openfoam-v13-capabilities.md` — OpenFOAM Foundation v13 source-grounded capability matrix, executable evidence tooling, controlled remediation registry, and Safe Exit boundaries.
  - `preflight-gate.md` — the cheap fail-fast gate (static audit + checkMesh metrics + dry-run divergence prediction) that runs before the full Docker ladder.
  - `deterministic-constraint-layers.md` — the numeric-envelope constraint layer: LLM numbers are clamped into physically-plausible ranges (species-derived for `nu`) instead of trusted; feeds `src/knowledge/envelopes.py`.
  - `agent-framework-comparison.md` — LangGraph decision and why off-the-shelf agents don't fit.
  - `self-correction-loop.md` — dry-run self-correction design (corrects intent, routes failures by owner).
  - `rl-for-agent-skills.md` — RL / verifiable-reward self-improvement research thread.
- `supervision/` — supervisor-facing status and open questions.
  - `project-status.md` — **the living status doc**: current state, settled decisions,
    open items (RHVT divergence, local-model run), and the answer to the v13 concern.
  - `first-meeting-prep.md` — Week-1 record, superseded by the above; kept for history.
- `evidence/` — captured run artefacts worth keeping: `rhvt-residuals-2026-08-18.md` is the
  verified residual trace for the supervisor's reference case (10 ranks, all fields decaying).
- `progress/` — session-by-session milestone and findings logs.
  - `progress-log.md` — running log for the `src/` spike; append new sessions at the top.
  - `findings-2026-08-12.md` — a detailed session/findings record.
- `guides/` — practical how-to-run guides for the `src/` pipeline.
  - `engineer-user-guide.md` — start here if you just want to *use* the agent
    (mechanical engineers, no coding required).
  - `developer-guide.md` — the developer entry point; links to the full
    `development/` reference.
  - `agent_harness_quickstart.md` — **the no-coding path for the tool harness**:
    `./setup.sh` then `./start.sh` to drive OpenFOAM from an AI assistant.
  - `agent-usage-guide.md` — technical CLI/API quickstart; `new-workflow.md` —
    the current working decisions and workflow recap.
- `development/` — the split-by-concern developer reference for `src/`
  (architecture/data flow, IR & rendering, orchestration, knowledge layer,
  validation & preflight, config/LLM/mesh, extension recipes, testing &
  conventions). Start at `development/README.md`.
  - `failure-modes.md` — catalogue of what actually breaks, tagged observed vs
    anticipated; the evidence base for the report's failure-mode section.
  - `agent-harness-and-pi.md` — the **second entry point**: the MCP tool server and
    the pi adapter/skill, how it differs from the LangGraph pipeline (nothing
    enforces the workflow), and the v13-vs-v9 guardrail.
- `training/` — Wolf Dynamics OpenFOAM training decks + general NU orientation PDFs (OpenFOAM 9 — old convention; mind the v13 gap). Tracked via Git LFS.

For the engineering implementation, see `src/` (the Phase II architecture spike) and its `src/README.md`. For overall project context, see the repo `README.md` and `CLAUDE.md`.