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

import pydantic
from pydantic import BaseModel

from knowledge.rag import KnowledgeRetriever

# Module-level singleton: KnowledgeRetriever reads JSON files from disk on init;
# re-instantiating it on every intent_to_spec() call wastes I/O on every retry.
_RETRIEVER = KnowledgeRetriever()

from .case_types import CASE_TYPES, CaseTypeDef, get_case_type
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

PatchRole = Literal["inlet", "outlet", "wall", "movingWall", "empty", "cyclic", "symmetry"]
Regime = Literal["laminar", "turbulent"]
FlowType = Literal["incompressible", "compressible"]
SolverMode = Literal["transient", "steadyState"]
GeometryType = Literal["box", "external_mesh"]


class PatchIntent(BaseModel):
    name: str
    role: PatchRole
    velocity: tuple[float, float, float] | None = None  # inlet / movingWall
    pressure: float | None = None  # outlet (gauge or absolute Pa depending on flow_type)
    temperature: float | None = None  # inlet / wall temperature [K]
    # Explicit box faces for this patch (box geometry only). When any patch lists
    # faces, every box patch must list its faces and together they must cover all
    # six faces exactly once. When none do, faces are assigned from a canonical
    # role map (see _assign_faces) with the leftover faces folded into a wall.
    faces: list[BoxFace] | None = None
    # Agent-chosen per-field BC overrides. Applied on top of role defaults.
    # Each key is a field name (e.g. "p", "U"); value is a dict of OpenFOAM BC
    # entries e.g. {"type": "totalPressure", "p0": "uniform 400000"}.
    # Must pass IntentValidator checks before reaching the renderer.
    bc_overrides: dict[str, dict[str, str]] | None = None


class CaseIntent(BaseModel):
    # Case-type physics family (see ir.case_types.CASE_TYPES). Determines the
    # solver module, the field set, and the property flavour (VoF, Euler,
    # film, solid, ...). Defaults keep the original single-phase behaviour.
    case_type: str = "incompressibleFluid"
    regime: Regime = "turbulent"
    flow_type: FlowType = "incompressible"
    solver_mode: SolverMode = "transient"
    fluid_species: str | None = None  # air, nitrogen, co2, oxygen, helium, water_vapour
    nu: float = 1e-05  # kinematic viscosity [m^2/s] (incompressible)
    p_ambient: float = 100000.0  # reference/operating pressure [Pa] (compressible)
    temperature: float = 300.0  # internal/inlet reference temperature [K]
    geometry: GeometryType = "box"
    box_size: tuple[float, float, float] | None = None  # required if geometry == box
    cells: tuple[int, int, int] = (20, 20, 1)
    patches: list[PatchIntent]
    # --- case-type-specific physics knobs (validated by IntentValidator) ---
    # VoF / multiphase / buoyant: gravity vector, written to constant/g and
    # switching the pressure field to p_rgh.
    gravity: tuple[float, float, float] | None = None
    # VoF phase names overriding the default pair for the chosen case_type.
    phases: list[str] | None = None
    # Surface tension [N/m] for the phaseProperties file (VoF families).
    surface_tension: float | None = None
    # --- Agent-decided parameters (optional; validated by IntentValidator) ---
    # Turbulence model name. If None, defaults to kEpsilon.
    turbulence_model: str | None = None
    # Full fvSolution body (everything between the outer braces of the file body,
    # excluding the FoamFile header). Leave null/None to use standard preset.
    custom_fv_solution: str | None = pydantic.Field(
        default=None,
        description="Optional fvSolution dictionary override string. Leave null/None to use the standard built-in OpenFOAM solver preset (HIGHLY RECOMMENDED). Only specify if custom settings are strictly required."
    )
    # Full fvSchemes body. When set, overrides the static preset.
    custom_fv_schemes: str | None = None
    # Arbitrary additional case files the solver/physics may require
    # (system/fvConstraints, system/functions, constant/thermophysicalProperties,
    # etc.). Keys are case-relative paths; values are the full file content.
    # OpenFOAM solvers demand optional/domain-specific dictionaries we cannot
    # enumerate in advance, so the agent may emit whatever the physics needs.
    extra_files: dict[str, str] = {}  # arbitrary additional case-relative files


