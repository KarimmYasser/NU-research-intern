# IR & Rendering

This document covers the **structured intermediate representation** (the types
that carry the case description) and the **deterministic renderer** that turns it
into OpenFOAM v13 files. If you are adding a solver family, a field, a boundary
condition, or a property dictionary, this is the doc.

## 1. The two-layer IR

There are two levels of description, kept deliberately separate:

- **`CaseIntent`** (`ir/intent.py`) — *the small thing the LLM emits.* Physics
  vocabulary only: case type, regime, flow type, solver mode, species, `nu`,
  pressures, temperatures, geometry, a few numbers, and per-patch roles/overrides.
  Pydantic-validated, then envelope- and semantically-checked before it counts.
- **`CaseSpec`** (`ir/spec.py`) — *the complete structural description*:
  every field with its 7-int dimensions, every patch, every field's boundary
  conditions, the property dictionaries, turbulence, time control, numerics.
  This is what the renderer consumes.

The expander `intent_to_spec()` (`ir/intent.py:503`) maps one to the other
deterministically — the LLM never produces a `CaseSpec` directly.

### The LLM's vocabulary (`CaseIntent`)

The fields the model may set (see `ir/intent.py:62` for defaults):

| Field | Meaning / default |
|---|---|
| `case_type` | solver family name, default `"incompressibleFluid"` (enum in the registry) |
| `regime` | `laminar` / `turbulent`, default `turbulent` |
| `flow_type` | `incompressible` / `compressible`, default `incompressible` |
| `solver_mode` | `transient` / `steadyState`, default `transient` |
| `fluid_species` | `air`, `nitrogen`, `co2`, `oxygen`, `helium`, `water_vapour` |
| `nu` | kinematic viscosity [m²/s], default `1e-05` |
| `p_ambient` | reference pressure [Pa], default `100000` |
| `temperature` | reference temperature [K], default `300` |
| `geometry` | `box` / `external_mesh` |
| `box_size` / `cells` | box dimensions / cell counts |
| `gravity` | overrides the case-type default `(0,-9.81,0)` |
| `phases` / `surface_tension` | VoF / multiphase overrides |
| `turbulence_model` | RAS model name (validated against registry) |
| `custom_fv_solution` / `custom_fv_schemes` | raw dict bodies that override presets |
| `patches` | list of `PatchIntent` (role, velocity, pressure, temperature, faces, `bc_overrides`) |

### The complete model (`CaseSpec`)

`ir/spec.py` defines the structural types the renderer reads (`BoxGeometry`,
`ExternalMesh`, `Field`, `FieldBC`, `Patch`, `TurbulenceRAS`, `ThermoProperties`,
`FluidProperties`, `TimeControl`, and the top-level `CaseSpec`).

A `Field` is a solved field written into `0/`:

```python
Field(name="U", field_class="volVectorField",
      dimensions=(0, 1, -1, 0, 0, 0, 0),   # the 7-int OpenFOAM dimensions
      internal_field="uniform (0 0 0)")
```

## 2. The case-type registry (`ir/case_types.py`)

`CASE_TYPES` maps every **solver-family name** to a `CaseTypeDef` — the
structural facts the renderer needs. This is the map from "a physics family" to
"the v13 dictionary layout it needs," grounded in the real tutorials
(`temp/openfoam-repo/OpenFOAM-13/tutorials`).

Every `CaseTypeDef` records:

| Field | Meaning |
|---|---|
| `solver` | v13 solver **module** name written into `controlDict` |
| `flow_type` | `incompressible` / `compressible` / `solid` |
| `needs_gravity` | writes `constant/g` + switches to `p_rgh` |
| `phases` | default phase names → `alpha.<phase>` fields + `phaseProperties` |
| `per_phase_properties` | `physicalProperties.<phase>` dictionaries |
| `compressible_phases` | per-phase files carry thermo, not ν/ρ |
| `surface_tension` | default σ [N/m] |
| `relative_velocity_model` | drift-flux model |
| `species` | default species for multicomponent / combustion cases |
| `is_solid` / `is_film` | field + property-dict flavour |
| `needs_thermo_transport` | compressible turbulent: `constant/thermophysicalTransport` |
| `default_numerics` | which fvSchemes/fvSolution preset applies |
| `notes` | human-readable rationale |

The 18 families (all dispatch through `intent_to_spec` + `render`):

