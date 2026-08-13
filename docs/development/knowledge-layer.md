# Knowledge Layer

This document covers `src/knowledge/` — the **data + deterministic logic** that
grounds the LLM's choices: the JSON registries, the RAG retriever, the
IntentValidator, the physical-envelope constraint, and the skills. If you want
the agent to "know" more or to trust its numbers less, this is the doc.

## 1. The JSON registries (data, not code)

All files are in `src/knowledge/`. The RAG engine, validator, and envelopes all
read from them; **adding knowledge usually means editing JSON, not code.**

| File | Consumed by | Contents |
|---|---|---|
| `solvers.json` | RAG | v13 solver modules, regimes, required/turbulent fields, descriptions |
| `boundary_conditions.json` | RAG | per-role BC guidance (role, patch_type, description, notes) |
| `bc_types.json` | IntentValidator | 27 known BC type names → applicable fields, required/optional keys |
| `thermophysics.json` | RAG, `intent_to_spec`, envelopes | per-species `mixture` → molWeight, Cp, hf, μ, Pr (6 species) |
| `turbulence_models.json` | IntentValidator | known RAS/LES model names |
| `v13_tutorials.json` | RAG | fvSchemes/fvSolution snippets per tutorial solver/flow |
| `physical_envelopes.json` | envelopes | plausible/reject ranges + rationale per numeric field |

The RAG `formatted` context, BC guidance, and species list are all rendered
directly into the LLM system prompt — these files are the *voice* the model is
grounded in.

## 2. RAG (`rag.py`)

`KnowledgeRetriever` is deliberately **zero-dependency**: keyword-overlap scoring
(2 points per query word found in the JSON-serialized item) over three registries
(solvers, boundary conditions, thermo). `search(query, top_k=3)` returns the top
matches (falling back to the first entries when no hit). Typed lookups:

- `get_species_thermo(name)` — exact-match species lookup.
- `get_tutorial_numerics(solver, flow)` — best-matching tutorial snippets.

`format_prompt_context(query)` renders the retrieved items into a markdown block
appended to the LLM user prompt.

> **Note:** `intent_to_spec()` uses `_RETRIEVER` as a module-level singleton
> (`ir/intent.py:24`) because loading the JSON files on every call wastes I/O on
> every retry. Don't reinstantiate per call.

## 3. Skills (`skills.py`)

Deterministic enrichment passes that run *after* extraction, in
`extract_intent`. Each `BaseSkill` has `is_applicable()` and `enrich_intent()`.

Two built-in skills:

- **`ExternalMeshPatchSkill`** — auto-maps provided-mesh patch names to physics
  roles *from the patch type* (cyclic/symmetry/inlet/outlet/wall), using the name
  only as a fallback heuristic.
- **`PromptPressureSkill`** — reads an explicit operating/ambient pressure out of
  the prompt text ("4 bar", "5 atm", "mbar/MPa/kPa/psi/Pa") when the LLM left
  `p_ambient` at its default of 100000 Pa. Only fires for compressible flow.

`SkillRegistry` holds the ordered list; `apply_skills()` runs every matching
skill in sequence. **Skills mutate the `CaseIntent`** — that is allowed only in
deterministic code post-validation (the LLM JSON path has already been checked).

## 4. IntentValidator (`intent_validator.py`)

Deterministic **semantic validation** between Pydantic and the renderer. All
checks are pure Python against the registries (no LLM, no Docker). Returns
`list[IntentValidationError]`, which the pipeline feeds back to the LLM in the
same retry loop.

Check order (`validate`, `intent_validator.py:83`):

1. **Turbulence model** exists in `turbulence_models.json`.
2. **Per-patch `bc_overrides`** — type name exists in `bc_types.json`, type is
   applicable to the target field, required keys present.
3. **Relaxation factors** in `custom_fv_solution` are in `(0, 1]`.
4. **OpenFOAM dict syntax** — lightweight brace-balance check on
   `custom_fv_schemes` / `custom_fv_solution` bodies.
5. **`custom_fv_solution` blocks for the compressible `fluid` family** — must
   include a `PIMPLE`/`SIMPLE` block (matching `solver_mode`) and a solver entry
   covering the energy field `h`/`he`/`e` (the v13 `fluid` module dies at startup
   with "keyword h is undefined" if missing). `_dict_entry_names()` even parses
   regex entries like `"(U|h|e)"`.
6. **Box-geometry faces** — explicit faces must tile all 6; `faces` with
   `external_mesh` is rejected.
7. **Patch names** must exist in the provided mesh (when one is given).

## 5. Physical envelopes (`envelopes.py` + `physical_envelopes.json`)

The "range of a number instead of model experience" constraint layer — runs as
**Stage 1.5** between Pydantic and the IntentValidator
(`extract_intent`). Every numeric field the LLM emits is bounded by a curated,
physically-plausible envelope:

```json
{ "field": "nu", "units": "m^2/s",
  "min": 1e-7, "max": 1e-1,
  "reject_below": 1e-9, "reject_above": 1e1,
  "rationale": "Kinematic viscosity of real fluids: ..." }
```

Three-way decision per value (`EnvelopeConstraint.apply()`,
`envelopes.py:140`):

- **inside `[min, max]`** → passed through untouched;
- **modestly outside** (within the clamp band) → **clamped** to the nearest
  bound, with a machine-readable note. Velocity vectors are rescaled
  **direction-preserving** (magnitude clamped, components scaled) so a prescribed
  flow angle is never bent; zero velocity is a no-op;
- **outside the reject band** (a units/dimension slip-up, e.g. `nu=1e5`) → an
  `IntentValidationError` with the valid range, fed back into the LLM retry loop;
  box_size/cells components are handled per-axis.

**Envelope provenance — species-derived `nu`:** when `fluid_species` is named,
the `nu` envelope is **computed, not hard-coded** (`_nu_envelope`,
`envelopes.py:91`): with ideal-gas `rho = p·Mw/(R·T)` at the case's
`p_ambient`/`temperature`, `nu_ref = μ/ρ`, and the envelope is a decade band
around it (reject beyond ±2 decades). Audited example: air at 1 atm / 300 K
derives nu_ref ≈ 1.58e-5 m²/s, so `nu=1e-5` passes while `nu=0.5` (a cP-style
slip, ~4 decades above) is **rejected** rather than clamped. Unknown species and
all case-parameter fields (temperature, pressure, box_size, cells, velocity) use
the curated static ranges.

Fields covered: `nu`, `p_ambient`, `temperature`, `surface_tension`, velocities,
`box_size`, `cells`, and per-patch `pressure`/`temperature`.

**Adding an envelope** for a new field = add a JSON entry only; the code picks it
up automatically. Deriving one from species data = add a branch in
`_envelope_for()`.

## 6. Gotchas specific to this layer

- These files are loaded at **module import** by `KnowledgeRetriever` /
  `IntentValidator` / `EnvelopeConstraint`; after editing a JSON file in a running
  process you must re-import the module (tests do this by reconstruction).
- `Introspection`: `IntentValidator._dict_entry_names` is a small brace-aware
  entry-name scanner shared between validator checks — keep it in sync if you
  extend dict-entry parsing.
- The stress is on **cheap, deterministic, local**. Skills, envelopes, and the
  validator are the layers between "the model said a thing" and "the renderer
  trusts it". Never put an LLM call in this layer.