# Box face assignment. Canonical map: inlet->xMin, outlet->xMax,
# movingWall->yMax, empty->zMin/zMax. Symmetry patches then walls split the
# remaining side faces; any unclaimed face (including zMin/zMax when the case is
# genuinely 3D) is folded into the last wall so the box is always closed. This
# is a *general* layout rule, not a reproduction of one tutorial's geometry.
_ALL_FACES: list[BoxFace] = ["xMin", "xMax", "yMin", "yMax", "zMin", "zMax"]
_SIDE_FACES: list[BoxFace] = ["xMin", "xMax", "yMin", "yMax"]


def _assign_faces(patches: list[PatchIntent]) -> dict[str, list[BoxFace]]:
    # Explicit per-patch faces (LLM-chosen): require every patch to name its
    # faces and verify they tile the box exactly once.
    if any(p.faces for p in patches):
        for p in patches:
            if not p.faces:
                raise ValueError(
                    f"Patch '{p.name}' has no faces: when any box patch lists "
                    "faces explicitly, every patch must list the faces it covers."
                )
        assigned = {p.name: list(p.faces or []) for p in patches}
        seen: set[BoxFace] = set()
        for name, fs in assigned.items():
            for f in fs:
                if f in seen:
                    raise ValueError(f"Box face '{f}' is assigned to multiple patches.")
                seen.add(f)
        missing = [f for f in _ALL_FACES if f not in seen]
        if missing:
            raise ValueError(f"Box faces not covered by any patch: {missing}")
        return assigned

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
    for p in patches:
        if p.role == "symmetry" and p.name not in faces:
            faces[p.name] = [remaining.pop(0)] if remaining else []
    for p in patches:
        if p.role == "wall" and p.name not in faces:
            faces[p.name] = [remaining.pop(0)] if remaining else []

    # Close the box: any face still unclaimed (a 3D case has no empty z patches)
    # goes to the last wall, or the first symmetry patch if there are no walls.
    claimed = {f for fs in faces.values() for f in fs}
    unclaimed = [f for f in _ALL_FACES if f not in claimed]
    if unclaimed:
        walls = [p for p in patches if p.role == "wall"]
        if walls:
            faces[walls[-1].name] = faces.get(walls[-1].name, []) + unclaimed
        elif any(p.role == "symmetry" for p in patches):
            sym = next(p for p in patches if p.role == "symmetry")
            faces[sym.name] = faces.get(sym.name, []) + unclaimed
        else:
            raise ValueError(
                "Box geometry left unassigned faces and there is no wall or "
                "symmetry patch to absorb them; merge patches or list `faces` explicitly."
            )

    # Two same-role patches (e.g. two inlets) would collide on one canonical
    # face — catch it instead of emitting a duplicate-face mesh.
    seen: set[BoxFace] = set()
    for name, fs in faces.items():
        for f in fs:
            if f in seen:
                raise ValueError(
                    f"Box face '{f}' is assigned to multiple patches ('{name}' "
                    "collides with an earlier patch sharing its canonical face). "
                    "Merge the patches or list `faces` explicitly."
                )
            seen.add(f)

    for p in patches:
        if not faces.get(p.name):
            raise ValueError(
                f"Patch '{p.name}' could not be assigned a box face — too many "
                "patches for a 6-face box. Merge patches or list `faces` explicitly."
            )
    return faces


def _max_velocity(intent: CaseIntent) -> float:
    """Largest prescribed velocity across patches (inlet/movingWall)."""
    return max(
        (math.sqrt(sum(c * c for c in p.velocity)) for p in intent.patches if p.velocity),
        default=1.0,
    ) or 1.0


def _turbulence_values(intent: CaseIntent) -> tuple[float, float]:
    """Estimate inlet k and epsilon from velocity and a length scale."""
    umag = _max_velocity(intent)
    lref = min(intent.box_size) if intent.box_size else 0.1
    k = 1.5 * (0.05 * umag) ** 2
    length = 0.05 * lref
    epsilon = 0.09 ** 0.75 * k ** 1.5 / length
    return k, epsilon


