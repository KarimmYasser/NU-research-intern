# src/ — Phase II Architecture Implementation

Multi-agent OpenFOAM CFD case-generation pipeline based on a **structured IR + deterministic renderer** architecture ("reason free, constrain late" — see `docs/architecture/phase-ii-architecture.md` §2): the LLM emits a compact validated intent, deterministic code renders guaranteed-valid OpenFOAM v13 files, and the case is validated by *actually running it* in an OpenFOAM v13 Docker container (`microfluidica/openfoam:13`).

## Sub-Package Architecture

- **`ir/`**: Intermediate Representation (`CaseSpec`, `CaseIntent`, and deterministic `intent_to_spec()` expander).
- **`rendering/`**: Deterministic v13 file renderer (`renderer.py`) and `CaseFiles` in-memory container (`case.py`).
- **`mesh/`**: External mesh polyMesh boundary parser and mesh copier (`mesh_io.py`).
- **`knowledge/`**: Local domain knowledge RAG retriever (`rag.py`), validated intents (`intent_validator.py`), the numeric physical-envelope constraint layer (`physical_envelopes.json` + `envelopes.py`), and agent skills engine (`skills.py`).
- **`preflight/`**: Cheap fail-fast gate run before the full ladder — Level A static dictionary audit (`audit.py`), Level B checkMesh metrics (`metrics.py`), Level C bounded dry-run + residual-slope divergence predictor (`dry_run.py`), orchestrated by `run_preflight_with_agent()` (`runner.py`). See `docs/architecture/preflight-gate.md`.
- **`validation/`**: Docker OpenFOAM v13 validation ladder (`blockMesh` -> `checkMesh` -> `foamRun`, serial or `mpirun -parallel`, NaN-abort) and error parser (`validation_agent.py`).
- **`llm/`**: OpenAI-compatible client configuration (`llm_client.py`).
- **`orchestration/`**: LangGraph state graph (`graph.py`) and direct execution pipeline (`pipeline.py`).
- **`harness/`**: the **second entry point** — an MCP tool server (`mcp_server.py`) exposing 19 tools (`tools.py`) over JSON-RPC/stdio, so an external coding agent (pi, Cline, Claude Code) can drive the same domain core interactively. `rules/openfoam.md` is the canonical agent-behaviour spec (the pi skill symlinks to it). See `docs/development/agent-harness-and-pi.md`.
- **`__main__.py`**: CLI entry point (`python -m src`).

### Two entry points, one core

`orchestration/graph.py` and `harness/` are two ways to reach the same
`ir/` + `rendering/` + `knowledge/` + `preflight/` + `validation/` code:

| | `orchestration/graph.py` | `harness/` |
|---|---|---|
| Who decides the next step | code (LangGraph edges) | the model |
| Model needed | weak/local (Ollama) | strong hosted |
| Reliability comes from | the **structure** | the **model** |
| Runs on an isolated VM | yes | no |
| Role | **the deliverable** — running cases | developing/testing this tool |

The pairing is deliberate: a local model hallucinates dictionary syntax and solver
names if allowed to write them, so the graph lets it fill only a typed `CaseIntent`
and renders the files in code; a strong hosted model is trustworthy enough for
open-ended tool use, which makes the harness the productive way to work during development.

A renderer or knowledge fix benefits both. Do not let the harness's freedom leak
into the graph: the graph's rigidity is what makes a small local model viable.

## Running Tests

From the repository root, with your Python environment (venv/conda/whatever you
prefer) active and dependencies installed:

```bash
pytest -q
```
