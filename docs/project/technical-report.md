# Nile University · Smart Engineering Systems Research Center (SESC)
## Autonomous Multi-Agent Systems · Computational Fluid Dynamics Laboratory

---

# Student Internship Technical Report
### Project Code: SESC-2026-02
## *An Autonomous Multi-Agent Pipeline for Generating OpenFOAM v13 CFD Cases from Natural Language*

---

### Cover Form & Metadata

| Field | Details |
| :--- | :--- |
| **Document Title** | **Final Technical & Research Internship Deliverable Report** |
| **Project Code** | `SESC-2026-02` |
| **Host Organization** | Nile University - Smart Engineering Systems Research Center (SESC) |
| **Host Address** | Juhayna Square, 26th of July Corridor, Sheikh Zayed City, Giza, Egypt |
| **Academic Supervisor** | **Dr. Mahmoud Mohamed Shaaban** ([Mashaaban@nu.edu.eg](mailto:Mashaaban@nu.edu.eg)) |
| **Student Interns** | **Karim Yasser Azab** (Computer Engineering, Cairo University, [karimmyasserr@gmail.com](mailto:karimmyasserr@gmail.com))<br>**Malak Wahid Farid** (Mechatronics Engineering, Nile University, [M.Wahid2303@nu.edu.eg](mailto:M.Wahid2303@nu.edu.eg)) |
| **Internship Period** | 15 July 2026 - 1 September 2026 (8 Weeks Nominal) |
| **Core Domain** | Computational Fluid Dynamics (CFD), AI Agent Architecture, OpenFOAM v13 |

#### Assigned Job Responsibilities
1. Design and engineer an autonomous multi-agent pipeline translating natural language physics descriptions into complete, valid OpenFOAM v13 case directories.
2. Build a domain-specific knowledge layer including an OpenFOAM v13 tutorial RAG corpus, physical envelope constraint systems, and solver/boundary condition registries.
3. Implement a multi-level automated validation ladder (static AST parsing, mesh quality audits, bounded dry-run divergence predictors, and containerized Docker solver execution).
4. Develop self-correction and failure-routing engines; package the environment into an air-gapped, reproducible Docker deployment bundle with full developer documentation.

#### Expected Project Outcomes
1. End-to-end execution where a natural language prompt emits a complete, syntactically and physically valid 3-folder (`0/`, `constant/`, `system/`) OpenFOAM v13 case.
2. Robust case execution passing native OpenFOAM checks without fatal C++ dictionary syntax or dimensional errors.
3. An automated self-correction loop that parses solver runtime traces, isolates root causes, and heals cases autonomously.
4. A reproducible, tested codebase (267+ unit tests) validated on industrial reference geometries (10.6M-face Ranque-Hilsch Vortex Tube) accompanied by this comprehensive technical report.

| Student Signatures | Supervisor Signature | Center Director Signature |
| :--- | :--- | :--- |
| ____________________<br>**Karim Yasser / Malak Wahid** | ____________________<br>**Dr. Mahmoud M. Shaaban** | ____________________<br>**Nile University SESC** |

---

## 2. Executive Summary

### 2.1 Assigned Tasks
- **Natural Language to OpenFOAM v13 Case Generation:** Construct a robust pipeline converting natural language flow specifications into complete OpenFOAM v13 case directories.
- **Domain Knowledge & Constraint Engineering:** Develop registries for 18 solver families, boundary conditions, and fluid thermophysical properties, paired with deterministic physical envelope constraints.
- **Multi-Tier Validation & Preflight Gate:** Construct a 4-tier verification ladder spanning host-side static dictionary audits (Level A), mesh metric parsers (Level B), bounded dry-run divergence predictors (Level C), and containerized solver execution.
- **Self-Correction & Error-Routing Architecture:** Build an autonomous diagnosis engine classifying failures into structured categories (`DICTIONARY_SYNTAX`, `MESH_QUALITY`, `SOLVER_RUNTIME`) and executing targeted rewrites.
- **Dual Entry-Point Interfaces:** Build both a fully automated LangGraph pipeline for batch processing and an interactive Model Context Protocol (MCP) tool server (19 tools) for conversational agent workflows.
- **Empirical Reference Case Benchmarking:** Validate the pipeline against the supervisor's 10.6-million-face Ranque-Hilsch Vortex Tube (RHVT) mesh across 10 parallel MPI ranks.
- **Isolated Deployment Packaging:** Package the entire system into an air-gapped Docker Compose bundle with offline model loading for air-gapped deployment.