def _num(v: float) -> str:
    return f"{v:g}"


# per-role boundary conditions, given the turbulent field values k0, eps0
# per-field boundary conditions by physics anchor ("p_rgh", "alpha", "delta",
# "D", "T", ...). The anchor is the field-name prefix before the first "." so
# phase-suffixed fields (alpha.water, U.air, k.water) share one rule.
_PHASE_ANCHORS = ("U", "T", "alpha", "k", "epsilon", "nut", "alphat", "rho", "Theta", "nuTilda")


def _anchor(field_name: str) -> str:
    head = field_name.split(".", 1)[0]
    return head if head in _PHASE_ANCHORS else field_name


def _default_bc_for_role(role: PatchRole, p: PatchIntent, turbulent: bool, compressible: bool,
                         k0: float, eps0: float, default_temp: float, p_ambient: float,
                         active_fields: list[str] | None = None) -> dict[str, FieldBC]:
    """Generate the standard role-based BCs for every active field.

    `active_fields` defaults to the base incompressible/compressible set; when a
    case type adds fields (p_rgh, alpha.<phase>, delta, D, species) they are
    seeded here with their role defaults. Agent bc_overrides are applied on top
    by _bc_for_role.
    """
    def vec(v):
        return f"uniform ({_num(v[0])} {_num(v[1])} {_num(v[2])})"

    if active_fields is None:
        active_fields = ["U", "p"] + (["T", "alphat"] if compressible else []) + (["k", "epsilon", "nut"] if turbulent else [])

    passthrough = {"empty": role, "cyclic": role, "symmetry": "symmetryPlane" if role == "symmetry" else role}
    if role in ("empty", "cyclic", "symmetry"):
        bc_type = "symmetryPlane" if role == "symmetry" else role
        value = "$internalField" if role == "cyclic" else None
        return {f: FieldBC(type=bc_type, value=value) for f in active_fields}

    is_inlet_like = role in ("inlet", "movingWall")
    is_wall = role in ("wall", "movingWall")
    bc: dict[str, FieldBC] = {}

    for f in active_fields:
        a = _anchor(f)
        if a == "U":
            if is_inlet_like:
                bc[f] = FieldBC(type="fixedValue", value=vec(p.velocity or (0, 0, 0)))
            elif role == "outlet":
                # For compressible flow, inletOutlet prevents backflow divergence at outlets
                bc[f] = FieldBC(type="inletOutlet", value="uniform (0 0 0)", inletValue="uniform (0 0 0)") if compressible \
                    else FieldBC(type="zeroGradient")
            else:
                bc[f] = FieldBC(type="noSlip")
        elif a == "p":
            if is_wall:
                bc[f] = FieldBC(type="fixedFluxPressure", value="uniform 0") if compressible \
                    else FieldBC(type="zeroGradient")
            elif role == "outlet":
                p_val = p.pressure if p.pressure is not None else (p_ambient if compressible else 0.0)
                bc[f] = FieldBC(type="fixedValue", value=f"uniform {_num(p_val)}")
            elif is_inlet_like:
                bc[f] = FieldBC(type="zeroGradient")
        elif a == "p_rgh":
            # hydrostatic-consistent pressure: fixedFluxPressure at walls, fixed 0 elsewhere.
            if is_wall:
                bc[f] = FieldBC(type="fixedFluxPressure", value="uniform 0")
            else:
                bc[f] = FieldBC(type="fixedValue", value=f"uniform {_num(p.pressure or 0.0)}")
        elif a == "T":
            if is_inlet_like:
                t_val = p.temperature if p.temperature is not None else default_temp
                bc[f] = FieldBC(type="fixedValue", value=f"uniform {_num(t_val)}")
            elif role == "outlet":
                bc[f] = FieldBC(type="inletOutlet", value=f"uniform {_num(default_temp)}", inletValue=f"uniform {_num(default_temp)}") if compressible \
                    else FieldBC(type="zeroGradient")
            else:
                if p.temperature is not None:
                    bc[f] = FieldBC(type="fixedValue", value=f"uniform {_num(p.temperature)}")
                else:
                    bc[f] = FieldBC(type="zeroGradient")
        elif a == "alphat":
            # movingWall prescribes T directly, so alphat stays calculated there.
            bc[f] = FieldBC(type="calculated", value="uniform 0") if (not is_wall or role == "movingWall") \
                else FieldBC(type="compressible::alphatJayatillekeWallFunction", value="uniform 0")
        elif a == "alpha":
            # phase fraction: inletOutlet on inlets (fills inletValue), zeroGradient on boundaries.
            if is_inlet_like:
                bc[f] = FieldBC(type="inletOutlet", value="uniform 0", inletValue="uniform 1")
            else:
                bc[f] = FieldBC(type="zeroGradient")
        elif a == "k" or a == "epsilon" or a == "nuTilda":
            # movingWall is a moving wall: U is prescribed but turbulence uses
            # wall functions, not inlet fixed values.
            if role == "inlet":
                v = f"uniform {_num(k0)}" if a == "k" else f"uniform {_num(eps0)}" if a == "epsilon" else "uniform 0"
                bc[f] = FieldBC(type="fixedValue", value=v)
            elif role == "outlet":
                bc[f] = FieldBC(type="zeroGradient")
            elif is_wall:
                wf = "kqRWallFunction" if a == "k" else "epsilonWallFunction" if a == "epsilon" else "zeroGradient"
                v = None if a == "nuTilda" else f"uniform {_num(k0 if a == 'k' else eps0)}"
                bc[f] = FieldBC(type=wf, value=v)
        elif a == "nut":
            bc[f] = FieldBC(type="nutkWallFunction" if is_wall else "calculated", value="uniform 0")
        elif a == "delta":
            # film thickness: fixedValue on inlets, filmContactAngle at walls (zeroGradient fallback).
            if is_inlet_like:
                bc[f] = FieldBC(type="fixedValue", value="uniform 1e-3")
            elif is_wall:
                bc[f] = FieldBC(type="filmContactAngle", value="uniform 0")
            else:
                bc[f] = FieldBC(type="zeroGradient")
        elif a == "D":
            # solid displacement: prescribed fixedValue on inlet/movingWall patches, traction-free elsewhere.
            if is_inlet_like:
                bc[f] = FieldBC(type="fixedValue", value=vec(p.velocity or (0, 0, 0)))
            else:
                bc[f] = FieldBC(type="zeroGradient")
        elif a in ("Xi", "b", "Tu") or len(f.split(".")[0]) > 1:
            # combustion scalars (mass fractions, Xi, b, Tu): fixedValue at inlets.
            if is_inlet_like:
                t_val = p.temperature if p.temperature is not None else default_temp
                bc[f] = FieldBC(type="fixedValue",
                                value=f"uniform {_num(t_val if a == 'Tu' else 0.0)}")
            else:
                bc[f] = FieldBC(type="zeroGradient")
        if bc.get(f) is None:
            bc[f] = FieldBC(type="zeroGradient")
    return bc


