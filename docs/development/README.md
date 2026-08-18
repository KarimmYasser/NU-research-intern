# Developer Documentation Index

This folder is the **developer-facing reference** for the `src/` pipeline —
the IR types, the renderer, the knowledge layer, the validation ladder, the
preflight gate, and the LangGraph orchestration that glues them together. It is
split by *concern* so you can pull up only the doc you need.

If you just want to *run* the agent (no coding), read
[`../guides/engineer-user-guide.md`](../guides/engineer-user-guide.md) instead.
For the CLI/API quickstart, see
[`../guides/agent-usage-guide.md`](../guides/agent-usage-guide.md).

> **Two entry points.** Most of this folder describes the `src/` pipeline, run from
> the command line and driven by a local LLM through LangGraph. There is a second
> way in — the **tool harness**
> (`src/harness/`), which exposes the same domain core to an external coding agent
> such as pi. See [`agent-harness-and-pi.md`](agent-harness-and-pi.md); §2 explains
> why the two have very different ideas about who owns the workflow.

> **Status first.** `src/` is a deliberately scoped **architecture spike**, not
> the production deliverable. It proves the Phase II design end-to-end. The
> authoritative engineering plan is
> [`../architecture/phase-ii-architecture.md`](../architecture/phase-ii-architecture.md);
> this folder documents *what the code actually does today*, including pragmatic
> shortcuts.

## Reading paths — which doc do you need?

| You want to...                                            | Go to                                    |
|-----------------------------------------------------------|------------------------------------------|
| Understand the whole design in ~10 minutes                | `overview.md` + `orchestration.md`       |
| ...then see the data flow end-to-end                      | `overview.md` §2                         |
| Add a **new solver family / case type**                   | `ir-and-rendering.md` §6, `extending.md` |
| Understand the field/BC/geometry IR                       | `ir-and-rendering.md`                    |
| Understand `intent_to_spec()` / the renderer              | `ir-and-rendering.md`                    |
| Make the pipeline "know" more (RAG, species, BCs)         | `knowledge-layer.md`                     |
| Constrain agent numbers (envelopes)                        | `knowledge-layer.md` §5                  |
| Understand self-correction / the LangGraph               | `orchestration.md`                       |
| Debug a **validation failure / the Docker ladder**        | `validation-and-preflight.md` §1–2       |
| Understand the preflight gate (audit / metrics / dry run)  | `validation-and-preflight.md` §3         |
| Configure the LLM backend / env vars                      | `config-and-llm.md`                      |
| Check what tests exist and how testing works              | `testing-and-conventions.md`             |
| See what actually breaks (and what stops it)              | `failure-modes.md`                       |
| Walk through a concrete extension end-to-end              | `extending.md`                           |
| Work on the **tool harness / pi integration**             | `agent-harness-and-pi.md`                |
| Understand why the harness has *no* enforced workflow     | `agent-harness-and-pi.md` §2             |
| Change what the agent is told about **v13 vs v9**          | `agent-harness-and-pi.md` §6             |

## Module map (one line each)

```
src/
├── __main__.py             CLI entry point (`python -m src run ...`)
├── ir/
│   ├── case_types.py       18 solver-family registry (CaseTypeDef)
│   ├── intent.py           CaseIntent (the LLM emits this) + intent_to_spec()
│   └── spec.py             CaseSpec + all sub-models (Field, Patch, FieldBC, ...)
├── rendering/
│   ├── renderer.py         deterministic CaseSpec -> v13 dictionary files
│   └── case.py             CaseFiles in-memory container + apply_file_overrides()
├── mesh/
│   └── mesh_io.py          external polyMesh: boundary parse + byte copy
├── knowledge/
│   ├── rag.py              zero-dep keyword-overlap RAG over the JSON registries
│   ├── intent_validator.py semantic checks before the LLM's JSON is trusted
│   ├── envelopes.py        physical-envelope constraint (clamp numbers)
│   ├── skills.py           deterministic intent-enrichment skills
│   └── *.json  (data, not code) solvers / BCs / thermodynamics / turbulence /
│                bc_types / v13_tutorials / physical_envelopes
├── llm/
│   └── llm_client.py       OpenAI-compatible client (Ollama/vLLM/gateway)
├── validation/
│   ├── validation_agent.py Docker ladder (serial or mpirun -parallel) + NaN abort
│   └── snapshot.py         archives the config + log of a run that passed
├── preflight/
│   ├── audit.py            Level A: static dictionary audit (no Docker)
│   ├── metrics.py          Level B: quantitative checkMesh metrics
│   ├── dry_run.py          Level C: residual-slope divergence predictor
│   ├── ofdict.py           tolerant OpenFOAM-dict parsing helpers
│   ├── runner.py           run_preflight_with_agent() A->B->C orchestration
│   └── __init__.py         PreflightResult / PreflightVerdict / run_preflight
├── orchestration/
│   ├── pipeline.py         extract_intent / extract_file_patches (LLM entrypoints)
│   └── graph.py            LangGraph StateGraph (the CURRENT primary pipeline)
└── harness/                2nd entry point: tool server for a coding agent
    ├── mcp_server.py       JSON-RPC 2.0 stdio server, 19 tool schemas
    ├── tools.py            OpenFOAMToolbox (wraps the same domain core)
    └── rules/openfoam.md   canonical agent rules (pi skill symlinks to this)
tests/                      pytest suite (267 tests, offline, ~3s)
  pi_adapter/               node: pi adapter <-> real Python harness
  shell/                    bash: ./setup.sh / ./start.sh regression tests
```

The `knowledge/*.json` files are **data, not code** — the RAG engine, intent
validator, and envelopes all read from them. They are usually the cheapest place
to give the agent more domain knowledge.

## Quick orientation: the one central bet

> **The LLM never writes OpenFOAM dictionary syntax.** It only emits a small,
> typed physics `CaseIntent`; deterministic code (`intent_to_spec` + the
> renderer) turns that into guaranteed-valid v13 files. Then the case is judged
> by *actually running it* in Docker, and failures route back to whichever stage
> owns them.

Everything in this folder is a detail around that bet. Start with `overview.md`.