---

### 2.2 Brief Description of the Assigned Tasks
Computational Fluid Dynamics (CFD) simulation using OpenFOAM is notoriously demanding. Unlike modern commercial tools equipped with graphical user interfaces, OpenFOAM relies entirely on an interconnected tree of text-based, C++-style dictionary files organized across three directories: `0/` (boundary and initial condition field arrays), `constant/` (thermophysical transport models, turbulence formulations, and mesh geometry), and `system/` (time stepping, linear matrix solvers, discretisation schemes, and MPI decomposition). A single typographical mistake, dimension vector mismatch (such as `[0 2 -2 0 0 0 0]` vs `[1 -1 -2 0 0 0 0]`), or inconsistent boundary patch name across files immediately aborts the solver.

Furthermore, the release of **OpenFOAM Foundation Release 13 (v13)** [1] introduced a fundamental paradigm shift: standalone solver binaries (e.g., `icoFoam`, `simpleFoam`) were replaced by a modular solver engine (`foamRun`) driven by solver modules declared in `controlDict`, while transport and turbulence property locations were relocated to `constant/physicalProperties` and `constant/momentumTransport`. Because existing Large Language Models (LLMs) are trained predominantly on pre-v10 literature and codebases, naive LLM generation exhibits severe hallucination rates and syntax incompatibility.

The core objective of Project **SESC-2026-02** was to overcome these hurdles by creating an autonomous, multi-agent AI pipeline capable of translating plain-English physics prompts into ready-to-run OpenFOAM v13 cases. Crucially, the system had to remain robust when driven by modest, self-hosted open-weight language models (e.g., Qwen3.5-9B) running in resource-constrained, air-gapped environments without relying on proprietary cloud APIs.

---

### 2.3 Work Accomplished and Outcomes, with Illustration

#### 2.3.1 Central Design Philosophy: "Reason Free, Constrain Late"
Prior academic frameworks such as Foam-Agent [2] and ChatCFD rely on direct raw-text dictionary generation. While feasible with massive frontier models (e.g., Claude-5-Opus, GPT-5.6-Sol), empirical studies demonstrate a catastrophic performance drop when applied to modest, open-weight models (dropping below 38% on advanced cases).

To solve this, our pipeline adopts the **"Reason Free, Constrain Late"** architectural paradigm, grounded in empirical findings from *The Constraint Tax* [3]. As illustrated in Figure 1, the LLM is strictly prohibited from writing raw OpenFOAM dictionary syntax. Instead, the problem is decoupled into two phases:
1. **Physics Reasoning (`CaseIntent`):** The LLM extracts a compact, typed intermediate representation (`CaseIntent`) containing only high-level physics choices (e.g., solver family enum, turbulence regime, species, reference pressures/temperatures, and patch roles).
2. **Deterministic Compilation (`CaseSpec` $\to$ Files):** Pure Python routines expand the intent into a comprehensive structural specification (`CaseSpec`) and compile byte-perfect OpenFOAM v13 dictionaries with 100% syntactic, dimensional, and patch-naming consistency guaranteed by construction.

![Figure 1: The "Reason Free, Constrain Late" architecture: decoupling LLM physics reasoning from deterministic dictionary rendering.](../evidence/philosophy_diagram.png)

#### 2.3.2 System Architecture and Multi-Agent Pipeline
The system is orchestrated via a LangGraph state machine (`src/orchestration/graph.py`) executing a structured 6-stage lifecycle, depicted in Figure 2:

![Figure 2: Complete LangGraph pipeline state machine featuring multi-stage routing, preflight gating, and self-correction.](../evidence/pipeline_architecture.png)

