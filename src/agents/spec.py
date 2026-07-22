"""CaseSpec — the structured intermediate representation (IR) the LLM emits.

This is the contract at the heart of the Phase II architecture (see
docs/phase-ii-architecture.md §2): the LLM reasons about physics and emits a
*validated* CaseSpec; deterministic code in renderer.py turns it into
guaranteed-valid OpenFOAM v13 files. The model never writes dictionary syntax.

Everything here is v13 convention (see docs/openfoam/case-structure.md):
- `solver` is a v13 solver *module* named in controlDict (incompressibleFluid,
  compressibleFluid, ...), NOT an old solver binary (icoFoam, simpleFoam).
- properties live in constant/physicalProperties + constant/momentumTransport.

Scope note: this first cut covers tier-1 geometry (an axis-aligned box) and the
incompressibleFluid solver with a RAS turbulence model — enough to render the
cavity tutorial. Wider coverage (cylinder/wedge, more solvers) extends the enums
and the renderer; the overflow path handles what the renderer can't (see §2.3).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel
from pydantic import Field as PydanticField

# --- geometry -------------------------------------------------------------

BoxFace = Literal["xMin", "xMax", "yMin", "yMax", "zMin", "zMax"]
PatchType = Literal["wall", "patch", "empty", "symmetryPlane", "symmetry"]


class BoxGeometry(BaseModel):
    """An axis-aligned box meshed by blockMesh with a single graded hex block.

    Coordinates are in `convert_to_meters` units (blockMesh's convertToMeters),
    matching how the OpenFOAM tutorials express geometry.
    """

    kind: Literal["box"] = "box"
    convert_to_meters: float = 1.0
    x: tuple[float, float]  # (min, max)
    y: tuple[float, float]
    z: tuple[float, float]
    cells: tuple[int, int, int]
    grading: tuple[float, float, float] = (1.0, 1.0, 1.0)


# --- boundary conditions --------------------------------------------------

# A field BC is a (type, optional value) pair. `value` is a rendered token like
# "uniform (1 0 0)" or "uniform 0"; the renderer places it verbatim.
class FieldBC(BaseModel):
    type: str  # fixedValue, noSlip, zeroGradient, empty, ...
    value: str | None = None  # e.g. "uniform (1 0 0)"; None for type-only BCs


class Patch(BaseModel):
    name: str
    type: PatchType
    faces: list[BoxFace]  # which box faces this patch covers
    # per-field boundary conditions, keyed by field name ("U", "p", ...)
    bc: dict[str, FieldBC]


# --- physics --------------------------------------------------------------

class Field(BaseModel):
    """A solved field written into 0/. `dimensions` is the 7-int OpenFOAM vector."""

    name: str  # U, p, k, epsilon, ...
    field_class: Literal["volScalarField", "volVectorField"]
    dimensions: tuple[int, int, int, int, int, int, int]
    internal_field: str  # "uniform (0 0 0)" or "uniform 0"


class TurbulenceRAS(BaseModel):
    simulation_type: Literal["RAS", "laminar", "LES"] = "RAS"
    model: str = "kEpsilon"  # kEpsilon, kOmegaSST, ...
    turbulence: bool = True


class FluidProperties(BaseModel):
    viscosity_model: Literal["constant"] = "constant"
    nu: str = "1e-05 [m^2/s]"  # kinematic viscosity token as written in v13


class TimeControl(BaseModel):
    start_time: float = 0.0
    end_time: float = 10.0
    delta_t: float = 0.005
    write_interval: int = 100
    write_control: str = "timeStep"


# --- the whole case -------------------------------------------------------

class CaseSpec(BaseModel):
    """Complete structured description of one OpenFOAM v13 case."""

    solver: Literal["incompressibleFluid"] = "incompressibleFluid"
    geometry: BoxGeometry
    patches: list[Patch]
    fields: list[Field]
    turbulence: TurbulenceRAS = PydanticField(default_factory=TurbulenceRAS)
    fluid: FluidProperties = PydanticField(default_factory=FluidProperties)
    time: TimeControl = PydanticField(default_factory=TimeControl)
    schemes_family: Literal["firstOrder", "secondOrder"] = "firstOrder"
