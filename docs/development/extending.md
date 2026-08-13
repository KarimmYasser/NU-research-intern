# Extending the Pipeline — Walkthroughs

Practical, step-by-step recipes for the most common development task: making the
pipeline handle a new physics family, species, boundary condition, or numeric
constraint. Each recipe lists the exact files to touch and the test to add.

## 1. Add a new case type / solver family

1. **`ir/case_types.py`** — add a `CaseTypeDef` to `CASE_TYPES`:
   - correct v13 solver **module** name (`solver`), `flow_type`;
   - the booleans/tuples/numbers that apply (`needs_gravity`, `phases`,
     `per_phase_properties`, `compressible_phases`, `surface_tension`,
     `relative_velocity_model`, `species`, `is_solid`, `is_film`,
     `needs_thermo_transport`);
   - `default_numerics` → the preset name.
   Ground it in a **real v13 tutorial**, not assumption.
2. **`ir/intent.py`** — if the family needs a non-default field set or property
   dict:
   - extend `_case_fields()` for the new field set;
   - add any per-role BC branch in `_default_bc_for_role()` (keyed by the field's
     phase anchor in `_anchor`);
   - update `intent_to_spec()` for new property/flag plumbing.
3. **`rendering/renderer.py`** — `render()` must produce the case:
   - property layer (single `physicalProperties` vs per-phase vs solid/film);
   - `phaseProperties`, `g`, `dynamicMeshDict` branches;
   - **if `default_numerics` names a preset that doesn't exist yet**, add it to
     both `_FVSCHEMES` and `_FVSOLUTION` (a missing key silently falls back to
     the first preset — that's a silent bug).
4. **`knowledge/solvers.json`** — add the solver so RAG can recommend it.
5. **Test** — add a renderer test asserting the exact output for the new family
   (mirror `tests/test_renderer.py` / `tests/test_compressible_vortex_tube.py`).

## 2. Add a fluid species (e.g. "methane")

1. **`knowledge/thermophysics.json`** — add the species + `mixture` block with
   `specie` (molWeight) / `thermodynamics` (Cp, hf) / `transport` (mu, Pr).
2. **`orchestration/pipeline.py`** — add the token to the `fluid_species` enum in
   both `_SCHEMA` and `_RESPONSE_FORMAT` (the model is told the valid names in
   the JSON schema).
3. **`ir/intent.py`** — no change needed: `intent_to_spec` looks the species up
   by name via `_RETRIEVER.get_species_thermo`.
4. Optional: **`docs/guides/agent-usage-guide.md`** — update the species table.
5. **Test** — a `test_intent.py` case that a compressible intent with the new
   species renders correct Cp/Mw into `physicalProperties`, plus an
   envelope-derivation case in `tests/test_envelopes.py` if you also want the
   `nu` band (it derives automatically).

## 3. Add a boundary-condition override / new BC type

- New **generic BC name** → add to `knowledge/bc_types.json` (with
  `applicable_fields` + `required_keys`, so the validator can check it).
- New **role guidance** → `knowledge/boundary_conditions.json`.
- **Stricter semantic checks** → `knowledge/intent_validator.py`
  (`_check_bc_overrides` / a new check in `validate()`).
- **LLM-facing hints** → `_BC_GUIDANCE` in `orchestration/pipeline.py`.

The validator will reject a BC type it doesn't know before it ever reaches the
renderer.

## 4. Add a retrieval source / make RAG smarter

- Extend `get_tutorial_numerics()` + `v13_tutorials.json` with new tutorial
  snippets — **but never add a preset key without defining it in the renderer.**
- To add a whole new registry dimension (e.g. materials for solids), add the
  JSON file, load it in `KnowledgeRetriever`, and expose a lookup + a formatting
  hook in `format_prompt_context`.

## 5. Add or tune a numeric envelope (constraint range)

1. **`knowledge/physical_envelopes.json`** — add an `envelopes` entry:
   `field`, `units`, `min`/`max` (plausible range), `reject_below`/`reject_above`
   (unit-error band), and a `rationale` string. **No code change**: `EnvelopeConstraint`
   picks the new field up automatically.
2. If the envelope should be **derived from species data** (like `nu`), add a
   branch in `_envelope_for()` (`knowledge/envelopes.py`) that computes the
   reference from the species' thermo block and returns a derived `Envelope` only
   when the species is known; keep the static fallback otherwise.
3. **Wire the field into `apply()`** if it is a scalar/tuple shape not yet
   handled (per-patch numeric fields are handled per-patch).
4. **Test** — in `tests/test_envelopes.py` add one case each for: in-range pass,
   clamp-with-note, reject-with-error.

## 6. Add a validation check (ladder or preflight)

**Ladder level (runs in Docker):** add a rung method on `ValidationAgent`
(e.g. `run_mycheck`) and insert it in `run_ladder`'s `rungs` tuple. Or extend
`parse_errors` for a new OpenFOAM error pattern.

**Preflight Level A (static, cheap):** add a check to `audit_case()` in
`audit.py`, reusing `ofdict.section` / `entries` / `boundary_fields`. Give it a
machine-readable `code` that maps to a known root cause, and a `pass`/`warn`/
`fail` path. Add findings to `PreflightResult.audit` via
`run_preflight_with_agent`.

**Level C threshold tuning:** adjust `DECAY_SLOPE` / `RISE_SLOPE` /
`CONVERGED_ABS` in `dry_run.py` if the residual-slope band needs retuning.

Test each in `tests/test_preflight.py`.

## 7. Recipe summary table

| Task | Touch | Test file |
|---|---|---|
| solver family | `case_types.py`, `intent.py`, `renderer.py`, `solvers.json` | `test_renderer.py` |
| species | `thermophysics.json`, `pipeline.py` `_SCHEMA`/`_RESPONSE_FORMAT` | `test_intent.py`, `test_envelopes.py` |
| BC type / guidance | `bc_types.json`, `boundary_conditions.json`, `intent_validator.py`, `_BC_GUIDANCE` | `test_intent_validator.py` |
| RAG/tutorial numerics | `v13_tutorials.json`, `rag.py` | `test_rag_skills.py` |
| envelope | `physical_envelopes.json`, `envelopes.py` (if derived) | `test_envelopes.py` |
| validation check | `audit.py` / `metrics.py` / `dry_run.py` / `validation_agent.py` | `test_preflight.py`, `test_validation.py` |

## 8. General rules of thumb

- **Prefer data edits (JSON) over code** — most new knowledge is a registry entry
  plus, occasionally, a lookup/enum string.
- **Never let a new numeric field through without an envelope** — if the model
  can emit it, bound it.
- **Ground everything in the real v13 tutorials** (`temp/openfoam-repo/`), and
  re-run `pytest -q` + the vortex-tube case after any renderer change.
- **Keep presets paired** — a new `_FVSCHEMES` key without a matching
  `_FVSOLUTION` key silently falls back.