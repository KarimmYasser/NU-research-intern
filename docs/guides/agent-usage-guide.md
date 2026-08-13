# Agent Usage Guide: Running the OpenFOAM v13 CFD Agent

This guide explains how to run the multi-agent OpenFOAM case-generation pipeline, pass natural-language prompts, ground generation on external meshes, and execute native Docker validation.

---

## 1. Quickstart CLI Usage

### Prerequisites & Installation

Activate your Python environment and install all dependencies:

```bash
pip install -r requirements.txt
```

### macOS / Linux (Single-line — Recommended for terminal pasting)

```bash
export PYTHONPATH="src" && python -m src run --mesh "temp/case-files" --case-dir "temp/out/my_case" --prompt-file "temp/case-files/requirements.txt"
```


> [!WARNING]
> On macOS, `python3` often invokes Xcode System Python 3.9 (`/Applications/Xcode-beta.app/...`) rather than the interpreter in the Python environment you created (venv/conda/etc.). Always use **`python`** (not `python3`) from your active environment.

### macOS / Linux (Environment's Python runner)

```bash
.venv/bin/python -m src run --mesh "temp/case-files" --case-dir "temp/out/my_case" --prompt-file "temp/case-files/requirements.txt"
```

### macOS / Linux (Multi-line)

```bash
export PYTHONPATH="src"
python -m src run \
    --mesh "temp/case-files" \
    --case-dir "temp/out/my_case" \
    --prompt-file "temp/case-files/requirements.txt"
```

> [!NOTE]
> The pipeline executes the LLM extraction (`gpt-5-mini`), spec expansion, rendering, and Docker OpenFOAM validation. This process takes ~10–15 seconds and runs silently until completion output is printed.

### Windows (PowerShell)

```powershell
$env:PYTHONPATH="src"
python -m src run `
    --mesh "temp/case-files" `
    --case-dir "temp/out/my_case" `
    --prompt-file "temp/case-files/requirements.txt"
```

### Option A: Read Prompt from a Text File (Recommended)

```bash
python -m src run \
    --mesh "temp/case-files" \
    --case-dir "temp/out/my_case" \
    --prompt-file "temp/case-files/requirements.txt"
```

### Option B: Pass Prompt Directly as a String

```bash
python -m src run \
    --mesh "temp/case-files" \
    --case-dir "temp/out/my_case" \
    --prompt "I'm setting up a steady-state, compressible, turbulent RAS OpenFOAM case for a 60° vortex tube sector..."
```

### Option C: Interactive Prompt Mode (Stdin)

Omitting both `--prompt` and `--prompt-file` starts interactive mode:

```bash
python -m src run --mesh "temp/case-files" --case-dir "temp/out/my_case"
```

*(Paste your text description, then press `Ctrl+D` on macOS/Linux or `Ctrl+Z` followed by `Enter` on Windows).*

---

## 2. CLI Reference & Arguments

| Argument | Required | Description |
|---|---|---|
| `--mesh <path>` | **Yes** | Path to the provided mesh folder (containing `polyMesh/` or `boundary`). |
| `--case-dir <path>` | **Yes** | Directory where rendered OpenFOAM case files will be written. |
| `--prompt "<text>"` | No | Natural-language fluid simulation description. |
| `--prompt-file <path>` | No | File path containing the prompt text. |
| `--max-attempts <int>` | No | Maximum validation-and-retry attempts (default: `3`). |
| `--model <model_name>` | No | LLM model override (e.g., `gpt-4o`, `qwen2.5:7b`). |

---

## 3. Python API Integration

### Primary API: LangGraph Agent StateMachine (`orchestration.graph.run`)

The graph runner is the preferred entry point. It runs the full validation-gated
self-correction loop including the `patch_files` node for targeted syntax fixes.

```python
import sys
from pathlib import Path

sys.path.insert(0, "src")

from llm import LLMConfig
from orchestration import graph

final_state = graph.run(
    nl=Path("temp/case-files/requirements.txt").read_text(),
    mesh_dir=Path("temp/case-files"),
    case_dir=Path("temp/out/my_case"),
    config=LLMConfig(),
    max_attempts=3
)

if final_state.get("intent_error"):
    print("Intent extraction failed:", final_state["intent_error"])
elif final_state["validation"].ok:
    print("Final State OK:", final_state["validation"].ok)
else:
    print("Final State OK:", final_state["validation"].ok)
    for err in final_state["validation"].errors:
        print(f"  [{err.failure_class.name}] {err.file_hint}: {err.message}")
