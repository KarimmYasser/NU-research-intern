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

from knowledge.rag import KnowledgeRetriever

# Module-level singleton: KnowledgeRetriever reads JSON files from disk on init;
# re-instantiating it on every intent_to_spec() call wastes I/O on every retry.
_RETRIEVER = KnowledgeRetriever()

from .spec import (
    BoxFace,
    BoxGeometry,
    CaseSpec,
    ExternalMesh,
    Field,
    FieldBC,
    FluidProperties,
    Patch,
    ThermoProperties,
    TimeControl,
    TurbulenceRAS,
)

PatchRole = Literal["inlet", "outlet", "wall", "movingWall", "empty", "cyclic"]


class PatchIntent(BaseModel):
    name: str
    role: PatchRole
    velocity: tuple[float, float, float] | None = None  # inlet / movingWall
    pressure: float | None = None  # outlet (gauge or absolute Pa depending on flow_type)
    temperature: float | None = None  # inlet / wall temperature [K]
    # Agent-chosen per-field BC overrides. Applied on top of role defaults.
    # Each key is a field name (e.g. "p", "U"); value is a dict of OpenFOAM BC
    # entries e.g. {"type": "totalPressure", "p0": "uniform 400000"}.
    # Must pass IntentValidator checks before reaching the renderer.
    bc_overrides: dict[str, dict[str, str]] | None = None


class CaseIntent(BaseModel):
    regime: Literal["laminar", "turbulent"] = "turbulent"
    flow_type: Literal["incompressible", "compressible"] = "incompressible"
    solver_mode: Literal["transient", "steadyState"] = "transient"
    fluid_species: str | None = None  # air, nitrogen, co2, oxygen, helium, water_vapour
    nu: float = 1e-05  # kinematic viscosity [m^2/s] (incompressible)
    p_ambient: float = 100000.0  # reference/operating pressure [Pa] (compressible)
    temperature: float = 300.0  # internal/inlet reference temperature [K]
    geometry: Literal["box", "external_mesh"] = "box"
    box_size: tuple[float, float, float] | None = None  # required if geometry == box
    cells: tuple[int, int, int] = (20, 20, 1)
    patches: list[PatchIntent]
    # --- Agent-decided parameters (optional; validated by IntentValidator) ---
    # Turbulence model name. If None, defaults to kEpsilon.
    turbulence_model: str | None = None
    # Full fvSolution body (everything between the outer braces of the file body,
    # excluding the FoamFile header). When set, overrides the static preset.
    custom_fv_solution: str | None = None
    # Full fvSchemes body. When set, overrides the static preset.
    custom_fv_schemes: str | None = None


# Box face assignment: fixed roles take canonical faces; wall patches share the
# remaining side faces. Reproduces the cavity's movingWall/fixedWalls layout.
_SIDE_FACES: list[BoxFace] = ["xMin", "xMax", "yMin", "yMax"]


def _assign_faces(patches: list[PatchIntent]) -> dict[str, list[BoxFace]]:
    faces: dict[str, list[BoxFace]] = {}
    used: set[BoxFace] = set()
    for p in patches:
        if p.role == "inlet":
            faces[p.name] = ["xMin"]; used.add("xMin")
        elif p.role == "outlet":
            faces[p.name] = ["xMax"]; used.add("xMax")
        elif p.role == "movingWall":
            faces[p.name] = ["yMax"]; used.add("yMax")
        elif p.role == "empty":
            faces[p.name] = ["zMin", "zMax"]
    remaining: list[BoxFace] = [f for f in _SIDE_FACES if f not in used]
    walls = [p for p in patches if p.role == "wall"]
    for i, p in enumerate(walls):
        faces[p.name] = [remaining[i]] if i < len(remaining) else []
    if walls and len(remaining) > len(walls):  # extra faces go to the last wall
        faces[walls[-1].name] += remaining[len(walls):]
    return faces