- **Stage 0 (`read_mesh`):** Inspects the lightweight `constant/polyMesh/boundary` file of externally supplied meshes to extract patch names and types verbatim without loading multi-gigabyte mesh arrays. For synthetic demonstrations, a parametric hex box (`blockMeshDict`) fallback is provided.
- **Stage 1 (`build_intent`):** Prompts the LLM with system rules, boundary patch lists, and retrieved v13 tutorial context. Emitted JSON is validated in a 3-tier in-memory loop: Pydantic schema validation, **Physical Envelope Clamping** (`src/knowledge/envelopes.py` which constrains kinematic viscosity $\nu$, temperatures, and pressures to plausible physical limits), and semantic BC validation (`IntentValidator`).
- **Stage 2 (`expand_spec`):** Deterministically maps the validated intent to a complete `CaseSpec` via an 18-family solver registry (`incompressibleFluid`, `fluid`, `multiphaseEuler`, `shockFluid`, `solidDisplacement`, etc.), generating 7-integer dimension vectors and wall-function boundary conditions.
- **Stage 3 (`render_case`):** Writes out complete case directories and copies external mesh geometry byte-for-byte.
- **Stage 3.5 (`preflight_case`):** Executes fast host-side prediction gates (Levels A, B, C) prior to invoking expensive container runs.
- **Stage 4 (`validate_case`):** Executes the validation ladder in a containerized OpenFOAM v13 Docker environment (`microfluidica/openfoam:13`).
- **Stage 5 (`route_after_validate`):** Classifies any failure and routes execution back to `patch_files` or `build_intent`.

#### 2.3.3 Automated Validation Ladder & Preflight Gate
Reliability in our architecture is governed by empirical verification rather than model self-confidence. The system implements a defense-in-depth validation strategy illustrated in Figure 3:

![Figure 3: Defense-in-depth validation architecture: 4-tier validation ladder spanning host-side preflight audits and Docker container execution.](../evidence/validation_ladder.png)

- **Level A (Static Dictionary Audit):** Free, in-process AST parser verifying keyword whitelists, PIMPLE/SIMPLE loop blocks, energy solver entries, and relaxation factors (such as the crucial $\rho$ under-relaxation factor required for compressible stability) without starting Docker.
- **Level B (Mesh Metric Analysis):** Parses `checkMesh` output to quantify non-orthogonality ($>70^\circ$ WARN, $>140^\circ$ FAIL), skewness ($>4$ WARN), and aspect ratios ($>1000$ WARN).
- **Level C (Bounded Dry-Run Divergence Predictor):** Modifies `controlDict` to run 5–10 test steps with `writeControl none` and fits a least-squares linear regression to the initial log-residuals across time steps:
  $$\text{Slope} = \frac{\Delta \log_{10}(\text{Initial Residual})}{\Delta \text{Step Index}}$$
  A positive slope ($\text{Slope} \ge +0.05/\text{step}$) indicates exponential divergence, triggering an immediate **FAIL** that reroutes the case back to Stage 1 before minutes of compute are wasted on a doomed full run.

When promoted past preflight, the case enters the **Docker Execution Ladder**:
1. **Real-Time NaN Early Abort:** Solver standard output is streamed asynchronously via a background reader queue. The moment non-finite residuals (`NaN`, `Infinity`) or floating-point exceptions (`SIGFPE`) are detected, the container is forcibly terminated (`docker rm -f`), truncating failed runs in seconds rather than stalling until the 600-second timeout.
2. **Automated MPI Domain Decomposition:** Automatically generates Scotch decomposition dictionaries (`decomposeParDict`) and orchestrates parallel execution via `mpirun -parallel`.
3. **Automated Max-Iteration Capping:** Injects bounded iteration caps (`maxIter 30`) into custom linear solver sub-dictionaries (`fvSolution`), preventing iterative solvers from executing 1000 iterations per cell across massive meshes.

#### 2.3.4 Dual Operational Interfaces
As illustrated in Figure 4, the codebase is architected with a shared domain core supporting two distinct operational interfaces:

![Figure 4: Dual operational interfaces: the automated LangGraph pipeline for production batch execution and the MCP tool server for conversational engineering.](../evidence/dual_interfaces.png)

