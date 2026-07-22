# OpenFOAM Command Reference

## Meshing and validation

| Command | Purpose |
|---|---|
| `blockMesh` | Generate mesh from `system/blockMeshDict` → writes `constant/polyMesh` |
| `blockMesh -blockTopology` | Dump block edges as OBJ for a quick visual/topology sanity check before full generation |
| `blockMesh -dict <file>` | Use an alternative dictionary instead of `system/blockMeshDict` |
| `blockMesh -region <name>` | Mesh an alternative region (multi-region cases) |
| `checkMesh` | Validate mesh quality: non-orthogonality, skewness, closed-volume checks, etc. |
| `snappyHexMesh` | Generate a complex mesh by snapping a background hex mesh onto an STL surface (out of scope for parametric test cases, but exists) |

## Running a case

- v13 (current OpenFOAM.org release, per `system/controlDict`'s `solver` keyword): the case names its solver module in `controlDict` (e.g. `solver incompressibleFluid;`) and is run with the generic runtime-selectable runner, **`foamRun`** — e.g. `foamRun` (no arguments; it reads `solver` from `controlDict` itself), or `foamRun -help` to confirm the install works. Requires the v13 environment to be sourced first: `. /opt/openfoam13/etc/bashrc` (see `docs/vm-ollama-setup.md` for the full install/verification walkthrough).
- Pre-v12 / most tutorials and papers still circulating (including the ones in this project's literature review): the case is run by directly invoking a solver binary named in `controlDict`'s `application` field — e.g. `icoFoam`, `simpleFoam`, `pimpleFoam`.

Know which convention your generated case is targeting — mixing the two (e.g. an old-style `application icoFoam;` field expecting the new solver framework, or vice versa) will fail to run even if the file syntax itself is valid.

## Case management

| Command | Purpose |
|---|---|
| `foamCleanCase` / `Allclean` scripts | Reset a case to its pre-run state (delete generated time directories, mesh, logs) — useful inside an agent's retry loop so each attempt starts clean |
| `paraFoam` | Open case results in ParaView for visualization |
| `foamListTimes` | List which time directories a case has produced |

## Where these appear in the case structure

See `docs/openfoam/case-structure.md` for what each dictionary (`0/U`, `0/p`, `constant/physicalProperties`, `constant/momentumTransport`, `system/controlDict`, `system/fvSchemes`, `system/fvSolution`) actually contains, and `docs/openfoam/blockmesh.md` for the full `blockMeshDict` breakdown.