def _turbulence_values(intent: CaseIntent) -> tuple[float, float]:
    """Estimate inlet k and epsilon from velocity and a length scale."""
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
def _default_bc_for_role(role: PatchRole, p: PatchIntent, turbulent: bool, compressible: bool,
                         k0: float, eps0: float, default_temp: float, p_ambient: float) -> dict[str, FieldBC]:
    """Generate the standard role-based BCs. Agent bc_overrides are applied on top by _bc_for_role."""
    def vec(v):
        return f"uniform ({_num(v[0])} {_num(v[1])} {_num(v[2])})"

    active_fields = ["U", "p"] + (["T", "alphat"] if compressible else []) + (["k", "epsilon", "nut"] if turbulent else [])
    if role in ("empty", "cyclic"):
        return {f: FieldBC(type=role) for f in active_fields}

    bc: dict[str, FieldBC] = {}
    if role in ("inlet", "movingWall"):
        bc["U"] = FieldBC(type="fixedValue", value=vec(p.velocity or (0, 0, 0)))
        bc["p"] = FieldBC(type="zeroGradient")
        if compressible:
            t_val = p.temperature if p.temperature is not None else default_temp
            bc["T"] = FieldBC(type="fixedValue", value=f"uniform {_num(t_val)}")
            bc["alphat"] = FieldBC(type="calculated", value="uniform 0")
    elif role == "outlet":
        bc["U"] = FieldBC(type="zeroGradient")
        p_val = p.pressure if p.pressure is not None else (p_ambient if compressible else 0.0)
        bc["p"] = FieldBC(type="fixedValue", value=f"uniform {_num(p_val)}")
        if compressible:
            bc["T"] = FieldBC(type="zeroGradient")
            bc["alphat"] = FieldBC(type="calculated", value="uniform 0")
    else:  # wall
        bc["U"] = FieldBC(type="noSlip")
        bc["p"] = FieldBC(type="zeroGradient")
        if compressible:
            if p.temperature is not None:
                bc["T"] = FieldBC(type="fixedValue", value=f"uniform {_num(p.temperature)}")
            else:
                bc["T"] = FieldBC(type="zeroGradient")
            bc["alphat"] = FieldBC(type="compressible::alphatJayatillekeWallFunction", value="uniform 0")

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


def _bc_for_role(role: PatchRole, p: PatchIntent, turbulent: bool, compressible: bool,
                 k0: float, eps0: float, default_temp: float, p_ambient: float) -> dict[str, FieldBC]:
    """Generate BCs for a patch: role defaults, then agent bc_overrides applied on top."""
    bc = _default_bc_for_role(role, p, turbulent, compressible, k0, eps0, default_temp, p_ambient)
    # Apply agent-decided overrides. Each override is a dict of OpenFOAM BC key-values
    # e.g. {"type": "totalPressure", "p0": "uniform 400000"}.
    # The IntentValidator has already confirmed these are valid before we reach here.
    if p.bc_overrides:
        for field_name, override_dict in p.bc_overrides.items():
            bc[field_name] = FieldBC(**override_dict)
    return bc


