# OpenFOAM Onboarding — Start Here

Rapid onboarding for Phase I of `ConceptNote-1.pdf`: OpenFOAM's case directory structure and standard meshing via `blockMesh`. Everything here is grounded in real files pulled from the official OpenFOAM Foundation repo (cloned locally to `temp/openfoam-repo/`, see below) — not paraphrased from a website.

## Read in this order

1. **[`docs/openfoam/case-structure.md`](openfoam/case-structure.md)** — the `0/`, `constant/`, `system/` anatomy, with every file from the real `cavity` tutorial reproduced and annotated line by line.
2. **[`docs/openfoam/blockmesh.md`](openfoam/blockmesh.md)** — `blockMeshDict` anatomy: the minimal cavity example annotated in full, plus an advanced curved-geometry example, plus the utility's own CLI options straight from its source file.
3. **[`docs/openfoam/command-reference.md`](openfoam/command-reference.md)** — command cheatsheet, including the v13 solver-execution convention (important — see below).

## The one thing that will trip you up immediately

**OpenFOAM v13 restructured how cases are run and named some files differently from almost every tutorial, paper, or LLM-agent system (OpenFOAMGPT, Foam-Agent, etc.) published before this change:**

- `constant/transportProperties` → renamed `constant/physicalProperties` in v13
- `constant/turbulenceProperties` → renamed `constant/momentumTransport` in v13
- Running a case used to mean calling a solver-specific binary (`icoFoam`, `simpleFoam`, `pimpleFoam`) named in `controlDict`'s `application` field. v13 instead names a modular solver via `controlDict`'s `solver` field (e.g. `solver incompressibleFluid;`) and runs it through a generic runner.

Since your literature review (`temp/paper-research-agentic-cfd-llm/`) is built on systems designed against the *older* convention, expect your own agents to default to old filenames/patterns unless you explicitly steer them — this is worth deciding deliberately in Phase II rather than discovering by accident when `blockMesh`/the solver fails to find a file it expects.

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
