# OpenFOAM Onboarding — Start Here

Rapid onboarding for Phase I of `ConceptNote-1.pdf`: OpenFOAM's case directory structure and standard meshing via `blockMesh`. Everything here is grounded in real files pulled from the official OpenFOAM Foundation repo (cloned locally to `temp/openfoam-repo/`, see below) — not paraphrased from a website.

## Directed onboarding material (from Dr. Shaaban)

At the first meeting the supervisor handed over two **Wolf Dynamics** training decks (in `docs/`), which are the standard third-party OpenFOAM course — treat these as the assigned Week 1–2 learning path:

- **`OpenFOAM® Introductory Training.pdf`** (2020 Edition) — the hands-on, case-driven course (run the tutorial cases via the `run_*.sh` scripts, learn the CLI).
- **`Introduction to the FVM method.pdf`** (Feb 2022) — the theory course: FVM discretization, Navier–Stokes & pressure–velocity coupling, CFL, steady vs unsteady, residuals, boundary/initial-condition selection. This is also direct domain grounding for the **Physics Agent** — the choices it explains (which BC/IC, which coupling, CFL limits) are exactly what that agent has to get right.

**Version caveat — read this before you start the tutorials.** These decks are built on **OpenFOAM 9** (old convention), but the project targets **v13** (new convention, confirmed with the supervisor). So the file names and run commands you learn from Wolf Dynamics *will not match what the pipeline must generate*, and the training cases won't run as-is on a v13 install. Learn the concepts from them; mentally translate the mechanics per the "one thing that will trip you up" section below.

## Read in this order

1. **[`docs/openfoam/case-structure.md`](openfoam/case-structure.md)** — the `0/`, `constant/`, `system/` anatomy, with every file from the real `cavity` tutorial reproduced and annotated line by line.
2. **[`docs/openfoam/blockmesh.md`](openfoam/blockmesh.md)** — `blockMeshDict` anatomy: the minimal cavity example annotated in full, plus an advanced curved-geometry example, plus the utility's own CLI options straight from its source file.
3. **[`docs/openfoam/command-reference.md`](openfoam/command-reference.md)** — command cheatsheet, including the v13 solver-execution convention (important — see below).

## The one thing that will trip you up immediately

**OpenFOAM v13 restructured how cases are run and named some files differently from almost every tutorial, paper, or LLM-agent system (OpenFOAMGPT, Foam-Agent, etc.) published before this change:**

- `constant/transportProperties` → renamed `constant/physicalProperties` in v13
- `constant/turbulenceProperties` → renamed `constant/momentumTransport` in v13
- Running a case used to mean calling a solver-specific binary (`icoFoam`, `simpleFoam`, `pimpleFoam`) named in `controlDict`'s `application` field. v13 instead names a modular solver via `controlDict`'s `solver` field (e.g. `solver incompressibleFluid;`) and runs it through a generic runner.

The target is confirmed as **v13** (new convention). But note that *both* the sources you'll learn from pull the other way: your literature review (`temp/paper-research-agentic-cfd-llm/`) is built on systems designed against the older convention, **and the supervisor's own Wolf Dynamics training decks are OpenFOAM 9** (see "Directed onboarding material" above). So both your reading and your hands-on practice use old filenames/patterns, while the pipeline must emit new ones. Expect your own agents to default to the old patterns unless you explicitly steer them — the `physics_agent.py` system prompt already bakes in the v13 names for this reason; keep that discipline everywhere rather than discovering the gap when the solver fails to find a file it expects.

## Local reference repo

`temp/openfoam-repo/` is a sparse, shallow clone of the official [OpenFOAM-13](https://github.com/OpenFOAM/OpenFOAM-13) repo, scoped to what's needed for onboarding:

- `tutorials/incompressibleFluid/cavity/` — the full minimal reference case quoted in `case-structure.md`
- `tutorials/incompressibleFluid/pitzDaily/` — a step-flow case, good second example once cavity makes sense
- `tutorials/mesh/blockMesh/` — standalone blockMesh examples including the curved-geometry `pipe` case
- `applications/utilities/mesh/generation/blockMesh/` — the actual `blockMesh` utility source
- `doc/` — official guides

To pull in more of the repo later (e.g. `snappyHexMesh` source, other solver tutorials):
```
cd temp/openfoam-repo
git sparse-checkout add <path>
```

## Suggested first hands-on step

Before writing any agent code: if you get OpenFOAM itself installed on the Ubuntu VM, run the `cavity` tutorial end to end by hand (`blockMesh`, then run the solver, then `paraFoam` to view results) using the real files already sitting in `temp/openfoam-repo/tutorials/incompressibleFluid/cavity/`. It's small enough to fully understand in one sitting and gives you a known-good reference case to diff your agents' output against later.