def _bc_for_role(role: PatchRole, p: PatchIntent, turbulent: bool, compressible: bool,
                 k0: float, eps0: float, default_temp: float, p_ambient: float,
                 active_fields: list[str] | None = None) -> dict[str, FieldBC]:
    """Generate BCs for a patch: role defaults, then agent bc_overrides applied on top."""
    bc = _default_bc_for_role(role, p, turbulent, compressible, k0, eps0, default_temp, p_ambient,
                              active_fields=active_fields)
    # Apply agent-decided overrides. Each override is a dict of OpenFOAM BC key-values
    # e.g. {"type": "totalPressure", "p0": "uniform 400000"}.
    # The IntentValidator has already confirmed these are valid before we reach here.
    if p.bc_overrides:
        for field_name, override_dict in p.bc_overrides.items():
            bc[field_name] = FieldBC(**override_dict)
    return bc


def _resolve_case_type(intent: CaseIntent) -> CaseTypeDef:
    """Resolve the physics family, keeping backward compatibility: callers that
    only set flow_type="compressible" (the pre-case-type API) map to the `fluid`
    family automatically."""
    name = intent.case_type
    if not name or name == "incompressibleFluid":
        if intent.flow_type == "compressible":
            return CASE_TYPES["fluid"]
        return CASE_TYPES["incompressibleFluid"]
    return get_case_type(name)