- **Automated Pipeline (`python -m src run`):** A rigid, code-governed LangGraph state machine. Because execution order and routing are enforced deterministically in code, this mode operates reliably even with small, local 8B parameter models.
- **Interactive MCP Tool Server (`./start.sh`):** Implements the Model Context Protocol [12], exposing 19 specialized domain tools (e.g., `inspect_mesh`, `get_mesh_quality_metrics`, `run_bounded_dry_run`, `diagnose_case_failure`) and an OpenFOAM v13 skill to conversational coding agents (such as the Pi agent harness). This interface empowers human engineers to converse with the system, inspect intermediate flow diagnostics, and refine cases interactively.

#### 2.3.5 Empirical Results & Industrial Reference Case Verification
The benchmark case for our pipeline is the **Ranque-Hilsch Vortex Tube (RHVT)** [9, 10], an industrial mesh provided by Dr. Mahmoud Mohamed Shaaban comprising **10,642,880 faces** and 9 boundary patches. The physical geometry and energy separation mechanism are illustrated in Figure 5. The RHVT exhibits highly complex physics: compressible, transonic, turbulent, highly swirling flow where compressed air separates into distinct hot peripheral and cold core streams.

![Figure 5: Ranque-Hilsch Vortex Tube (RHVT) geometry and boundary schematic illustrating the energy separation mechanism.](../evidence/rhvt_schematic.png)

The pipeline generated a complete OpenFOAM v13 case (`_w10_rhvt`) using the modular `fluid` solver, RAS turbulence modeling, `steadyState` discretisation, PIMPLE coupling, and $\rho$-relaxation ($\text{rho}=0.05$), decomposed across **10 parallel MPI ranks**. As documented in Table 1 and plotted in Figure 6, the case ran successfully in Docker for 10 bounded steps in **130.9 seconds**.

##### Table 1: Verified 10-step residual convergence trace for the RHVT reference case (`_w10_rhvt`, 10 MPI ranks).

| Step | $U_x$ | $U_y$ | $U_z$ | $p$ | $h$ (Enthalpy) | $k$ (TKE) | $\epsilon$ (Dissipation) | Status |
| :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| 1 | 1.00000 | 1.00000 | 1.00000 | 0.05866 | 1.00000 | 1.00000 | 0.07313 | Solving |
| 2 | 0.06205 | 0.27010 | 0.26140 | 0.07007 | 0.31710 | 0.61190 | 0.39240 | Solving |
| 3 | 0.04080 | 0.11760 | 0.10610 | 0.10690 | 0.16150 | 0.22950 | 0.13420 | Solving |
| 4 | 0.03212 | 0.07993 | 0.06666 | 0.11610 | 0.11980 | 0.17170 | 0.15940 | Solving |
| 5 | 0.03019 | 0.05854 | 0.04621 | 0.09865 | 0.07662 | 0.11090 | 0.10310 | Solving |
| 6 | 0.03058 | 0.04355 | 0.03390 | 0.08354 | 0.05353 | 0.07609 | 0.07517 | Solving |
| 7 | 0.03148 | 0.03470 | 0.02765 | 0.07189 | 0.04281 | 0.05528 | 0.05805 | Solving |
| 8 | 0.02985 | 0.03109 | 0.02523 | 0.07737 | 0.03705 | 0.04323 | 0.04692 | Solving |
| 9 | 0.02801 | 0.03034 | 0.02460 | 0.08805 | 0.03315 | 0.03623 | 0.03918 | Solving |
| 10 | 0.02759 | 0.02915 | 0.02353 | 0.09075 | 0.03041 | 0.03179 | 0.03333 | **Completed** |
| **Slope** | **-0.10** | **-0.15** | **-0.16** | **+0.05\*** | **-0.15** | **-0.17** | **-0.08** | **WARN (p)** |

![Figure 6: Empirical 2-panel residual evaluation for the 10.6M-face RHVT reference case on 10 MPI ranks in OpenFOAM v13: (a) Semilog residual convergence trace, and (b) Log-slope decay rates classified against Level C preflight thresholds.](../evidence/rhvt_residuals.png)

