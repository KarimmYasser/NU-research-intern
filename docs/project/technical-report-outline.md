# Technical Report — Working Outline

Draft scaffold for the final deliverable. **Structure follows the official NU
template** (`temp/report/NU Research Internship Student report.docx`), with the
ConceptNote's technical requirements folded into the body.

- **Authors:** Karim Yasser and Malak Wahid Farid — *joint report, both names.*
- **Format:** drafted here in Markdown, converted to the NU Word/PDF template at the end.
- **Audience:** Dr. Mahmoud Mohamed Shaaban and NU/SESC. **Self-contained** — it must
  stand alone without `docs/` or the repo.
- **Target length:** Executive Summary 5–10 pages, per the template.

Status key: **[ready]** assemble from the cited doc · **[draft]** needs writing ·
**[blocked]** needs a decision or a result first.

> **Two specs, one document.** The NU template asks for an internship report
> (tasks, outcomes, learning). The ConceptNote asks for a technical report (tools,
> architecture, failure modes, scalability). The NU template governs the *structure*;
> the ConceptNote items live inside §2.3 and §2.5 below. Do not write two reports.

---

## 1. Cover page

Template fields, filled where known:

| Field | Value |
|---|---|
| Title | *TODO* — proposed: "An Autonomous Multi-Agent Pipeline for Generating OpenFOAM v13 CFD Cases from Natural Language" |
| Name of Intern | Karim Yasser · Malak Wahid Farid |
| NU ID | *TODO* |
| School / Program | *TODO* — Karim: Computer Engineering, Cairo University |
| Organization | Nile University — Smart Engineering Systems Research Center (SESC) |
| Address / Phone | *TODO* |
| Supervisor | Dr. Mahmoud Mohamed Shaaban — Mashaaban@nu.edu.eg |
| Internship Duration | *TODO* — confirm actual dates (the ConceptNote's 15 Jul–1 Sep is **not fixed**) |
| Project code | SESC-2026-02 |

**Job Responsibilities** (template asks for 4) — **[ready]**:
1. Design and implement the natural-language → OpenFOAM v13 case-generation pipeline.
2. Build the domain knowledge layer (solver/BC/thermophysical registries, v13 tutorial corpus).
3. Implement automated validation: static audit, mesh quality, containerised solver execution.
4. Document the architecture and package the system for reproducible deployment.

**Expected Outcomes** (4) — **[ready]**:
1. A prompt produces a complete, valid, runnable OpenFOAM v13 case directory.
2. Generated cases pass native OpenFOAM checks without fatal dictionary errors.
3. A self-correction loop that diagnoses failures and revises the case automatically.
4. A reproducible, documented codebase with a technical report.

---

## 2. Executive Summary (5–10 pages)

### 2.1 Assigned tasks — bullet points **[ready]**

Mirror the four responsibilities above; keep to one line each. Template explicitly
wants bullets here, prose in 2.2.

### 2.2 Brief description of the assigned tasks **[ready]**

The problem in domain terms: OpenFOAM has no GUI; a case is a strict directory of
interdependent C++-style dictionaries where a single wrong keyword is fatal. Setting
one up demands both CFD knowledge and OpenFOAM-specific syntax knowledge. The task was
to let an engineer describe the problem in English instead.

Note the constraint that shaped every later decision: it must work with a **modest,
self-hostable model**, not only a frontier one.

*Sources:* `docs/domain/openfoam/case-structure.md`, ConceptNote-1.

### 2.3 Work accomplished and outcomes, with illustration **[draft]** — the core

This is the bulk of the report. Suggested order:

**(a) The central design decision — "reason free, constrain late"** **[ready]**
The LLM never writes OpenFOAM syntax. It emits a small typed intermediate
representation (`CaseIntent`); deterministic code renders the dictionaries. Explain
why: free-text generation from a weak model produces plausible, invalid files, and
plausible-but-wrong is the dangerous outcome in CFD.
*Source:* `docs/architecture/phase-ii-architecture.md`, `docs/development/overview.md` §1.

**(b) Architecture** **[ready]** — *ConceptNote: "agent architecture"*
The staged flow `read_mesh → build_intent → expand_spec → render_case → preflight_case
→ validate_case`, with failure-class routing back to the owning stage. Include the
diagram. Cover the knowledge layer (RAG over a real v13 tutorial corpus, BC and
thermophysical registries) and the physical-envelope layer that clamps implausible
numbers instead of trusting them.
*Source:* `docs/development/overview.md` §2, `orchestration.md`, `knowledge-layer.md`.

**(c) Validation — the part that makes it trustworthy** **[ready]**
Correctness is not asserted, it is *tested*: Level A static audit, Level B `checkMesh`
metrics, Level C bounded dry run with residual-slope divergence prediction, then the
full ladder `blockMesh → checkMesh → foamRun` in a real OpenFOAM v13 container, with
NaN abort. Emphasise that the system runs the CFD to judge its own output.
*Source:* `docs/development/validation-and-preflight.md`, `docs/architecture/preflight-gate.md`.

**(d) Two ways to use it** **[ready]** — *lead the demo with this*
A command-line pipeline for automated generation, and an interactive tool server (19
tools + a skill) that plugs into a coding agent, so an engineer can converse with the
system. Same domain core underneath.
*Source:* `docs/development/agent-harness-and-pi.md`.

**(e) Results** **[ready]**
The headline: **from a plain-English prompt plus the supervisor's own 10.6M-face RHVT
mesh, the system produced a compressible turbulent OpenFOAM v13 case that runs on 10
MPI ranks with every residual decaying** — rendered, audited, mesh-checked and executed
without a human writing a dictionary.

Include the residual table (10 steps, log-slopes `Ux` −0.10, `h` −0.15, `k` −0.17 per
step) from `docs/evidence/rhvt-residuals-2026-08-18.md` — it is the single most
convincing artefact in the report. State honestly that **pressure converges slowly**
(0.059 → 0.091 while other fields fall) and that further tuning was out of scope,
since the end user adjusts solver settings himself. Note that the preflight gate
correctly reports WARN rather than PASS for exactly this reason — the system knows what
it does not know.

Supporting numbers (all verifiable): 237 + 8 + 13 automated tests, fully offline;
parallel validation cutting a run from ~20 min to ~1.8 min; ~15× speed-up from bounding
solver iterations; NaN abort within the first solve instead of a full failed run.

**(f) The OpenFOAM v13 problem** **[ready]** — *a strong, distinctive section*
Most published LLM-CFD work and most model training data target OpenFOAM 9 or the ESI
branch. Quantified against the v13 corpus: `application` **0** vs `solver <module>`
**177**; `transportProperties` **0** vs `physicalProperties` **727**;
`turbulenceProperties` **0** vs `momentumTransport` **671**. Describe the three-layer
mitigation and the measured finding that on-demand guidance is insufficient — a
guardrail must be unconditionally present.
*Source:* `docs/supervision/project-status.md` §4, `src/harness/rules/references/v13-vs-older.md`.

**(g) Failure modes** **[ready]** — *ConceptNote requirement*
Summarise by owner: model-side, numerical, infrastructure, agent-guidance. Include
RHVT divergence honestly. Cite the pattern finding: almost all model-side failures
entered through free-text escape hatches, which is direct evidence for the IR design.
*Source:* `docs/development/failure-modes.md`.

**(h) Scalability recommendations** **[draft]** — *ConceptNote requirement*
Grounded suggestions, not speculation: provider abstraction (self-hosted or API behind
one client); parallel validation; bounded solver iterations for large meshes; cheap
preflight before expensive runs; containerised, offline-transferable deployment
(`deploy/`); the registry-driven knowledge layer as the cheapest extension point.
State honestly what has *not* been proven at scale (self-hosted model runs, VM deployment).

### 2.4 Learning outcomes and gained skills **[draft]**

Write personally, not as a feature list. Candidates: CFD/OpenFOAM domain knowledge
(case structure, boundary conditions, solver selection, mesh quality); constrained
generation and why typed IR beats free text for weak models; validation-driven
development; containerised reproducibility; the discipline of verifying claims against
a corpus instead of trusting recall — the v13 audit is the concrete example, and it
caught an error in our own work.

### 2.5 Conclusion and future steps **[draft]**

Conclusion: an engineer describes a problem in English and receives a case that has
been rendered, audited, mesh-checked and executed — with failures diagnosed rather
than hidden.

Future steps: resolve RHVT convergence; validate on a self-hosted model; deploy to the
isolated VM; broaden the case-type coverage; the RL/verifiable-reward direction
(`docs/architecture/rl-for-agent-skills.md`) as a research extension.

---

## 3. Appendix (template allows supporting material)

- **[recommended]** A short screen recording of the interactive session building a
  case — the template explicitly permits video, and this is the most legible artefact.
- Example generated case directory (small box case, complete listing).
- Architecture diagram at full size.
- Repository link + `./setup.sh` / `./start.sh` instructions.

---

## 4. Open items before submission

1. Cover-page facts: NU IDs, school/program for both authors, address/phone, dates.
2. Malak's contribution — written by her, or jointly.
3. One self-hosted model run, to upgrade §2.3(h) and `failure-modes.md` §5 from
   anticipated to observed.
4. Confirm with the supervisor whether the report should present the API-backed
   configuration as the primary mode, given the ConceptNote's "no external API calls"
   wording versus his guidance that the backend is plug-and-play.
