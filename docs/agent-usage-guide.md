# Agent Usage Guide: Running the OpenFOAM v13 CFD Agent

This guide explains how to run the multi-agent OpenFOAM case-generation pipeline, pass natural-language prompts, ground generation on external meshes, and execute native Docker validation.

---

## 1. Quickstart CLI Usage

Make sure your virtual environment (`.venv`) is set up and `PYTHONPATH` includes `src/`.

### Option A: Read Prompt from a Text File (Recommended)

```powershell
$env:PYTHONPATH="src"
.\.venv\Scripts\python.exe -m agents run `
    --mesh "temp/case-files" `
    --case-dir "temp/out/my_case" `
    --prompt-file "temp/case-files/requirements.txt"
```

### Option B: Pass Prompt Directly as a String

```powershell
$env:PYTHONPATH="src"
.\.venv\Scripts\python.exe -m agents run `
    --mesh "temp/case-files" `
    --case-dir "temp/out/my_case" `
    --prompt "I'm setting up a steady-state, compressible, turbulent RAS OpenFOAM case for a 60° vortex tube sector..."
```

### Option C: Interactive Prompt Mode (Stdin)

Omitting both `--prompt` and `--prompt-file` starts interactive mode:

```powershell
$env:PYTHONPATH="src"
.\.venv\Scripts\python.exe -m agents run --mesh "temp/case-files" --case-dir "temp/out/my_case"
```
*(Paste your text description, then press `Ctrl+Z` followed by `Enter` on Windows).*

---

## 2. CLI Reference & Arguments

| Argument | Required | Description |
|---|---|---|
| `--mesh <path>` | **Yes** | Path to the provided mesh folder (containing `polyMesh/` or `boundary`). |
| `--case-dir <path>` | **Yes** | Directory where rendered OpenFOAM case files will be written. |
| `--prompt "<text>"` | No | Natural-language fluid simulation description. |
| `--prompt-file <path>` | No | File path containing the prompt text. |
| `--max-attempts <int>` | No | Maximum validation-and-retry attempts (default: `3`). |
| `--model <model_name>` | No | LLM model override (e.g., `gpt-5-mini`, `qwen2.5:7b`). |

---

## 3. Python API Integration

### High-Level Pipeline (`pipeline.generate`)

```python
import sys
from pathlib import Path

# Add src to python path
sys.path.insert(0, "src")

from agents.pipeline import generate

# 1. Load prompt text
nl_prompt = Path("temp/case-files/requirements.txt").read_text()

# 2. Execute pipeline
result = generate(
    nl=nl_prompt,
    case_dir=Path("temp/out/my_case"),
    mesh_dir=Path("temp/case-files"),
    validate=True  # Runs Docker checkMesh & foamRun validation
)

print(f"Validation Status: {result.validation.ok}")
print(f"Stage Reached: {result.validation.stage}")
```

### LangGraph Agent StateMachine (`graph.run`)

To run the full validation-gated self-correction graph:

```python
import sys
from pathlib import Path

sys.path.insert(0, "src")

from agents import graph
from agents.llm_client import LLMConfig

final_state = graph.run(
    nl=Path("temp/case-files/requirements.txt").read_text(),
    mesh_dir=Path("temp/case-files"),
    case_dir=Path("temp/out/my_case"),
    config=LLMConfig(),
    max_attempts=3
)

print("Final State OK:", final_state["validation"].ok)
```

---

## 4. How the Pipeline Works

1. **Mesh Patch Grounding:** Reads `boundary` file from `--mesh` directory to learn exact patch names and geometric types (`wall`, `cyclic`, `patch`).
2. **RAG Knowledge Search:** Queries `src/knowledge/v13_tutorials.json` (indexed from 252 official OpenFOAM v13 tutorials) to ground the prompt with authentic v13 solver names, field dimensions, and boundary condition guidelines.
3. **Agent Skill Refinement:** Applies modular skills (`CompressibleSwirlingFlowSkill`, `ExternalMeshPatchSkill`) to map rotational symmetry boundaries to `cyclic` and select steady-state SIMPLE presets.
4. **IR CaseSpec Expansion & Rendering:** Converts intent into a typed `CaseSpec` (Pydantic) and deterministically renders guaranteed-valid C++ dictionary files (`0/*`, `constant/*`, `system/*`).
5. **Docker Validation Ladder:** Runs OpenFOAM v13 (`microfluidica/openfoam:13`) inside Docker to execute `checkMesh` and `foamRun`.