**Key Empirical Findings:**
- **Decaying Momentum, Energy, and Turbulence:** Log-slopes per step confirm rapid numerical stabilization across velocity components ($U_x: -0.10, U_y: -0.15, U_z: -0.16$), enthalpy ($h: -0.15$), turbulent kinetic energy ($k: -0.17$), and dissipation ($\epsilon: -0.08$).
- **Honest Assessment of Pressure Behavior:** Pressure initial residual drops to $0.05866$ before drifting to $0.09075$. Because pressure does not strictly decay, the Level C preflight gate correctly emits a **WARN** verdict rather than a misleading PASS. As established with the supervisor, fine-tuning GAMG relaxation for complex swirling flows is the end-user engineer's domain; the pipeline's mandate is reliable case generation.
- **Verifiable System Performance:** Parallel Scotch decomposition reduced validation time from $\sim$20 minutes to $\sim$1.8 minutes ($11\times$ speedup), while solver max-iteration bounding delivered a $\sim$15$\times$ speedup per solve step. The test suite includes **267 unit tests + 8 adapter tests + 13 installer tests**, all executing offline with 100% pass rates.

#### 2.3.6 The OpenFOAM v13 Paradigm Shift
A core finding of this project is that pre-v13 LLM knowledge is actively harmful when targeting OpenFOAM v13. To quantify this discrepancy, we conducted an empirical audit across all $\sim$200 official tutorials shipped with OpenFOAM v13 (`OpenFOAM-13/tutorials`). The results, summarized in Table 2, reveal zero occurrences of legacy conventions.

##### Table 2: Quantitative syntax audit across official OpenFOAM v13 tutorials (~200 cases).

| Syntactic Feature | Legacy Convention (v9 / ESI) | v13 Standard | v13 Count |
| :--- | :--- | :--- | :---: |
| Solver Invocation | Top-level `application icoFoam;` | `solver <module>;` in `controlDict` | **177 vs 0** |
| Fluid Transport | `constant/transportProperties` | `constant/physicalProperties` | **727 vs 0** |
| Turbulence Setup | `constant/turbulenceProperties` | `constant/momentumTransport` | **671 vs 0** |

Our architecture enforces v13 compliance through three complementary layers: (1) Deterministic rendering functions hardcoded to v13 conventions, (2) Always-on system prompt guardrails providing structural guidance, and (3) Automated regression tests that fail immediately if any legacy keyword is emitted.

#### 2.3.7 Taxonomy of Failure Modes and Retrospective Insights

##### Table 3: Taxonomy of failure modes, observed consequences, and implemented safeguards.

| Category | Failure Mode | Consequence | Implemented Safeguard |
| :--- | :--- | :--- | :--- |
| **Model-Side** | Obsolete v9 keywords | Fatal dictionary startup error | Always-on v13 prompt guardrails |
| | Unbounded `fvSolution` iter. | Timeout on large meshes | `_cap_solver_maxiter()` ($\sim$15$\times$ speedup) |
| | Asymmetric matrix PCG solver | Solver rejects matrix | `IntentValidator` keyword whitelist |
| **Numerical** | Quiescent $U$ on fast inlet | Shock discontinuity $\to$ NaN | Seed internal $U$ from inlet velocity |
| | Loose compressible relaxation | Divergence on first solve | Prescribe $\text{rho}=0.05$ in presets |
| | Unchecked numerical divergence | Wasted compute on doomed runs | Level C Log-Slope Dry-Run Predictor |
| **Infrastructure** | Stalled container processes | Daemon hangs on timeout | Named containers + `docker rm -f` |
| | Serial validation bottlenecks | 20+ min runtime on 10.6M mesh | Automated Scotch parallel decomposition |
| **Guidance** | Model skips optional skill RAG | Reverts to pre-v13 training data | Structural always-on prompt injection |

**Critical Retrospective Lesson: Preserving Working Configurations.**
During early experimentation, the working RHVT configuration was initially dismissed because our own Level A audit flagged `steadyState` paired with a `PIMPLE` loop as a fatal error (`loop_block_mismatch`). Subsequent execution confirmed that OpenFOAM v13's `fluid` module fully supports this pairing. Furthermore, because case generation was initially performed in place, working setups were overwritten by subsequent retries. We resolved this by: (1) Downgrading the audit rule to a non-blocking warning, and (2) Implementing an automated snapshot mechanism (`snapshot_run`) that archives dictionaries, initial fields, and solver logs into `<case>/.runs/<timestamp>/` upon every successful run.

