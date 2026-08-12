"""CaseSpec — the structured intermediate representation (IR) the LLM emits.

This is the contract at the heart of the Phase II architecture (see
docs/phase-ii-architecture.md §2): the LLM reasons about physics and emits a
*validated* CaseSpec; deterministic code in renderer.py turns it into
guaranteed-valid OpenFOAM v13 files. The model never writes dictionary syntax.

Everything here is v13 convention (see docs/domain/openfoam/case-structure.md):
- `solver` is a v13 solver *module* named in controlDict (incompressibleFluid,
  ...), NOT an old solver binary (icoFoam, simpleFoam, pisoFoam).
- properties live in constant/physicalProperties + constant/momentumTransport.

Scope: geometry is either a tier-1 axis-aligned `box` (rendered to a blockMeshDict)
or an `external_mesh` (mesh supplied separately — only the case setup is rendered,
patches come from the mesh). Solver: incompressibleFluid with a RAS model.
"""

from __future__ import annotations

from typing import Literal, Union

from pydantic import BaseModel
from pydantic import Field as PydanticField

# --- geometry -------------------------------------------------------------

BoxFace = Literal["xMin", "xMax", "yMin", "yMax", "zMin", "zMax"]
PatchType = Literal["wall", "patch", "empty", "symmetryPlane", "symmetry", "cyclic"]


class BoxGeometry(BaseModel):
    """An axis-aligned box meshed by blockMesh with a single graded hex block."""

    kind: Literal["box"] = "box"
    convert_to_meters: float = 1.0
    x: tuple[float, float]  # (min, max)
    y: tuple[float, float]
    z: tuple[float, float]
    cells: tuple[int, int, int]
    grading: tuple[float, float, float] = (1.0, 1.0, 1.0)


class ExternalMesh(BaseModel):
    """Mesh supplied separately (STL+snappyHexMesh, converted, or provided polyMesh).

    No blockMeshDict is rendered; patch names come from the mesh, so patches carry
    only a name + boundary conditions (no box faces).
    """

    kind: Literal["external_mesh"] = "external_mesh"


Geometry = Union[BoxGeometry, ExternalMesh]


# --- boundary conditions --------------------------------------------------

class FieldBC(BaseModel):
    """A field boundary condition: a type, an optional value token, and extra dictionary entries (p0, etc.)."""

    type: str  # fixedValue, noSlip, zeroGradient, empty, cyclic, totalPressure, ...
    value: str | None = None  # e.g. "uniform (1 0 0)"; None for type-only BCs
    extra: dict[str, str] = PydanticField(default_factory=dict)

    def __init__(self, type: str, value: str | None = None, **kwargs):
        extra_dict = dict(kwargs.pop("extra", {}))
        for k, v in list(kwargs.items()):
            if v is not None:
                extra_dict[k] = str(v)
        super().__init__(type=type, value=value, extra=extra_dict)


class Patch(BaseModel):
    name: str
    type: PatchType = "patch"  # geometric type; only used when rendering a box mesh
    faces: list[BoxFace] = []  # box faces this patch covers (box geometry only)
    bc: dict[str, FieldBC]  # per-field boundary conditions, keyed by field name


# --- physics --------------------------------------------------------------

class Field(BaseModel):
    """A solved field written into 0/. `dimensions` is the 7-int OpenFOAM vector."""

    name: str  # U, p, T, k, epsilon, nut, alphat, nuTilda, ...
    field_class: Literal["volScalarField", "volVectorField"]
    dimensions: tuple[int, int, int, int, int, int, int]
    internal_field: str  # "uniform (0 0 0)" or "uniform 0.375"


class TurbulenceRAS(BaseModel):
    simulation_type: Literal["RAS", "laminar", "LES"] = "RAS"
    model: str = "kEpsilon"  # kEpsilon, kOmegaSST, ...
    turbulence: bool = True
    print_coeffs: bool = False


class ThermoProperties(BaseModel):
    thermo_type: dict[str, str] = PydanticField(
        default_factory=lambda: {
            "type": "heRhoThermo",
            "mixture": "pureMixture",
            "transport": "const",
            "thermo": "hConst",
            "equationOfState": "perfectGas",
            "specie": "specie",
            "energy": "sensibleEnthalpy",
        }
    )
    p_ref: float = 0.0
    mol_weight: float = 28.966
    cp: float = 1008.0
    hf: float = 0.0
    mu: float = 1.846e-05
    pr: float = 0.71


class FluidProperties(BaseModel):
    flow_type: Literal["incompressible", "compressible"] = "incompressible"
    viscosity_model: Literal["constant"] = "constant"
    nu: str = "1e-05 [m^2/s]"  # kinematic viscosity token as written in v13 for incompressible
    comment: str | None = None  # rendered as a `// ...` note above nu
    thermo: ThermoProperties | None = None  # for compressible flow


class TimeControl(BaseModel):
    start_from: str = "startTime"  # startTime, latestTime, ...
    start_time: float = 0.0
    stop_at: str = "endTime"
    end_time: float = 10.0
    delta_t: float = 0.005
    write_control: str = "timeStep"  # timeStep, adjustableRunTime, ...
    write_interval: float = 100
    run_time_modifiable: str = "true"  # "true" / "yes"
    adjust_time_step: str | None = None  # "yes" -> emits adjustTimeStep + maxCo
    max_co: float | None = None


# --- the whole case -------------------------------------------------------

# Named numeric presets (fvSchemes + fvSolution templates). These reference field
# names, not geometry, so they are stable across cases sharing a discretisation style.
NumericsPreset = Literal[
    "incompressibleFluid_limitedLinear",  # 2nd-order bounded, transient incompressible
    "incompressibleFluid_upwind",  # 1st-order upwind, robust (steady/incompressible)
    "compressibleFluid_steadyState",  # steady-state SIMPLE (compressible)
    "incompressibleVoF",  # transient free-surface (interface compression, alpha solver)
    "compressibleVoF",  # transient compressible free-surface (per-phase alpha thermodynamics)
]


class CaseSpec(BaseModel):
    """Complete structured description of one OpenFOAM v13 case."""

    solver: str = "incompressibleFluid"  # incompressibleFluid, fluid, ...
    case_type: str = "incompressibleFluid"
    geometry: Geometry = PydanticField(discriminator="kind")
    patches: list[Patch]
    fields: list[Field]
    turbulence: TurbulenceRAS = PydanticField(default_factory=TurbulenceRAS)
    fluid: FluidProperties = PydanticField(default_factory=FluidProperties)
    time: TimeControl = PydanticField(default_factory=TimeControl)
    numerics: str = "incompressibleFluid_limitedLinear"
    custom_fv_schemes: str | None = None
    custom_fv_solution: str | None = None
    functions: list[str] = []  # raw controlDict function entries (e.g. "#includeFunc ...")

    # --- case-type-specific extras (populated by intent_to_spec / renderer) ---
    gravity: tuple[float, float, float] | None = None  # writes constant/g + enables p_rgh
    phases: list[str] = []  # VoF/multiphase phase names -> alpha.<phase> fields + phaseProperties
    surface_tension: float | None = None  # single-scalar sigma for 2-phase VoF
    relative_velocity_model: str | None = None  # drift-flux phaseProperties
    solid: bool = False  # solidDisplacement: renders a solid property dict, D field
    is_film: bool = False  # film families: renders a film physicalProperties + delta field
    species: list[str] = []  # multicomponent / combustion mass-fraction fields
    per_phase_properties: bool = False  # constant/physicalProperties.<phase> per phase
    compressible_phases: bool = False  # per-phase files carry thermo, not nu/rho
