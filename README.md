# NU Research Internship — SESC-2026-02

Working repo for the 2026 Nile University Summer Research Internship, track **SESC-2026-02**, at the Smart Engineering Systems Research Center (SESC).

**Goal:** build an autonomous multi-agent AI pipeline that translates plain-English descriptions of a physical problem into a ready-to-run **OpenFOAM** CFD case directory, using a locally hosted LLM (Ollama/vLLM) on an isolated Ubuntu VM — no external API calls.

- **Intern:** Karim Yasser
- **Supervisor:** Dr. Mahmoud Mohamed Shaaban (SESC)
- **Duration:** 15 July – 1 Sept 2026

See `CLAUDE.md` for full project context (objectives, phases, KPIs) intended to orient anyone (human or AI) picking up this repo.

## Structure

- `docs/` — reference material and project documentation, classified by topic (see `docs/README.md` for the index):
  - `project/` — the authoritative scope, timeline, and KPIs (`ConceptNote-1.pdf`)
  - `domain/` — OpenFOAM case-structure and `blockMesh` onboarding, grounded in real files from the official OpenFOAM Foundation repo
  - `infrastructure/` — Phase I Ubuntu VM + OpenFOAM v13 + Ollama setup, and the local-LLM backend comparison
  - `architecture/` — the Phase II engineering plan (structured-IR + deterministic renderer), framework decision, self-correction loop, and the RL-for-agent-skills research thread
  - `supervision/` — supervisor-meeting prep and open questions
  - `progress/` — session-by-session milestone and findings logs
  - `guides/` — practical how-to-run guides
  - `training/` — the Wolf Dynamics OpenFOAM decks and general NU material (PDFs)
- `src/` — the Phase II architecture spike: a working NL→OpenFOAM-v13 pipeline (structured IR + deterministic renderer, case-type registry for 18 solver families, Docker validation ladder). See `src/README.md`.
- `temp/` — scratch space (downloaded reference repos, literature-review PDFs, exploratory output). Gitignored — not part of version control. See `CLAUDE.md` for the convention.

The `src/` pipeline runs end-to-end today (NL → validated v13 case via a local LLM + Docker OpenFOAM). The remaining work is model-in-the-loop hardening and the eventual VM deploy — see `docs/README.md` and the progress log.
