# Project Status — SESC-2026-02

**Living document.** Edit this in place instead of adding dated files; it is the
single answer to "where is the project right now". Last updated: **2026-08-18**.

- **Project:** NL → OpenFOAM v13 CFD case generation, SESC-2026-02
- **Interns:** Karim Yasser, Malak Wahid Farid (joint work; Karim carrying most of
  the implementation)
- **Supervisor:** Dr. Mahmoud Mohamed Shaaban (Mashaaban@nu.edu.eg)
- **Phase:** finalising + drafting the report. The 8-week ConceptNote timeline is
  **not fixed** (supervisor); treat its *deliverables* as binding, not its dates.

> Audience: us. The supervisor gets a **self-contained report**
> ([`../project/technical-report-outline.md`](../project/technical-report-outline.md)),
> not this folder.

---

## 1. Where the project stands

A plain-English prompt becomes an OpenFOAM v13 case that is rendered, statically
audited, mesh-checked, and actually executed in Docker — with failures routed back
for self-correction. That works end-to-end today, two ways:

| | How it is reached | State |
|---|---|---|
| **Pipeline** (`src/orchestration/graph.py`) | `python -m src run "..."` | works end-to-end; the automated path |
| **Tool harness** (`src/harness/`) | `./setup.sh`, `./start.sh` → chat | works end-to-end; 19 tools + skill |

Both sit on one domain core (`ir/`, `rendering/`, `knowledge/`, `preflight/`,
`validation/`), so a fix lands in both. Evidence: **246 Python tests + 8 adapter +
13 installer tests**, all offline; real cases built and run in Docker.

Deliverable-side status against the ConceptNote KPIs:

| KPI | State |
|---|---|
| Produces a 3-folder case from a text prompt | **done** |
| Passes native OpenFOAM checks (`blockMesh` etc.) | **done** — full ladder, not just blockMesh |
| Reproducible codebase + README + dependencies | **done** (`./setup.sh`, `requirements.txt`, `deploy/`) |
| Technical report | **in progress** — outline drafted |
| Local LLM on isolated VM | **deliberately deferred** — see §3 |

## 2. Decisions (settled — do not reopen)

| Decision | Outcome |
|---|---|
| Scope | **CFD case generation.** The "3D Modelling from 2D Drawings" title is void. |
| OpenFOAM version | **v13** (openfoam.org Foundation), not ESI, not v9. |
| Mesh | **Provided externally.** We do not generate or repair real geometry. A simple box mesh exists as a *fallback*, and warns. |
| Reference case | **RHVT** (Ranque-Hilsch vortex tube), mesh supplied by the supervisor: `temp/provided-mesh/3D RHVT (OpenFOAM Files)/` (~10.6M faces). Runs on 10 ranks with all residuals decaying; pressure converges slowly. Further tuning is the end user's job (§3.0). |
| Orchestration framework | **LangGraph** (our choice; alternatives researched in `docs/architecture/agent-framework-comparison.md`). |
| Architecture | **Structured IR + deterministic renderer** — the LLM never writes dictionary text. |
| LLM backend | **Abstracted.** One OpenAI-compatible client serves a self-hosted model (Ollama / llama.cpp) or a hosted API. Not a code-level concern. |
| Check-in cadence | Informal (WhatsApp). No fixed reporting format. |

## 3. Open items

**0. RHVT — runs, converging slowly. Closed.** Verified 2026-08-18 by sweeping every
preserved case through the Docker ladder: `temp/out/_w10_rhvt` on **10 MPI ranks**
completes 10 steps in 131 s with every field's residual decaying (`Ux` −0.10/step,
`h` −0.15, `k` −0.17). Pressure is the laggard (0.059 → 0.091 while the rest fall),
which is exactly the slow convergence observed originally. Residual table:
`../evidence/rhvt-residuals-2026-08-18.md`.

Tuning the pressure solve further is out of scope — the supervisor adjusts solver
settings himself as the end user. **This is a citable result for the report.**

**1. Local-model run never performed.**
No case has been generated with a self-hosted model; all runs used a hosted API. The
supervisor's position is that the backend is plug-and-play, so this is low-risk — but
it is currently an *assumption*, not a result. The honest report claim is
"provider-abstracted, exercised through an API"; a single Ollama smoke run would let
us claim more. See `docs/development/failure-modes.md` §"Anticipated".

**2. Work split with Malak** — joint deliverable, both names. The report needs her
contribution written up; the pipeline is largely Karim's.

**3. VM deployment untested.** `deploy/` (compose + offline image transfer) exists and
is documented, but has never run on a real isolated VM. Not currently blocking.

## 4. Answered: the supervisor's v13 concern

> *He observed that LLM agents get OpenFOAM v13 wrong because most models are
> trained on v9-era material.*

The concern was correct, and it applied to us. Audited the whole repo against the
shipped v13 tutorial corpus (~200 real cases):

| Convention | Pre-v12 | v13 | Corpus count |
|---|---|---|---|
| Solver selection | `application icoFoam;` | `solver <module>;` + `foamRun` | **0** vs **177** |
| Transport props | `constant/transportProperties` | `constant/physicalProperties` | **0** vs **727** |
| Turbulence | `constant/turbulenceProperties` | `constant/momentumTransport` | **0** vs **671** |

The renderer was already correct. The **agent guidance was not** — our own rules file
and one diagnostic told the model to write `application foamRun;`. Both fixed, and
the system is now hardened three ways:

1. A v13-vs-v9 reference the agent can load (`src/harness/rules/references/`).
2. An always-in-prompt guardrail, because guidance the model must *choose* to read is
   not enough — measured: without it the model emitted `application foamRun;` and an
   invented `constant/physicsProperties`.
3. A regression test that fails if pre-v13 conventions reappear in any guidance string.

Guidance is deliberately **structural, not literal** (`solver <module>;`, never a
worked example the model can copy into the wrong case type).

## 5. What to show him

Lead with the **pi harness** — describing a problem in chat and watching it build,
check and run a case is the most legible demonstration of the work. Then the
architecture argument underneath it: the LLM emits a typed intent, code renders the
files, Docker judges the result. Close with the v13 story in §4, which is a direct,
evidenced answer to a concern he raised.

---

*Superseded:* [`first-meeting-prep.md`](first-meeting-prep.md) is the Week-1 record,
kept for history. This file is current.