def intent_to_spec(intent: CaseIntent) -> CaseSpec:
    """Deterministically expand a compact CaseIntent into a full v13 CaseSpec."""
    turbulent = intent.regime == "turbulent"
    compressible = intent.flow_type == "compressible"
    steady = intent.solver_mode == "steadyState"
    k0, eps0 = _turbulence_values(intent) if turbulent else (0.0, 0.0)

    faces = _assign_faces(intent.patches) if intent.geometry == "box" else {}
    patches = [
        Patch(
            name=p.name,
            type="empty" if p.role == "empty" else ("cyclic" if p.role == "cyclic" else ("wall" if p.role in ("wall", "movingWall") else "patch")),
            faces=faces.get(p.name, []),
            bc=_bc_for_role(p.role, p, turbulent, compressible, k0, eps0, intent.temperature, intent.p_ambient),
        )
        for p in intent.patches
    ]

    p_dim = (1, -1, -2, 0, 0, 0, 0) if compressible else (0, 2, -2, 0, 0, 0, 0)
    p_int = f"uniform {_num(intent.p_ambient)}" if compressible else "uniform 0"

    fields = [
        Field(name="U", field_class="volVectorField",
              dimensions=(0, 1, -1, 0, 0, 0, 0), internal_field="uniform (0 0 0)"),
        Field(name="p", field_class="volScalarField",
              dimensions=p_dim, internal_field=p_int),
    ]

    if compressible:
        fields.append(
            Field(name="T", field_class="volScalarField",
                  dimensions=(0, 0, 0, 1, 0, 0, 0), internal_field=f"uniform {_num(intent.temperature)}")
        )

    if turbulent:
        fields += [
            Field(name="k", field_class="volScalarField",
                  dimensions=(0, 2, -2, 0, 0, 0, 0), internal_field=f"uniform {_num(k0)}"),
            Field(name="epsilon", field_class="volScalarField",
                  dimensions=(0, 2, -3, 0, 0, 0, 0), internal_field=f"uniform {_num(eps0)}"),
            Field(name="nut", field_class="volScalarField",
                  dimensions=(0, 2, -1, 0, 0, 0, 0), internal_field="uniform 0"),
        ]
        if compressible:
            fields.append(
                Field(name="alphat", field_class="volScalarField",
                      dimensions=(1, -1, -1, 0, 0, 0, 0), internal_field="uniform 0")
            )

    if intent.geometry == "box":
        if intent.box_size is None:
            raise ValueError("box geometry requires box_size")
        lx, ly, lz = intent.box_size
        geometry = BoxGeometry(convert_to_meters=1.0, x=(0, lx), y=(0, ly), z=(0, lz),
                               cells=intent.cells, grading=(1, 1, 1))
    else:
        geometry = ExternalMesh()

    solver = "fluid" if compressible else "incompressibleFluid"
    if compressible:
        thermo_item = _RETRIEVER.get_species_thermo(intent.fluid_species)
        if thermo_item and "mixture" in thermo_item:
            mix = thermo_item["mixture"]
            thermo = ThermoProperties(
                mol_weight=mix.get("specie", {}).get("molWeight", 28.966),
                cp=mix.get("thermodynamics", {}).get("Cp", 1008),
                hf=mix.get("thermodynamics", {}).get("hf", 0),
                mu=mix.get("transport", {}).get("mu", 1.846e-05),
                pr=mix.get("transport", {}).get("Pr", 0.71),
            )
        else:
            thermo = ThermoProperties()
        fluid = FluidProperties(flow_type="compressible", thermo=thermo)
    else:
        fluid = FluidProperties(flow_type="incompressible", nu=f"{intent.nu:g} [m^2/s]")

    if steady and compressible:
        numerics = "compressibleFluid_steadyState"
    elif steady:
        numerics = "incompressibleFluid_upwind"
    else:
        numerics = "incompressibleFluid_limitedLinear"

    # Agent-chosen turbulence model overrides the default kEpsilon.
    turb_model = intent.turbulence_model or "kEpsilon"

    # Agent-chosen custom fvSchemes/fvSolution take priority over static presets and
    # tutorial-RAG snippets. The IntentValidator has already checked their syntax.
    if intent.custom_fv_schemes or intent.custom_fv_solution:
        final_schemes = intent.custom_fv_schemes
        final_solution = intent.custom_fv_solution
    else:
        tut_numerics = _RETRIEVER.get_tutorial_numerics(solver, "compressible" if compressible else "incompressible")
        final_schemes = tut_numerics.get("fvSchemes") if numerics not in (
            "incompressibleFluid_limitedLinear", "incompressibleFluid_upwind", "compressibleFluid_steadyState"
        ) else None
        final_solution = tut_numerics.get("fvSolution") if numerics not in (
            "incompressibleFluid_limitedLinear", "incompressibleFluid_upwind", "compressibleFluid_steadyState"
        ) else None

    return CaseSpec(
        solver=solver,
        geometry=geometry,
        patches=patches,
        fields=fields,
        turbulence=TurbulenceRAS(simulation_type="RAS" if turbulent else "laminar",
                                 model=turb_model, turbulence=turbulent),
        fluid=fluid,
        time=_time_control(intent),
        numerics=numerics,
        custom_fv_schemes=final_schemes,
        custom_fv_solution=final_solution,
    )


def _time_control(intent: CaseIntent) -> TimeControl:
    """Pick a starting deltaT + adaptive-timestep policy."""
    if intent.solver_mode == "steadyState":
        return TimeControl(
            start_time=0, end_time=1000, delta_t=1.0, write_interval=10,
            adjust_time_step="no",
        )
    if intent.geometry == "external_mesh":
        return TimeControl(
            start_time=0, end_time=0.1, delta_t=1e-6, write_interval=20,
            adjust_time_step="yes", max_co=0.5,
        )
    return TimeControl(start_time=0, end_time=0.1, delta_t=0.005, write_interval=20)
