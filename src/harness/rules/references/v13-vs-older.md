# OpenFOAM v13 vs. older / ESI conventions

Load this before hand-writing any dictionary. Every claim below was verified against the
real v13 tutorial corpus shipped with this harness (`src/knowledge/v13_tutorials.json`,
~200 cases), not from general OpenFOAM knowledge.

Two separate sources of confusion:

- **Version:** most material online predates the v11–v13 restructuring (OpenFOAM 9 and
  earlier). This is what most models were trained on.
- **Branch:** openfoam.com (ESI) and openfoam.org (Foundation) diverged. **This project
  targets the Foundation v13 line.** ESI material is wrong here even when it is recent.

---

## 1. Solver selection — the big one

Pre-v12 picks a solver by running a named binary, declared in `controlDict`:

```
application     icoFoam;        // WRONG for v13
```

v13 declares a **solver module** and runs the generic `foamRun` runner:

```
solver          <module>;   // correct shape — substitute the module for this case
```

`<module>` is a placeholder. Do not copy any single module name from this page into a
case; choose the one matching the physics.

Corpus check: **0** occurrences of an `application` entry; **177** solver-module entries
(`incompressibleFluid` 48, `incompressibleVoF` 33, `fluid` 30, `multiphaseEuler` 27,
`multicomponentFluid` 18, `compressibleVoF` 8, `shockFluid` 8, ...).

Never emit `application`. Call `list_case_types` for the 18 valid module names, and let
`scaffold_case` write the entry when you can. Old binary names translate roughly as:

| Pre-v12 binary | v13 module |
| :--- | :--- |
| `icoFoam`, `simpleFoam`, `pimpleFoam` | `incompressibleFluid` |
| `interFoam` | `incompressibleVoF` |
| `buoyantPimpleFoam`, `buoyantSimpleFoam` | `fluid` (buoyant variants) |
| `rhoCentralFoam` | `shockFluid` |
| `reactingFoam` | `multicomponentFluid` |

Treat this as a starting point, not a lookup you can apply without thinking: confirm the
choice with `search_openfoam_knowledge` for the actual physics being modelled.

## 2. Renamed `constant/` files

| Pre-v12 | v13 | Corpus |
| :--- | :--- | :--- |
| `constant/transportProperties` | `constant/physicalProperties` | 0 vs 727 |
| `constant/turbulenceProperties` | `constant/momentumTransport` | 0 vs 671 |

Both old names are entirely absent from v13. If you write one, the case fails to read.

Typical v13 contents:

```
// constant/physicalProperties
viscosityModel  constant;
nu              1e-05;

// constant/momentumTransport
simulationType  laminar;        // or RAS, with a RAS { model kEpsilon; } block
```

## 3. Things that did NOT change — do not "fix" these

- **Dimension brackets on `nu` are still valid.** Both `nu [0 2 -1 0 0 0 0] 1e-05;` (44
  occurrences) and bare `nu 1e-05;` (21) appear in v13 tutorials. Leave whichever form is
  already in the file alone.
- `system/fvSchemes`, `system/fvSolution`, `system/blockMeshDict` keep their names and
  overall structure.
- `constant/polyMesh/{boundary,points,faces,owner,neighbour}` is unchanged.
- VoF phase fields are still `alpha.<phase>`, e.g. `alpha.water`.

---

## How to avoid guessing entirely

1. `get_tutorial_numerics` returns **real v13 tutorial** `fvSchemes` / `fvSolution`. Use it
   instead of recalling schemes.
2. `search_openfoam_knowledge` returns grounded reference cases for the physics at hand.
3. `read_case_file` on a file `scaffold_case` already produced shows the correct v13 form —
   match it rather than importing conventions from memory.
4. `run_preflight_audit` catches many version-mismatch mistakes before Docker does.

If a user supplies a v9-era or ESI snippet, translate it, apply it, and tell them what you
changed and why.
