# Prep for First Meeting with Dr. Shaaban

## What's done so far (Week 1, before infrastructure exists)

- Read and internalized `ConceptNote-1.pdf` — 8-week plan, 4 phases, KPIs.
- OpenFOAM onboarding (`docs/openfoam/`) — case structure (`0/`, `constant/`, `system/`) and `blockMesh`, verified against the actual OpenFOAM Foundation v13 repo (not just tutorials/blog posts).
- Literature review (`temp/paper-research-agentic-cfd-llm/`) — 40 papers covering the existing LLM+OpenFOAM agent systems (OpenFOAMGPT, Foam-Agent, MetaOpenFOAM, ChatCFD, SwarmFoam, etc.), self-correction/retry-loop design, and structured-JSON-output reliability on local models.
- Local LLM backend comparison (`docs/local-llm-backend-comparison.md`) — Ollama vs vLLM vs SGLang, with a recommendation (start with Ollama for the Week 2 milestone, since it's hardware-agnostic).
- Private GitHub repo set up: https://github.com/KarimmYasser/NU-research-intern

## Open questions worth asking him directly

**Scope / title confusion**
- The original track title was "3D Modelling from 2D Drawings"; the assessment email and concept note both describe CFD case generation instead. Worth a direct, quick confirmation that the concept note is the final scope and the geometry side is explicitly out of scope — rather than assuming.

**OpenFOAM version** — ✅ RESOLVED at meeting: target **v13**.
- The current OpenFOAM.org release (v13) renamed key files (`transportProperties`→`physicalProperties`, `turbulenceProperties`→`momentumTransport`) and changed how cases are run (`controlDict`'s `solver` field replaces picking a binary like `icoFoam`). Nearly all published LLM-agent papers target the *older* convention.
- **Outcome:** the project targets v13. Gotcha to carry forward: the supervisor's own onboarding decks (`docs/OpenFOAM® Introductory Training.pdf`, `docs/Introduction to the FVM method.pdf`) are **Wolf Dynamics / OpenFOAM 9** (old convention), as is the entire literature review — so both learning sources pull toward the old filenames while the pipeline must emit v13. See `docs/openfoam-onboarding.md` for the translation.

**VM / hardware**
- Does the isolated Ubuntu VM have a GPU? This directly decides Ollama-only-CPU vs vLLM/SGLang being viable, and which local models are even usable.
- Who provisions it — IT, or do you set it up yourselves? Timeline for access, since Phase I's Week 2 milestone depends on it.

**Agent framework**
- Concept note lists LangChain/AutoGen/CrewAI as examples, not a mandate — does he have a preference, or is that intern's choice?

**Working with Malak**
- Same track, same code (SESC-2026-02) — how does he want the work split between the two of you? Shared repo, separate components, pair on the same pipeline? Worth settling before either of you starts writing code independently.

**Deliverable cadence**
- Concept note gives 8-week phase milestones but no mention of weekly check-in format — worth asking what he expects between now and Week 2 (demo, written update, etc.).

## Suggested framing for the meeting

Lead with the status summary above (shows initiative, gives him something concrete to react to), then work through the open questions — particularly the OpenFOAM-version and VM/hardware ones, since those are blocking decisions for Phase I, not just nice-to-knows.