```
incompressibleFluid  fluid  fluid_buoyant
incompressibleVoF  incompressibleMultiphaseVoF  compressibleVoF  compressibleMultiphaseVoF
incompressibleDriftFlux  multiphaseEuler  incompressibleDenseParticleFluid
film  isothermalFilm  isothermalFluid  shockFluid  solidDisplacement
multicomponentFluid  XiFluid  movingMesh
```

Helpers: `get_case_type(name)` (raises with a known-names list),
`is_gravity_case`, `is_multiphase`, `valid_case_types()`.

> **Gotcha:** `_resolve_case_type()` in `ir/intent.py:344` keeps backward
> compatibility — a caller that only sets `flow_type="compressible"` with the
> default family maps to the `fluid` type automatically.

## 3. `intent_to_spec()` — the deterministic expander

`ir/intent.py:503`. Step by step:

1. Resolve the `CaseTypeDef`. **Solids and films force laminar** regardless of
   `regime`.
2. Estimate inlet `k`/`epsilon` from the max prescribed velocity and box size
   (`_turbulence_values`, `ir/intent.py:194`).
3. Assign box faces to patches (`_assign_faces`, `ir/intent.py:105`):
   - If **any** patch lists `faces` explicitly, *all* must, and together they
     must tile the 6 faces exactly once.
   - Otherwise the canonical role map applies (inlet→xMin, outlet→xMax,
     movingWall→yMax, empty→zMin/zMax, residuals→symmetry/walls, leftovers fold
     into the last wall so the box always closes).
4. Compute the **field set** for the family (`_case_fields`, `ir/intent.py:374`):
   the exact `0/` files and their dimensions, grounded per family.
5. Build each patch's **boundary conditions** for every active field
   (`_bc_for_role` → `_default_bc_for_role`, `ir/intent.py:220`). Per-field rules
   are keyed by a **phase anchor** (`_anchor`, `ir/intent.py:215`) so phase-
   suffixed fields share one rule.
6. Pick the **property layer**:
   - compressible → `ThermoProperties` looked up from `thermophysics.json`
     (species-derived when `fluid_species` is named);
   - incompressible → constant `nu`.
7. Pick the **numerics preset** (by family + steady/transient), then override
   with agent `custom_fv_schemes` / `custom_fv_solution` if present.
8. Assemble the `CaseSpec` (gravity default `(0,-9.81,0)` when the family needs
   it, phases, surface tension, per-phase flags, `species`, film/solid flags).

### BC generation notes (`_default_bc_for_role`)

The per-role BC for each field is derived from the role and field anchor:

| Role | U | p / p_rgh | T | turbulence (k/ε/nut) | alpha |
|---|---|---|---|---|---|
| inlet | fixedValue (velocity) | zeroGradient | fixedValue (T) | fixedValue (k/ε), calculated nut | inletOutlet |
| outlet | zeroGradient | fixedValue (p) | zeroGradient | zeroGradient | zeroGradient |
| wall | noSlip | fixedFluxPressure (comp.) / zeroGradient | zeroGradient / fixedValue | wall functions (kqR, epsilon, nutk) | zeroGradient |
| movingWall | fixedValue (velocity) | fixedFluxPressure / zeroGradient | fixedValue | wall functions (T prescribed) | inletOutlet |
| empty/cyclic | passthrough | passthrough | passthrough | passthrough | passthrough |
| symmetry | symmetryPlane | symmetryPlane | symmetryPlane | symmetryPlane | symmetryPlane |

Committee per-field extras exist for `p_rgh`, `alphat` (wall functions),
`delta` (films, `filmContactAngle`), `D` (solids), and combustion scalars
(`Xi`, `b`, species mass fractions).

### Time control (`_time_control`, `ir/intent.py:618`)

- **Steady state:** `deltaT=1`, `endTime=1000`, no adjust.
- **Transient box:** `adjustTimeStep=yes`, `maxCo=0.5`, first `deltaT` from a
  Courant estimate `0.5·dx/umax`, clamped to `[1e-6, 0.1]`.
- **Transient external mesh:** same adaptivity, conservative `deltaT=1e-4`.

## 4. The renderer (`rendering/renderer.py`)

Pure functions, one per dictionary. All take a `CaseSpec` (or geometry + patches)
and return file **content** (a string). `render(spec)` (`renderer.py:966`)
assembles a `CaseFiles` in memory; `CaseFiles.write(case_dir)` writes it to disk.

