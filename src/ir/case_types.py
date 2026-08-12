"""CaseType registry: maps every OpenFOAM v13 solver-module family from the
official tutorials (temp/openfoam-repo/OpenFOAM-13/tutorials) to the structural
facts the deterministic renderer needs to produce a valid case.

Architecture fit (docs/phase-ii-architecture.md §2, "reason free, constrain
late"): the LLM picks a `case_type` from this registry plus a small number of
physics numbers (regime, phases, sigma, gravity, ...). Every mechanical detail —
which fields exist, their dimensions, which property dictionaries appear, whether
a constant/g + p_rgh are needed, which numerics preset applies — is a property
of code here, not of the model. Details below are grounded in the real v13
tutorial files, not blog paraphrase:

  incompressibleFluid          -> single-phase, U + p (+ turbulence)
  fluid                        -> compressible/thermal single-phase, U + p + T
                                   (+ buoyancy when gravity is set: Boussinesq
                                   thermo, g file, p_rgh)
  incompressibleVoF            -> two-phase free surface, alpha.<phase>, p_rgh
  incompressibleMultiphaseVoF  -> n-phase free surface, sigmas list
  compressibleVoF              -> compressible two-phase, per-phase thermo
  compressibleMultiphaseVoF    -> compressible n-phase, sigmas list
  incompressibleDriftFlux      -> dispersed two-phase, relative velocity model
  multiphaseEuler              -> Euler-Euler, per-phase alpha/U/T
  incompressibleDenseParticleFluid -> two-fluid particle-laden (MPPIC-style)
  film / isothermalFilm        -> thin liquid film, delta + U + p
  isothermalFluid              -> potential free-surface flow
  shockFluid                   -> inviscid high-speed gas, no rho field
  solidDisplacement            -> solid mechanics, displacement D (+ T)
  multicomponentFluid          -> species transport, one field per species
  XiFluid                      -> premixed combustion, b + Xi + Tu + species
  movingMesh                   -> dynamic mesh (dynamicMeshDict) + incompressible fluid

Every entry records: the controlDict solver module name, the flow_type literal,
whether it needs constant/g (and thus p_rgh), the default phases (used to build
alpha.<phase> / per-phase property dictionaries), and the default numerics preset.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

FlowType = Literal["incompressible", "compressible", "solid"]


@dataclass(frozen=True)
class CaseTypeDef:
    name: str
    solver: str
    flow_type: FlowType
    # Whether the case needs constant/g + p_rgh (VoF, multiphase, buoyant, films).
    needs_gravity: bool = False
    # Default phase names. VoF/multiphase families render alpha.<phase> fields and
    # constant/phaseProperties; per-phase physicalProperties.<phase> dictionaries.
    phases: tuple[str, ...] = ()
    per_phase_properties: bool = False  # constant/physicalProperties.<phase> per phase
    compressible_phases: bool = False   # per-phase files carry thermo (heRhoThermo) not nu/rho
    surface_tension: float | None = None  # default sigma [N/m] when single-scalar
    relative_velocity_model: str | None = None  # drift-flux: simple / general / ...
    # Default species for multicomponent / combustion cases (mass-fraction fields).
    species: tuple[str, ...] = ()
    is_solid: bool = False       # solidDisplacement: fields = D (+T), solid property dict
    is_film: bool = False        # film families: fields = delta + p + T + U
    needs_thermo_transport: bool = False  # compressible turbulent: constant/thermophysicalTransport
    default_numerics: str = "incompressibleFluid_limitedLinear"
    notes: str = ""


CASE_TYPES: dict[str, CaseTypeDef] = {
    "incompressibleFluid": CaseTypeDef(
        name="incompressibleFluid",
        solver="incompressibleFluid",
        flow_type="incompressible",
        default_numerics="incompressibleFluid_limitedLinear",
        notes="Single-phase incompressible Newtonian fluid (cavity, channel, duct).",
    ),
    "fluid": CaseTypeDef(
        name="fluid",
        solver="fluid",
        flow_type="compressible",
        needs_thermo_transport=True,
        default_numerics="compressibleFluid_steadyState",
        notes="Compressible/thermal single-phase flow; add gravity for buoyancy (p_rgh).",
    ),
    "fluid_buoyant": CaseTypeDef(
        name="fluid_buoyant",
        solver="fluid",
        flow_type="compressible",
        needs_gravity=True,
        needs_thermo_transport=True,
        default_numerics="compressibleFluid_steadyState",
        notes="Compressible buoyant flow (hotRoomBoussinesq): g + p_rgh + Boussinesq thermo.",
    ),
    "incompressibleVoF": CaseTypeDef(
        name="incompressibleVoF",
        solver="incompressibleVoF",
        flow_type="incompressible",
        needs_gravity=True,
        phases=("water", "air"),
        per_phase_properties=True,
        surface_tension=0.07,
        default_numerics="incompressibleVoF",
        notes="Two-phase free surface with interface compression (damBreak, capillaryRise).",
    ),
    "incompressibleMultiphaseVoF": CaseTypeDef(
        name="incompressibleMultiphaseVoF",
        solver="incompressibleMultiphaseVoF",
        flow_type="incompressible",
        needs_gravity=True,
        phases=("water", "oil", "mercury", "air"),
        per_phase_properties=True,
        surface_tension=0.07,
        default_numerics="incompressibleVoF",
        notes="n-phase incompressible free surface; pairwise `sigmas` list in phaseProperties.",
    ),
    "compressibleVoF": CaseTypeDef(
        name="compressibleVoF",
        solver="compressibleVoF",
        flow_type="compressible",
        needs_gravity=True,
        phases=("water", "air"),
        per_phase_properties=True,
        compressible_phases=True,
        surface_tension=0.07,
        default_numerics="compressibleVoF",
        notes="Compressible two-phase VoF with per-phase heRhoThermo dictionaries.",
    ),
    "compressibleMultiphaseVoF": CaseTypeDef(
        name="compressibleMultiphaseVoF",
        solver="compressibleMultiphaseVoF",
        flow_type="compressible",
        needs_gravity=True,
        phases=("water", "oil", "mercury", "air"),
        per_phase_properties=True,
        compressible_phases=True,
        surface_tension=0.07,
        default_numerics="compressibleVoF",
        notes="Compressible n-phase VoF with pairwise sigmas.",
    ),
    "incompressibleDriftFlux": CaseTypeDef(
        name="incompressibleDriftFlux",
        solver="incompressibleDriftFlux",
        flow_type="incompressible",
        needs_gravity=True,
        phases=("sludge", "water"),
        per_phase_properties=True,
        relative_velocity_model="simple",
        default_numerics="incompressibleVoF",
        notes="Dispersed two-phase flow with a relative-velocity model (sewage/clarifier).",
    ),
    "multiphaseEuler": CaseTypeDef(
        name="multiphaseEuler",
        solver="multiphaseEuler",
        flow_type="compressible",
        needs_gravity=True,
        phases=("air", "water"),
        per_phase_properties=True,
        compressible_phases=True,
        default_numerics="incompressibleVoF",
        notes="Euler-Euler: per-phase alpha/U/T, drag+virtual mass via phaseProperties.",
    ),
    "incompressibleDenseParticleFluid": CaseTypeDef(
        name="incompressibleDenseParticleFluid",
        solver="incompressibleDenseParticleFluid",
        flow_type="incompressible",
        needs_gravity=True,
        phases=("cloud", "carrier"),
        default_numerics="incompressibleFluid_limitedLinear",
        notes="Two-fluid dense particle flow: carrier fluid + solid cloud (MPPIC-style).",
    ),
    "isothermalFilm": CaseTypeDef(
        name="isothermalFilm",
        solver="isothermalFilm",
        flow_type="compressible",
        needs_gravity=True,
        is_film=True,
        default_numerics="incompressibleVoF",
        notes="Isothermal thin film: liquid film thickness delta + p + U, film surface/wall patches.",
    ),
    "film": CaseTypeDef(
        name="film",
        solver="film",
        flow_type="compressible",
        needs_gravity=True,
        is_film=True,
        default_numerics="incompressibleVoF",
        notes="Thermal thin film: film thickness delta + p + T + U.",
    ),
    "isothermalFluid": CaseTypeDef(
        name="isothermalFluid",
        solver="isothermalFluid",
        flow_type="compressible",
        needs_gravity=True,
        default_numerics="incompressibleFluid_limitedLinear",
        notes="Potential free-surface flow (isothermal): p + p_rgh + U, no turbulence.",
    ),
    "shockFluid": CaseTypeDef(
        name="shockFluid",
        solver="shockFluid",
        flow_type="compressible",
        default_numerics="compressibleFluid_steadyState",
        notes="Inviscid high-speed compressible flow; rho derived from p/T/thermo (no rho field).",
    ),
    "solidDisplacement": CaseTypeDef(
        name="solidDisplacement",
        solver="solidDisplacement",
        flow_type="solid",
        is_solid=True,
        default_numerics="incompressibleFluid_upwind",
        notes="Linear-elastic solid mechanics: volumetric displacement D (+ optional T).",
    ),
    "multicomponentFluid": CaseTypeDef(
        name="multicomponentFluid",
        solver="multicomponentFluid",
        flow_type="compressible",
        species=("N2", "O2"),
        default_numerics="compressibleFluid_steadyState",
        notes="Multi-component compressible flow: one mass-fraction field per species.",
    ),
    "XiFluid": CaseTypeDef(
        name="XiFluid",
        solver="XiFluid",
        flow_type="compressible",
        species=("fu", "ft"),
        default_numerics="incompressibleFluid_limitedLinear",
        notes="Premixed turbulent combustion (Xi flame-wrinkling): b + Xi + Tu fields.",
    ),
    "movingMesh": CaseTypeDef(
        name="movingMesh",
        solver="incompressibleFluid",
        flow_type="incompressible",
        default_numerics="incompressibleFluid_limitedLinear",
        notes="Dynamic mesh (constant/dynamicMeshDict) wrapped around a flowing case.",
    ),
}

# case_type -> canonical intent defaults used when the prompt fixes one dimension.
DEFAULT_REGIME: dict[str, str] = {"incompressibleFluid": "turbulent", "fluid": "turbulent"}


def get_case_type(name: str) -> CaseTypeDef:
    try:
        return CASE_TYPES[name]
    except KeyError:
        known = sorted(CASE_TYPES)
        raise ValueError(
            f"Unknown case_type '{name}'. Valid options: {known}. "
            "Pick the solver module that matches the physics you are describing."
        ) from None


def is_gravity_case(name: str) -> bool:
    return CASE_TYPES[name].needs_gravity


def is_multiphase(name: str) -> bool:
    return bool(CASE_TYPES[name].phases)


def valid_case_types() -> list[str]:
    return list(CASE_TYPES)