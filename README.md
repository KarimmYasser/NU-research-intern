# NU Research Internship — SESC-2026-02

Working repo for the 2026 Nile University Summer Research Internship, track **SESC-2026-02**, at the Smart Engineering Systems Research Center (SESC).

**Goal:** build an autonomous multi-agent AI pipeline that translates plain-English descriptions of a physical problem into a ready-to-run **OpenFOAM** CFD case directory, using a locally hosted LLM (Ollama/vLLM) on an isolated Ubuntu VM — no external API calls.

- **Intern:** Karim Yasser
- **Supervisor:** Dr. Mahmoud Mohamed Shaaban (SESC)
- **Duration:** 15 July – 1 Sept 2026

See `CLAUDE.md` for full project context (objectives, phases, KPIs) intended to orient anyone (human or AI) picking up this repo.

## Structure

- `docs/` — reference material and project documentation:
  - `ConceptNote-1.pdf` — the authoritative project scope, timeline, and KPIs from the supervisor
  - `openfoam-onboarding.md` + `openfoam/` — OpenFOAM case-structure and `blockMesh` onboarding, grounded in real files from the official OpenFOAM Foundation repo
  - `local-llm-backend-comparison.md` — Ollama vs vLLM vs alternatives for the project's local-LLM requirement
- `temp/` — scratch space (downloaded reference repos, literature-review PDFs, exploratory output). Gitignored — not part of version control. See `CLAUDE.md` for the convention.

No application source code yet — this repo currently holds onboarding/reference documentation produced ahead of Phase II (agentic framework implementation).