# Field factories per case type. Each returns (name, class, dims, internal_field).
def _initial_U(intent: CaseIntent) -> str:
    """Seed the U internal field from the dominant inlet velocity.

    A quiescent (0 0 0) interior against a fast inlet (e.g. 50 m/s through
    small nozzles) is a step discontinuity in velocity. The first compressible
    pressure solve must then balance a huge initial mass source, which blows the
    GAMG residual to ~1e68 and NaNs every field (the RHVT h-NaN). Starting the
    whole domain at the strongest inlet velocity keeps the field continuous and
    the first solve stable. Falls back to uniform (0 0 0) when no inlet has an
    explicit velocity (box-internal start-up, natural convection, etc.).
    """
    velocities = [p.velocity for p in intent.patches
                  if p.role == "inlet" and p.velocity is not None]
    if not velocities:
        return "uniform (0 0 0)"
    dominant = max(velocities, key=lambda v: v[0] ** 2 + v[1] ** 2 + v[2] ** 2)
    return f"uniform ({_num(dominant[0])} {_num(dominant[1])} {_num(dominant[2])})"


def _add_turb_fields(fields, turbulent, compressible, k0, eps0, suffix: str = ""):
    if not turbulent:
        return
    s = f".{suffix}" if suffix else ""
    fields += [
        Field(name=f"k{s}", field_class="volScalarField",
              dimensions=(0, 2, -2, 0, 0, 0, 0), internal_field=f"uniform {_num(k0)}"),
        Field(name=f"epsilon{s}", field_class="volScalarField",
              dimensions=(0, 2, -3, 0, 0, 0, 0), internal_field=f"uniform {_num(eps0)}"),
        Field(name=f"nut{s}", field_class="volScalarField",
              dimensions=(0, 2, -1, 0, 0, 0, 0), internal_field="uniform 0"),
    ]
    if compressible:
        fields.append(Field(name=f"alphat{s}", field_class="volScalarField",
                            dimensions=(1, -1, -1, 0, 0, 0, 0), internal_field="uniform 0"))


