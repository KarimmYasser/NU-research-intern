# Deterministic Constraint Layers: "a range of numbers, not model experience"

The LLM in this pipeline (a small local model) is only trusted to make *physics
intent* decisions. Every mechanical detail is produced by deterministic code.
This doc collects the **numeric** constraint layer — the "give the model a
range, don't take its number on faith" rule — and where it sits in the overall
"reason free, constrain late" design (`phase-ii-architecture.md` §2.0).

## The problem it solves

`CaseIntent` carries raw numbers: `nu`, `p_ambient`, `temperature`, per-patch
`velocity`/`pressure`/`temperature`, `box_size`, `cells`, `surface_tension`.
Before the envelope layer, the model's value passed as long as it satisfied
*structural* checks (right type, right enum). Nothing bounded its magnitude, so
a cP-vs-Pa·s slip like `nu=0.5`, or `temperature=2e5`, sailed straight into the
renderer and produced a case that was syntactically perfect and physically
absurd.

## The constraint: `EnvelopeConstraint` (`src/knowledge/envelopes.py`)

Runs in `extract_intent` as **Stage 1.5**, between Pydantic parsing (Stage 1)
and the semantic `IntentValidator` (Stage 2), in `orchestration/pipeline.py`.
For every numeric field, a curated **envelope** (a min/max plausible range, a
wider reject band, and a rationale) decides three ways:

| Model's value is... | Action | Result |
|---|---|---|
| inside `[min, max]` | accepted untouched | case uses the model's number |
| outside `[min, max]` but inside the reject band | **clamped** to the nearest bound, a note printed | case runs with a physically sane number; model is *not* trusted |
| outside the reject band (only a units/dimension slip-up can explain it) | returned as an `IntentValidationError` | fed back into the existing LLM retry loop with the valid range shown |

Velocity vectors are treated by **magnitude**: the direction is preserved and the
vector is rescaled so `|v|` lands on the bound. Clamping never bends a
prescribed flow angle.

Rejecting instead of clamping for the far-out cases matters: clamping silently
`nu=0.5` to `0.1` would "fix" a value that is really a units mistake, hiding the
error from the user. The retry loop surfaces it: the model sees
`nu=0.5 m^2/s is physically implausible; valid range is [1e-07, 0.1] m^2/s`.

## Where the envelopes come from

Two provenances, one rule: **fluid-property bounds are determined from data;
case-regime bounds are curated constants.**

### Derived from data — `nu` when a species is named

When `intent.fluid_species` is set, the `nu` envelope is **computed**, not
looked up. From `knowledge/thermophysics.json` (which already stores each
species' dynamic viscosity `mu` and molecular weight `Mw`):

```
rho     = p · Mw / (R · T)          # ideal gas, at the case's conditions
nu_ref  = mu / rho
envelope = [nu_ref/10, nu_ref·10],  reject band ±2 decades
```

`R = 8.314 J/(mol·K)`. Sanity check: air at 1 atm / 300 K derives
`nu_ref ≈ 1.58e-5 m²/s` — the textbook value. Consequences:

- `nu=1e-5` for air passes with **zero notes** (it is the real value);
- the same `nu=0.5` that the static envelope would have clamped to `0.1` is
  now **rejected**, because it is ~4 decades above the real fluid's value;
- the band shifts with the case: hotter gas → lower density → larger `nu_ref`.

If the species is unknown, or no species is named, `nu` falls back to the
static envelope.

### Curated constants — case-regime fields

`temperature`, `pressure`, `p_ambient`, `box_size`, `cells`, `velocity`, and
`surface_tension` are case parameters, not fluid properties, so their ranges
are hand-curated in `src/knowledge/physical_envelopes.json`:

| Field | Plausible range | Reject band | Basis |
|---|---|---|---|
| `nu` | `[1e-7, 1e-1]` m²/s | `[1e-9, 1e1]` | real-fluid table: He 1.2e-4, air 1.5e-5, water 1e-6, Hg 1.1e-7 |
| `p_ambient` | `[1e2, 1e8]` Pa | `[1e0, 1e10]` | 1 atm = 101325; vacuum to industrial |
| `temperature` | `[50, 4000]` K | `[1, 1e4]` | cryogenic LN₂ ~77 to combustion ~2500 |
| `velocity` (mag) | `[1e-2, 1e3]` m/s | `[1e-4, 1e4]` | microfluidics to high-speed gas |
| `pressure` (BC) | `[-1e6, 1e8]` Pa | `[-1e8, 1e10]` | gauge may be negative (suction) |
| `box_size` | `[1e-4, 1e3]` m/axis | `[1e-6, 1e4]` | microchannel to industrial |
| `cells` | `[1, 500]`/axis | `[1, 2000]` | blockMesh memory envelope |
| `surface_tension` | `[1e-4, 5]` N/m | `[1e-6, 1e2]` | water 0.072, Hg 0.486 |

Every entry carries a `rationale` string so a bound is defendable to the
supervisor, not an arbitrary constant. To add or tune a range, edit the JSON —
no code change.

## What it does NOT do

- It does **not** derive turbulence fields. `k`/`epsilon` inlet estimates and
  the CFL-based `deltaT` are already computed deterministically in
  `ir/intent.py` (`_turbulence_values`, `_cfl_delta_t`) — the model never
  emits them.
- It does **not** replace semantic validation. `IntentValidator` still checks
  BC type names, required keys, turbulence model names, and relaxation-factor
  ranges (`knowledge/intent_validator.py`).
- It does **not** make bad physics good. A value inside the envelope can still
  be the wrong *choice* for the problem; the ladder catches that. This layer
  only guarantees the model cannot emit a physically *impossible* number.

## The full "numbers" trust boundary (what the model may still emit)

| Field | Who decides the number | Mechanism |
|---|---|---|
| `nu` | species data, when named | derived envelope (`envelopes.py`) |
| `k`, `epsilon`, `nut`, `alphat` | deterministic code | `_turbulence_values()` in `ir/intent.py` |
| `deltaT`, `endTime` | deterministic code | `_time_control()` / `_cfl_delta_t()` |
| `p_ambient`, `temperature`, velocities, pressures | the model, then clamped | static envelopes |
| `box_size`, `cells` | the model, then clamped | static envelopes |
| `surface_tension` | the model, then clamped | static envelope |

## Tests

`tests/test_envelopes.py` — 16 cases covering: in-range pass-through; clamping
for `nu`/`temperature`/`cells`; direction-preserving velocity rescale;
zero-velocity no-op; reject-band unit errors carrying the
`IntentValidationError` shape; air-derived-envelope acceptance; out-of-band
rejection for a species-named `nu`; unknown-species static fallback; and
temperature-dependence of the derived band. Run: `pytest -q` (141 pass).

## Future directions

- **Liquid density source.** The `nu` derivation assumes ideal gas
  (`rho = p·Mw/R·T`). A liquid species would need a density table or
  incompressible liquid rho instead. The species registry is all gases today,
  so the current assumption is consistent — this is the first thing to revisit
  when a liquid is added.
- **Derive more fields from data.** Species data could bound `temperature`
  (phase-change limits) and inform `surface_tension` once per-species σ data
  exists.
- **Cross-field consistency.** A derived check could flag, e.g., an
  incompressible case whose prescribed velocity implies Ma > 0.3 — currently
  out of scope for the envelope but a natural next rung.
