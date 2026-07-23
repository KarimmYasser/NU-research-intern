"""CaseIntent: the compact, LLM-friendly intent the model extracts from an NL
prompt, plus a deterministic expander to the full CaseSpec.

Design (docs/phase-ii-architecture.md §2 + the Constraint Tax finding): the LLM
only decides *physics intent* over a small vocabulary — regime, geometry,
patch roles, a few numbers. Deterministic code here fills in everything
mechanical: which fields a solver needs, their dimensions, standard per-role
boundary conditions, wall functions, and estimated turbulence values. That
keeps the model's job small (which a tiny local model can do) and makes the
hard-to-get-right detail a property of code, not the model.
"""

from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel

from .spec import (
    BoxGeometry,
    CaseSpec,
    ExternalMesh,
    Field,
    FieldBC,
    FluidProperties,
    Patch,
    TimeControl,
    TurbulenceRAS,
)

PatchRole = Literal["inlet", "outlet", "wall", "movingWall", "empty"]


class PatchIntent(BaseModel):
    name: str
    role: PatchRole
    velocity: tuple[float, float, float] | None = None  # inlet / movingWall
    pressure: float | None = None  # outlet (gauge)


class CaseIntent(BaseModel):
    regime: Literal["laminar", "turbulent"]
    nu: float  # kinematic viscosity [m^2/s]
    geometry: Literal["box", "external_mesh"] = "box"
    box_size: tuple[float, float, float] | None = None  # required if geometry == box
    cells: tuple[int, int, int] = (20, 20, 1)
    patches: list[PatchIntent]


# Box face assignment: fixed roles take canonical faces; wall patches share the
# remaining side faces. Reproduces the cavity's movingWall/fixedWalls layout.
_SIDE_FACES = ["xMin", "xMax", "yMin", "yMax"]


def _assign_faces(patches: list[PatchIntent]) -> dict[str, list[str]]:
    faces: dict[str, list[str]] = {}
    used: set[str] = set()
    for p in patches:
        if p.role == "inlet":
            faces[p.name] = ["xMin"]; used.add("xMin")
        elif p.role == "outlet":
            faces[p.name] = ["xMax"]; used.add("xMax")
        elif p.role == "movingWall":
            faces[p.name] = ["yMax"]; used.add("yMax")
        elif p.role == "empty":
            faces[p.name] = ["zMin", "zMax"]
    remaining = [f for f in _SIDE_FACES if f not in used]
    walls = [p for p in patches if p.role == "wall"]
    for i, p in enumerate(walls):
        faces[p.name] = [remaining[i]] if i < len(remaining) else []
    if walls and len(remaining) > len(walls):  # extra faces go to the last wall
        faces[walls[-1].name] += remaining[len(walls):]
    return faces


def _turbulence_values(intent: CaseIntent) -> tuple[float, float]:
    """Estimate inlet k and epsilon from velocity and a length scale.

    k = 1.5*(I*U)^2 with intensity I=5%%; epsilon = Cmu^0.75 * k^1.5 / L with
    L = 0.05 * reference length. These reproduce the OpenFOAM cavity tutorial's
    k=0.00375, epsilon=0.00754 for U=1, L_ref=0.1.
    """
    umag = max(
        (math.sqrt(sum(c * c for c in p.velocity)) for p in intent.patches if p.velocity),
        default=1.0,
    ) or 1.0
    lref = min(intent.box_size) if intent.box_size else 0.1
    k = 1.5 * (0.05 * umag) ** 2
    length = 0.05 * lref
    epsilon = 0.09 ** 0.75 * k ** 1.5 / length
    return k, epsilon


def _num(v: float) -> str:
    return f"{v:g}"