```

The final state also carries the preflight verdict (`final_state["preflight"]`,
a `PreflightResult`) and patch-resolution stats (`final_state["patch_stats"]`).

### Legacy API: `orchestration.pipeline.generate` *(deprecated)*

> **Deprecated.** `generate()` does not run the `patch_files` self-healing node
> and does not clear stale file overrides across retries. Use `graph.run()` instead.

```python
from orchestration.pipeline import generate  # emits DeprecationWarning

result = generate(
    nl=nl_prompt,
    case_dir=Path("temp/out/my_case"),
    mesh_dir=Path("temp/case-files"),
    validate=True
)
```

---

## 4. How the Pipeline Works

The graph executes six nodes in a validation-gated state machine:

```
read_mesh → build_intent → expand_spec → render_case → preflight_case → validate_case
                 ↑                                           │                │
                 └── DICTIONARY_SYNTAX → patch_files ─────────┘                │
                 preflight FAIL / MESH_* / SOLVER_RUNTIME → replay to build_intent
```

1. **`read_mesh`** — Reads the `boundary` file from `--mesh` to learn exact patch names and types. Only the small boundary metadata is read; the full mesh stays on disk.

2. **`build_intent`** — LLM call: NL prompt + real patch names + RAG context + (on retry) the prior error + failing file content → `CaseIntent`. Clears any stale `file_overrides` so retry gets a clean render. If the LLM can't produce a valid intent after the retry loop, the run ends cleanly with an `intent_error` instead of crashing.

3. **`expand_spec`** — Deterministic `CaseIntent → CaseSpec` expansion (no LLM). Looks up per-species thermophysical properties (`fluid_species` field) and tutorial-based numerics from the knowledge base.

4. **`render_case`** — Deterministic `CaseSpec → OpenFOAM files`. Writes `0/`, `constant/`, `system/`, copies `polyMesh/`, then applies any `file_overrides` on top.

5. **`preflight_case`** — Cheap gate before the expensive ladder: static audit, then (if Docker is available) mesh-quality metrics and a bounded solver dry-run that predicts divergence from the residual trend. A known-bad case is caught here and routed back to `build_intent` with the audit report, *before* burning a full `foamRun`. If Docker is unavailable it defers to the ladder. See `docs/architecture/preflight-gate.md`.

6. **`validate_case`** — Runs the ladder inside Docker (`microfluidica/openfoam:13`), with a parallel-capable solver rung and NaN abort (see §6). Parses and classifies errors by `FailureClass`.

### Error routing

| Failure class | Next node | Why |
|---|---|---|
| `DICTIONARY_SYNTAX` | `patch_files` | LLM reads the exact failing file and emits corrected content — targeted fix without discarding a good intent |
| preflight `FAIL` | `build_intent` | doomed case caught cheaply; audit report as evidence |
| `SOLVER_RUNTIME`, `MESH_*` | `build_intent` | Full intent re-extraction with the classified error and file content in context |
| `ENVIRONMENT` | `END` | Docker/infra — nothing the agent can do; surface to operator |

---

## 5. Supported `fluid_species` Values

For compressible cases, the LLM can emit `"fluid_species"` to select thermophysical properties from the built-in library:

| Token | Fluid |
|---|---|
| `air` | Dry air (default) |
| `nitrogen` | N₂ |
| `co2` | CO₂ |
| `oxygen` | O₂ |
| `helium` | He |
| `water_vapour` | H₂O vapour |

These map to `ThermoProperties` (Cp, μ, Pr, Mw) in the `CaseSpec`. If omitted or unrecognised, air defaults are used.

---

## 6. Validation tuning (env vars)

The Docker ladder respects three environment variables:

| Env var | Default | Meaning |
|---|---|---|
| `FOAM_DOCKER_IMAGE` | `microfluidica/openfoam:13` | OpenFOAM v13 container image |
| `FOAM_VALIDATION_TIMEOUT` | `600` | wall-clock timeout per command (seconds) |
| `FOAM_VALIDATION_PROCS` | `1` | ranks for `foamRun -parallel` (1 = serial) |

Setting `FOAM_VALIDATION_PROCS=8` (say) on a multi-core host decomposes the mesh
and runs the solver via `mpirun`, cutting large-case validation from tens of
minutes to a couple. A diverged solver is detected at the first NaN residual and
aborted early, and reported as a `SOLVER_RUNTIME` failure (retried by the agent)
rather than a confusing timeout.

```bash
export FOAM_VALIDATION_PROCS=8 FOAM_VALIDATION_TIMEOUT=900
python -m src run --mesh "temp/case-files" --case-dir "temp/out/my_case" --prompt-file "temp/case-files/requirements.txt"
```