def _case_fields(def_: CaseTypeDef, intent: CaseIntent, turbulent: bool,
                 k0: float, eps0: float) -> list[Field]:
    """The exact 0/ field list a case_type needs, grounded in the v13 tutorials.

    Grounding (temp/openfoam-repo/OpenFOAM-13/tutorials):
      incompressibleVoF/damBreak*, incompressibleMultiphaseVoF/damBreak4phase,
      incompressibleDriftFlux/dahl         -> alpha + U + p_rgh (+ turbulence)
      compressibleVoF/*damBreak*, compressibleMultiphaseVoF/damBreak4phase
                                          -> alpha + U + p + p_rgh + T (+ turbulence)
      multiphaseEuler/bubbleColumn*        -> alpha.<ph> + U.<ph> + T.<ph> + p + p_rgh
      incompressibleDenseParticleFluid/column -> U.<carrier> + p
      isothermalFluid/potentialFreeSurfaceOscillatingBox -> U + p + p_rgh + T
      shockFluid/wedge15Ma5                -> U + p + T (inviscid, no turbulence)
      fluid_buoyant/hotRoomComfort         -> U + p + p_rgh + T (+ turbulence)
      solidDisplacement/plateHole          -> D + T
      multicomponentFluid/counterFlowFlame -> species + U + p + T (+ turbulence)
      XiFluid/moriyoshiHomogeneous         -> U + p + T + Tu + b + Xi + species (+ turbulence)
    """
    name = def_.name
    compressible = def_.flow_type == "compressible"
    dims_p = (1, -1, -2, 0, 0, 0, 0) if compressible else (0, 2, -2, 0, 0, 0, 0)
    p_int = f"uniform {_num(intent.p_ambient)}" if compressible else "uniform 0"
    phases = list(intent.phases or def_.phases)
    fields: list[Field] = []

    if def_.is_solid:
        fields.append(Field(name="D", field_class="volVectorField",
                            dimensions=(0, 1, 0, 0, 0, 0, 0), internal_field="uniform (0 0 0)"))
        fields.append(Field(name="T", field_class="volScalarField",
                            dimensions=(0, 0, 0, 1, 0, 0, 0),
                            internal_field=f"uniform {_num(intent.temperature)}"))
        return fields

    if def_.is_film:
        fields.append(Field(name="delta", field_class="volScalarField",
                            dimensions=(0, 1, 0, 0, 0, 0, 0), internal_field="uniform 0"))
        fields.append(Field(name="U", field_class="volVectorField",
                            dimensions=(0, 1, -1, 0, 0, 0, 0), internal_field=_initial_U(intent)))
        fields.append(Field(name="p", field_class="volScalarField",
                            dimensions=dims_p, internal_field=p_int))
        # rivuletPanel 0/ carries T for both film families.
        fields.append(Field(name="T", field_class="volScalarField",
                            dimensions=(0, 0, 0, 1, 0, 0, 0),
                            internal_field=f"uniform {_num(intent.temperature)}"))
        return fields

    # --- Euler-Euler: per-phase alpha/U/T + p + p_rgh -----------------------
    if name == "multiphaseEuler":
        for ph in phases:
            fields.append(Field(name=f"alpha.{ph}", field_class="volScalarField",
                                dimensions=(0, 0, 0, 0, 0, 0, 0), internal_field="uniform 0"))
            fields.append(Field(name=f"U.{ph}", field_class="volVectorField",
                                dimensions=(0, 1, -1, 0, 0, 0, 0), internal_field=_initial_U(intent)))
            if compressible:
                fields.append(Field(name=f"T.{ph}", field_class="volScalarField",
                                    dimensions=(0, 0, 0, 1, 0, 0, 0),
                                    internal_field=f"uniform {_num(intent.temperature)}"))
            _add_turb_fields(fields, turbulent, compressible, k0, eps0, suffix=ph)
        fields.append(Field(name="p", field_class="volScalarField",
                            dimensions=dims_p, internal_field=p_int))
        fields.append(Field(name="p_rgh", field_class="volScalarField",
                            dimensions=(1, -1, -2, 0, 0, 0, 0), internal_field="uniform 0"))
        return fields

    # --- two-fluid dense particle flow: U.<carrier> + p --------------------
    if name == "incompressibleDenseParticleFluid":
        carrier = phases[-1] if phases else "carrier"
        fields.append(Field(name=f"U.{carrier}", field_class="volVectorField",
                            dimensions=(0, 1, -1, 0, 0, 0, 0), internal_field=_initial_U(intent)))
        fields.append(Field(name="p", field_class="volScalarField",
                            dimensions=dims_p, internal_field=p_int))
        if turbulent:
            _add_turb_fields(fields, turbulent, compressible, k0, eps0, suffix=carrier)
        return fields

    # --- VoF + drift-flux phase-fraction fields ----------------------------
    if def_.phases:
        emit = phases if len(phases) > 2 else [phases[0]]
        for ph in emit:
            fields.append(Field(name=f"alpha.{ph}", field_class="volScalarField",
                                dimensions=(0, 0, 0, 0, 0, 0, 0), internal_field="uniform 0"))

    # Velocity + pressure. Incompressible hydrostatic families solve only p_rgh;
    # compressible gravity families solve both p and p_rgh; the rest solve p alone.
    fields.append(Field(name="U", field_class="volVectorField",
                        dimensions=(0, 1, -1, 0, 0, 0, 0), internal_field=_initial_U(intent)))
    if def_.needs_gravity and not compressible:
        fields.append(Field(name="p_rgh", field_class="volScalarField",
                            dimensions=(1, -1, -2, 0, 0, 0, 0), internal_field="uniform 0"))
    else:
        fields.append(Field(name="p", field_class="volScalarField",
                            dimensions=dims_p, internal_field=p_int))
        if def_.needs_gravity and name in ("fluid_buoyant", "compressibleVoF",
                                           "compressibleMultiphaseVoF", "isothermalFluid"):
            fields.append(Field(name="p_rgh", field_class="volScalarField",
                                dimensions=(1, -1, -2, 0, 0, 0, 0), internal_field="uniform 0"))

    # Temperature for every compressible family except multiphaseEuler (per-phase
    # T handled above). isothermalFluid also solves T (potentialFreeSurfaceOscillatingBox).
    if compressible and name != "multiphaseEuler":
        fields.append(Field(name="T", field_class="volScalarField",
                            dimensions=(0, 0, 0, 1, 0, 0, 0),
                            internal_field=f"uniform {_num(intent.temperature)}"))

    if name == "XiFluid":
        fields.append(Field(name="b", field_class="volScalarField",
                            dimensions=(0, 0, 0, 0, 0, 0, 0), internal_field="uniform 0"))
        fields.append(Field(name="Xi", field_class="volScalarField",
                            dimensions=(0, 0, 0, 0, 0, 0, 0), internal_field="uniform 1"))
        fields.append(Field(name="Tu", field_class="volScalarField",
                            dimensions=(0, 0, 0, 1, 0, 0, 0),
                            internal_field=f"uniform {_num(intent.temperature)}"))

    if def_.species:
        for sp in def_.species:
            fields.append(Field(name=sp, field_class="volScalarField",
                                dimensions=(0, 0, 0, 0, 0, 0, 0),
                                internal_field=f"uniform {_num(1.0 / len(def_.species))}"))

    # Turbulence for everything except inviscid shockFluid and potential isothermalFluid.
    if name not in ("shockFluid", "isothermalFluid"):
        _add_turb_fields(fields, turbulent, compressible, k0, eps0)
    return fields


