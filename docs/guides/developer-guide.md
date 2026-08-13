# Developer Guide — Extending the OpenFOAM Case-Generation Pipeline

This page is the **entry point** for developers. The full, detailed developer
reference lives in [`../development/README.md`](../development/README.md) —
a split-by-concern set of docs so you can pull up only the part you need.

If you just want to *use* the agent as an engineer, read
[`engineer-user-guide.md`](engineer-user-guide.md) instead — this assumes you are
comfortable with Python, Pydantic, and LangGraph.

> **Status first.** `src/` is a deliberately scoped **architecture spike**, not
> the production deliverable. It proves the Phase II design end-to-end against a
> local LLM + Docker. Expect pragmatic shortcuts. The authoritative engineering
> plan is [`../architecture/phase-ii-architecture.md`](../architecture/phase-ii-architecture.md).

---

## 1. The core idea: "reason free, constrain late"

The pipeline's central bet: **the LLM never writes OpenFOAM dictionary syntax.**
It only emits a small, typed *physics intent* (a `CaseIntent`), and deterministic
code turns that into guaranteed-valid v13 files. This is what makes a **weak,
local** model viable:

```
NL prompt ──LLM──▶ CaseIntent ──deterministic──▶ CaseSpec ──renderer──▶ files
                    (compact)      intent_to_spec()      render()      │
                                                                     ▼
                                               Docker v13 ladder (blockMesh /
                                               checkMesh / foamRun) + self-correction
```

Two more architectural anchors:

1. **The case-type registry** (`src/ir/case_types.py`) — one `CaseTypeDef` per
   solver family (18 families). The main extension point.
2. **The validation ladder** (`src/validation/validation_agent.py`) — the case is
   judged by *actually running the real solver* in Docker; failures are
   classified and routed back to whichever stage owns them.

## 2. Where to read what

| Topic | Doc |
|---|---|
| Full architecture + data flow in one pass | [`../development/overview.md`](../development/overview.md) |
| IR types, case registry, renderer | [`../development/ir-and-rendering.md`](../development/ir-and-rendering.md) |
| LangGraph graph, retry/routing, LLM extraction | [`../development/orchestration.md`](../development/orchestration.md) |
| RAG, IntentValidator, envelopes, skills, JSON data | [`../development/knowledge-layer.md`](../development/knowledge-layer.md) |
| Docker ladder, failure classes, preflight gate | [`../development/validation-and-preflight.md`](../development/validation-and-preflight.md) |
| LLM client, env vars, mesh I/O, CLI | [`../development/config-and-llm.md`](../development/config-and-llm.md) |
| Step-by-step extension walkthroughs | [`../development/extending.md`](../development/extending.md) |
| Tests, conventions, gotchas | [`../development/testing-and-conventions.md`](../development/testing-and-conventions.md) |
| CLI + Python API quickstart | [`agent-usage-guide.md`](agent-usage-guide.md) |
| Full repository & module map (including tests) | [`../development/README.md`](../development/README.md) |

## 3. Quick roadmap for your first change

1. Read [`../development/overview.md`](../development/overview.md) (10 minutes) —
   know how a prompt becomes a validated case.
2. Run the suite: `pytest -q` (141 tests, offline).
3. Head to your concern: new solver family → `ir-and-rendering.md` §6 +
   `extending.md` §1; new knowledge → `knowledge-layer.md` + `extending.md` §2–5;
   validation behavior → `validation-and-preflight.md` + `extending.md` §6.
4. Follow the "goto" recipes in [`../development/extending.md`](../development/extending.md)
   and add the matching test.

## 4. The current working decisions

See [`new-workflow.md`](new-workflow.md) for the live decisions (scope, mesh
rules, framework, backend) and [`../progress/progress-log.md`](../progress/progress-log.md)
for the milestone log of the `src/` spike.