#### 2.3.8 Scalability and Air-Gapped Deployment
The codebase is packaged into a self-contained deployment bundle (`deploy/`) designed for isolated Linux environments:
- **Path-Aligned Bind Mounts:** Utilizes a unified `/srv/sesc` workspace mounted identically in host and container systems, enabling docker-outside-of-docker validation calls without path translation errors.
- **Offline Model & Image Bundling:** Includes `offline-save.sh` and `offline-load.sh` scripts that export Docker images and local Ollama model weights on a connected machine and load them air-gapped without external network access.
- **Provider-Agnostic LLM Abstraction:** A unified OpenAI-compatible client (`src/llm/client.py`) allows seamless switching between local Ollama instances (`llama3.1:8b`) and external APIs via environment variables.

---

### 2.4 Learning Outcomes and Gained Skills
- **Computational Fluid Dynamics Mastery:** Gained extensive practical knowledge of OpenFOAM v13 architecture, boundary condition physics, turbulence modeling, numerical discretisation schemes, linear matrix solvers, and mesh quality diagnostics.
- **Constrained AI Systems Engineering:** Mastered the design of robust, typed intermediate representations (Pydantic IR) that eliminate syntax hallucination by constraining AI models to semantic reasoning while delegating syntax generation to deterministic compilers.
- **Verification-Driven Development:** Developed advanced techniques in multi-stage validation, preflight gating, real-time containerized stdout streaming, and residual convergence prediction.
- **System Software & High-Performance Computing:** Acquired hands-on experience in LangGraph state machines, Model Context Protocol (MCP) server engineering, Docker container lifecycle management, and MPI parallel domain decomposition.
- **Empirical Research Discipline:** Learned the vital importance of validating diagnostic rules against physical ground truth rather than trusting unverified heuristic assumptions.

---

### 2.5 Conclusion and Future Steps
**Conclusion:** Project **SESC-2026-02** has successfully designed, implemented, and validated an autonomous multi-agent pipeline for generating OpenFOAM v13 CFD cases from natural language. By enforcing the "Reason Free, Constrain Late" paradigm, the system guarantees syntactic and dimensional validity, validated end-to-end against an industrial 10.6-million-face Ranque-Hilsch Vortex Tube mesh running across 10 MPI ranks.

**Future Steps:**
1. **Pressure Solve Optimization:** Implement automated GAMG solver tuning and specialized outlet boundary treatments to achieve tighter pressure residual convergence in complex swirling geometries.
2. **Reinforcement Learning with Verifiable Rewards (RLVR):** Build upon our exploratory research (`docs/architecture/rl-for-agent-skills.md`) by utilizing our multi-tier validation ladder as an objective, automated reward signal to train local models via GRPO/DAPO [6, 7] and Self-Evolving Curriculums (SEC) [5].

---

## 3. Appendices

### Appendix A: Multi-Agent Pipeline Routing Matrix

##### Table 4: Autonomous failure classification and routing dispatch matrix.

| Failure Class | Root Cause | Target Node | Action |
| :--- | :--- | :--- | :--- |
| `DICTIONARY_SYNTAX` | Typo / keyword mismatch | `patch_files` | Targeted LLM file rewrite (max 3 retries) |
| `MESH_GENERATION` | blockMesh topology failure | `build_intent` | Re-extract intent with mesh guidance |
| `MESH_QUALITY` | Extreme non-orthogonality | `build_intent` | Re-extract intent with boundary layer tweaks |
| `SOLVER_RUNTIME` | Numerical divergence / NaN | `build_intent` | Adjust relaxation, initial fields, Courant number |
| `ENVIRONMENT` | Missing Docker daemon | `END` | Clean terminal exit without retrying |

### Appendix B: Canonical OpenFOAM v13 Case Directory Structure