def _active_field_names(fields: list[Field]) -> list[str]:
    return [f.name for f in fields]


def intent_to_spec(intent: CaseIntent) -> CaseSpec:
    """Deterministically expand a compact CaseIntent into a full v13 CaseSpec."""
    def_ = _resolve_case_type(intent)
    name = def_.name
    # Solids and films do not solve turbulence; force laminar regardless of regime.
    forced_laminar = def_.is_solid or def_.is_film
    turbulent = (intent.regime == "turbulent") and not forced_laminar
    steady = intent.solver_mode == "steadyState"
    compressible = def_.flow_type == "compressible"
    k0, eps0 = _turbulence_values(intent) if turbulent else (0.0, 0.0)

    faces = _assign_faces(intent.patches) if intent.geometry == "box" else {}
    fields = _case_fields(def_, intent, turbulent, k0, eps0)
    active = _active_field_names(fields)
    patches = [
        Patch(
            name=p.name,
            type=("empty" if p.role == "empty"
                  else "cyclic" if p.role == "cyclic"
                  else "symmetryPlane" if p.role == "symmetry"
                  else "wall" if p.role in ("wall", "movingWall")
                  else "patch"),
            faces=faces.get(p.name, []),
            bc=_bc_for_role(p.role, p, turbulent, compressible, k0, eps0,
                            intent.temperature, intent.p_ambient, active_fields=active),
        )
        for p in intent.patches
    ]

    if intent.geometry == "box":
        if intent.box_size is None:
            raise ValueError("box geometry requires box_size")
        lx, ly, lz = intent.box_size
        geometry = BoxGeometry(convert_to_meters=1.0, x=(0, lx), y=(0, ly), z=(0, lz),
                               cells=intent.cells, grading=(1, 1, 1))
    else:
        geometry = ExternalMesh()

    # Property dictionary. Solid / incompressible families keep a constant nu;
    # all compressible gas families carry thermo (per-species when named).
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

    # Numerics preset. The base single-phase families keep the legacy selection;
    # the rest use the case-type's grounded preset (all keys exist in renderer.py).
    if name == "incompressibleFluid":
        numerics = "incompressibleFluid_upwind" if steady else "incompressibleFluid_limitedLinear"
    elif name == "fluid":
        numerics = "compressibleFluid_steadyState" if steady else "compressibleFluid_transient"
    else:
        numerics = def_.default_numerics

    # Agent-chosen custom fvSchemes/fvSolution override the static preset. The
    # preset keys above are all defined in renderer.py, so no RAG fallback needed.
    final_schemes = intent.custom_fv_schemes
    final_solution = intent.custom_fv_solution

    is_film = def_.is_film
    return CaseSpec(
        solver=def_.solver,
        case_type=def_.name,
        geometry=geometry,
        patches=patches,
        fields=fields,
        turbulence=TurbulenceRAS(simulation_type="RAS" if turbulent else "laminar",
                                 model=intent.turbulence_model or "kEpsilon",
                                 turbulence=turbulent),
        fluid=fluid,
        time=_time_control(intent),
        numerics=numerics,
        custom_fv_schemes=final_schemes,
        custom_fv_solution=final_solution,
        extra_files=intent.extra_files or {},
        gravity=intent.gravity if intent.gravity is not None
                else ((0, -9.81, 0) if def_.needs_gravity else None),
        phases=list(intent.phases or def_.phases),
        surface_tension=(intent.surface_tension if intent.surface_tension is not None
                         else def_.surface_tension),
        relative_velocity_model=def_.relative_velocity_model,
        solid=def_.is_solid,
        is_film=is_film,
        species=list(def_.species),
        per_phase_properties=def_.per_phase_properties,
        compressible_phases=def_.compressible_phases,
    )


