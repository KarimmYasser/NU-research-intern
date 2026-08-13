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
- **`__main__.py`**: CLI entry point (`python -m src`).

## Running Tests

From the repository root, with your Python environment (venv/conda/whatever you
prefer) active and dependencies installed:

```bash
pytest -q
```