# per-role boundary conditions, given the turbulent field values k0, eps0
def _bc_for_role(role: PatchRole, p: PatchIntent, turbulent: bool, k0: float, eps0: float):
    def vec(v):
        return f"uniform ({_num(v[0])} {_num(v[1])} {_num(v[2])})"

    if role == "empty":
        fields = ["U", "p"] + (["k", "epsilon", "nut"] if turbulent else [])
        return {f: FieldBC(type="empty") for f in fields}

    bc: dict[str, FieldBC] = {}
    if role in ("inlet", "movingWall"):
        bc["U"] = FieldBC(type="fixedValue", value=vec(p.velocity or (0, 0, 0)))
        bc["p"] = FieldBC(type="zeroGradient")
    elif role == "outlet":
        bc["U"] = FieldBC(type="zeroGradient")
        bc["p"] = FieldBC(type="fixedValue", value=f"uniform {_num(p.pressure or 0.0)}")
    else:  # wall
        bc["U"] = FieldBC(type="noSlip")
        bc["p"] = FieldBC(type="zeroGradient")

    if turbulent:
        wall_like = role in ("wall", "movingWall")
        if role == "inlet":
            bc["k"] = FieldBC(type="fixedValue", value=f"uniform {_num(k0)}")
            bc["epsilon"] = FieldBC(type="fixedValue", value=f"uniform {_num(eps0)}")
            bc["nut"] = FieldBC(type="calculated", value="uniform 0")
        elif role == "outlet":
            bc["k"] = FieldBC(type="zeroGradient")
            bc["epsilon"] = FieldBC(type="zeroGradient")
            bc["nut"] = FieldBC(type="calculated", value="uniform 0")
        elif wall_like:
            bc["k"] = FieldBC(type="kqRWallFunction", value=f"uniform {_num(k0)}")
            bc["epsilon"] = FieldBC(type="epsilonWallFunction", value=f"uniform {_num(eps0)}")
            bc["nut"] = FieldBC(type="nutkWallFunction", value="uniform 0")
    return bc


def intent_to_spec(intent: CaseIntent) -> CaseSpec:
    """Deterministically expand a compact CaseIntent into a full v13 CaseSpec."""
    turbulent = intent.regime == "turbulent"
    k0, eps0 = _turbulence_values(intent) if turbulent else (0.0, 0.0)

    faces = _assign_faces(intent.patches) if intent.geometry == "box" else {}
    patches = [
        Patch(
            name=p.name,
            type="empty" if p.role == "empty" else ("wall" if p.role in ("wall", "movingWall") else "patch"),
            faces=faces.get(p.name, []),
            bc=_bc_for_role(p.role, p, turbulent, k0, eps0),
        )
        for p in intent.patches
    ]

    fields = [
        Field(name="U", field_class="volVectorField",
              dimensions=(0, 1, -1, 0, 0, 0, 0), internal_field="uniform (0 0 0)"),
        Field(name="p", field_class="volScalarField",
              dimensions=(0, 2, -2, 0, 0, 0, 0), internal_field="uniform 0"),
    ]
    if turbulent:
        fields += [
            Field(name="k", field_class="volScalarField",
                  dimensions=(0, 2, -2, 0, 0, 0, 0), internal_field=f"uniform {_num(k0)}"),
            Field(name="epsilon", field_class="volScalarField",
                  dimensions=(0, 2, -3, 0, 0, 0, 0), internal_field=f"uniform {_num(eps0)}"),
            Field(name="nut", field_class="volScalarField",
                  dimensions=(0, 2, -1, 0, 0, 0, 0), internal_field="uniform 0"),
        ]

    if intent.geometry == "box":
        if intent.box_size is None:
            raise ValueError("box geometry requires box_size")
        lx, ly, lz = intent.box_size
        geometry = BoxGeometry(convert_to_meters=1.0, x=(0, lx), y=(0, ly), z=(0, lz),
                               cells=intent.cells, grading=(1, 1, 1))
    else:
        geometry = ExternalMesh()

    return CaseSpec(
        solver="incompressibleFluid",
        geometry=geometry,
        patches=patches,
        fields=fields,
        turbulence=TurbulenceRAS(simulation_type="RAS" if turbulent else "laminar",
                                 model="kEpsilon", turbulence=turbulent),
        fluid=FluidProperties(nu=f"{intent.nu:g} [m^2/s]"),
        time=TimeControl(start_time=0, end_time=0.1, delta_t=0.005, write_interval=20),
        numerics="incompressibleFluid_limitedLinear",
    )