def _cfl_delta_t(intent: CaseIntent) -> float:
    """A conservative first deltaT from a Courant estimate (cell size / velocity).

    General, scale-aware default: no case-specific constants. Clamped to
    [1e-7, 0.1] so absurd inputs can't produce a runaway timestep.
    """
    if intent.box_size is None:
        return 1e-4
    dx = min(
        b / n for b, n in zip(intent.box_size, intent.cells) if n > 0
    )
    co_target = 0.2 if intent.flow_type == "compressible" else 0.5
    dt = co_target * dx / _max_velocity(intent)
    return max(1e-7, min(0.1, dt))


def _external_mesh_delta_t(intent: CaseIntent) -> float:
    """Scale-aware starting deltaT for external meshes based on peak velocity.

    High-speed compressible flows (e.g. 50-200 m/s in nozzles) on fine mesh
    nozzle throats (dx ~ 1e-4..1e-3 m) require a small initial deltaT (1e-6..1e-5 s)
    to prevent first-step Courant explosion. adjustTimeStep will ramp deltaT up.
    """
    vmax = _max_velocity(intent)
    dx_est = 1e-3  # conservative feature scale estimate
    co_target = 0.2 if intent.flow_type == "compressible" else 0.5
    dt = co_target * dx_est / vmax
    return max(1e-7, min(1e-3, dt))


def _time_control(intent: CaseIntent) -> TimeControl:
    """Pick a starting deltaT + adaptive-timestep policy.

    All transient cases get adaptive stepping (adjustTimeStep + maxCo) so the
    solver self-tunes the timestep to whatever mesh/velocity scale it finds;
    the starting deltaT is only a conservative first guess. Steady-state cases
    step directly with a unit deltaT.
    """
    if intent.solver_mode == "steadyState":
        return TimeControl(
            start_time=0, end_time=1000, delta_t=1.0, write_interval=10,
            adjust_time_step="no",
        )
    max_co = 0.5 if intent.flow_type == "compressible" else 0.8
    if intent.geometry == "box":
        return TimeControl(
            start_time=0, end_time=0.1, delta_t=_cfl_delta_t(intent), write_interval=20,
            adjust_time_step="yes", max_co=max_co,
        )
    # external mesh: scale unknown, so start velocity-scaled and let the adaptivity work.
    return TimeControl(
        start_time=0, end_time=0.1, delta_t=_external_mesh_delta_t(intent), write_interval=20,
        adjust_time_step="yes", max_co=max_co,
    )