| Function | Dictionary |
|---|---|
| `render_blockmeshdict` | `system/blockMeshDict` (single graded hex block, 8 corners) |
| `render_controldict` | `system/controlDict` (solver module, time, functions) |
| `render_fvschemes` | `system/fvSchemes` (preset or custom body) |
| `render_fvsolution` | `system/fvSolution` (preset or custom body) |
| `render_physicalproperties` | `constant/physicalProperties` (single-phase / solid / film) |
| `render_per_phase_physical` | `constant/physicalProperties.<phase>` |
| `render_momentumtransport` | `constant/momentumTransport` (laminar / RAS + coefficients) |
| `render_phaseproperties` | `constant/phaseProperties` (VoF sigma / drift-flux / Euler) |
| `render_g` | `constant/g` (uniformDimensionedVectorField) |
| `render_dynamicmeshdict` | `constant/dynamicMeshDict` (movingMesh) |
| `render_field` | `0/<field>` (dimensions, internalField, boundaryField) |

Key mechanics:

- **Banner + FoamFile header** helpers (`_header`, `renderer.py:48`) keep the
  output byte-identical to the v13 tutorial style (aligned `key   value;` at
  column 16).
- **Block-mesh face winding** is fixed (`_BOX_FACE_VERTS`, `renderer.py:58`) and
  verified against the cavity tutorial — normals point outward.
- **Numerics presets** are named dicts `_FVSCHEMES` / `_FVSOLUTION`, keyed by the
  `NumericsPreset` literal (`incompressibleFluid_limitedLinear`,
  `incompressibleFluid_upwind`, `compressibleFluid_steadyState`,
  `incompressibleVoF`, `compressibleVoF`). The renderer falls back to the first
  preset if a name is unknown — **every preset key you reference must be defined
  in both dicts.**
- **Custom Numerics Sanitization**: When `custom_fv_schemes` or `custom_fv_solution`
  is supplied, `_sanitize_dict_body` strips any existing `FoamFile` blocks, header banners,
  or separators before wrapping with the canonical OpenFOAM header and footer.
- **Per-phase transport defaults** for VoF/Euler families are a small table
  (`_PHASE_NU_RHO`, `renderer.py:772`) — water/air/oil/mercury/sludge/carrier/
  cloud.

### Files assembled by `render()`

- Box → `blockMeshDict`; else no blockMeshDict (external mesh).
- Always: `controlDict`, `fvSchemes`, `fvSolution`.
- `per_phase_properties` → one `physicalProperties.<phase>` per phase; else a
  single `physicalProperties` (or solid/film flavour).
- With phases (and not dense-particle): `phaseProperties`.
- Non-solid: `momentumTransport`.
- `gravity` set → `g`; `movingMesh` → `dynamicMeshDict`.
- One `0/<field>` per spec field.

## 5. Writing files & applying patches (`rendering/case.py`)

`CaseFiles` is the in-memory container — three dicts `{relpath → content}` for
`0/`, `constant/`, `system/`. `write()` creates dirs and writes files.

`apply_file_overrides(case_dir, overrides)` applies the **whole-file patches**
that the `patch_files` node emits:
- Each override key is a case-relative path; its content replaces the file.
- A **nested override** (e.g. an LLM emitting `"system/fvSolution/solvers"`
  when `system/fvSolution` is already a file) is **skipped, not fatal** — the
  parent is a file, not a directory. Returns the skipped paths for logging.
- Surving file content exactly as given (no re-render, no validation here —
  the ladder/audit is the check).

## 6. Adding a new case type — the checklist

See `extending.md` for the full walkthrough; the short version:

1. `ir/case_types.py` — add a `CaseTypeDef` (grounded in a real v13 tutorial).
2. `ir/intent.py` — extend `_case_fields()` / `intent_to_spec()` for any
   non-default field set or property dict.
3. `rendering/renderer.py` — cover any new property layer / phaseProperties /
   gravity / dynamicMeshDict branch in `render()`; ensure the referenced
   `default_numerics` preset exists in `_FVSCHEMES`/`_FVSOLUTION`.
4. `knowledge/solvers.json` — let RAG recommend it.
5. Add a renderer test.

## 7. Gotchas specific to this layer

- **V13 convention:** properties live in `constant/physicalProperties` +
  `constant/momentumTransport` (not `transportProperties`/`turbulenceProperties`),
  and `controlDict` names a solver **module** (`incompressibleFluid`), not a
  binary.
- **Dimensions are 7-int tuples** in OpenFOAM's order
  `[kg m s K mol A cd]` — get the order wrong and the solver silently
  misbehaves or the dictionary fails to parse.
- The renderer is deterministic; any new branch needs a test. It is where silent
  typo regressions hide.
- `apply_file_overrides` deliberately does **not** merge sub-dicts into files —
  overrides are whole-file only. Nested keys are skipped with a log line.