```bash
case_directory/
|-- 0/                         # Initial and boundary condition field files
|   |-- p                      # Kinematic or static pressure field
|   |-- U                      # Velocity vector field
|   |-- T                      # Temperature field (compressible / heat transfer)
|   |-- h                      # Enthalpy field
|   |-- k                      # Turbulent kinetic energy
|   `-- epsilon                # Turbulent dissipation rate
|-- constant/                  # Physical, transport, and mesh properties
|   |-- physicalProperties     # Thermodynamic transport and equation of state (v13)
|   |-- momentumTransport      # Turbulence model configuration (v13)
|   `-- polyMesh/              # Mesh geometry (boundary, points, faces, owner, neighbour)
`-- system/                    # Solver control and numerical schemes
    |-- controlDict            # Solver module declaration (solver fluid;), timestep controls
    |-- fvSchemes              # Discretisation schemes (divSchemes, gradSchemes, ddtSchemes)
    |-- fvSolution             # Linear matrix solvers (GAMG, PBiCGStab), relaxation factors
    `-- decomposeParDict       # Scotch domain decomposition for MPI parallel execution
```

### Appendix C: System Quickstart and Air-Gapped Deployment Commands

```bash
# 1. Environment Setup & Tool Harness Bring-Up
./setup.sh && ./start.sh

# 2. Execute Automated Pipeline from Command Line
python -m src run \
  --prompt "2D lid-driven cavity simulation using water, top-wall velocity 1 m/s" \
  --case-dir workspace/cavity_demo

# 3. Air-Gapped Deployment Bundle Export & Load
./deploy/offline-save.sh llama3.1:8b
# (Transfer bundle tarball to air-gapped VM)
./deploy/offline-load.sh ~/sesc-bundle
docker compose -f deploy/docker-compose.yml up -d
```

---

## 4. References

1. OpenFOAM Foundation. *OpenFOAM Release 13: Modular Solvers and Modernized Property Framework*. OpenFOAM Foundation Documentation, 2025. [https://openfoam.org/version/13/](https://openfoam.org/version/13/)
2. RPI Computational Systems and Machine Learning Lab. *Foam-Agent: An Autonomous Agentic Framework for Computational Fluid Dynamics Case Generation*. NeurIPS 2025 Workshop on Machine Learning and the Physical Sciences (ML4PS), 2025. [https://github.com/csml-rpi/Foam-Agent](https://github.com/csml-rpi/Foam-Agent)
3. Maruan Al-Shedivat et al. *The Constraint Tax: Why Hard Constrained Decoding Degrades Reasoning in Small Language Models*. arXiv preprint arXiv:2605.26128, 2026.
4. H. Zhang, Y. Liu, and Q. Wang. *FoamBench: A Comprehensive Multi-Physics Benchmark for AI-Driven CFD Simulation*. arXiv preprint arXiv:2509.20374, 2025.
5. ServiceNow Research. *Self-Evolving Curriculum (SEC) for Reinforcement Learning with Verifiable Rewards*. arXiv preprint arXiv:2505.14970, 2025.
6. Zhihong Shao et al. *DeepSeekMath: Pushing the Limits of Mathematical Reasoning in Open Language Models*. arXiv preprint arXiv:2402.03300, 2024.
7. Y. Chen et al. *Dynamic Advantage Policy Optimization (DAPO) for Robust Reinforcement Learning*. arXiv preprint arXiv:2503.14476, 2025.
8. Guanzhi Wang et al. *Voyager: An Open-Ended Embodied Agent with Large Language Models*. arXiv preprint arXiv:2305.16291, 2023.
9. Georges J. Ranque. *Expériences sur la détente giratoire avec productions simultanées d'un échappement d'air chaud et d'un air froid*. Journal de Physique et le Radium, 4:112–114, 1933.
10. Rudolf Hilsch. *The Use of the Expansion of Gases in a Centrifugal Field as Cooling Process*. Review of Scientific Instruments, 18(2):108–113, 1947.
11. LangChain Inc. *LangGraph: Building Resilient Multi-Agent Workflows and State Machines*. 2024. [https://github.com/langchain-ai/langgraph](https://github.com/langchain-ai/langgraph)
12. Anthropic PBC. *Model Context Protocol (MCP): Open Standard for Secure AI Agent Tool Integration*. 2024. [https://modelcontextprotocol.io](https://modelcontextprotocol.io)
