"""Tests for the deterministic CaseIntent -> CaseSpec expander (intent_to_spec).

Pure logic - no LLM, no Docker - so it runs fast anywhere. The end-to-end
NL->run path (extract_intent + generate) is an integration test needing the
local model endpoint and Docker, exercised separately.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ir import CaseIntent, PatchIntent, Regime, intent_to_spec
from rendering import render


def cavity_intent(regime: Regime = "turbulent") -> CaseIntent:
    return CaseIntent(
        regime=regime, nu=1e-5, geometry="box", box_size=(0.1, 0.1, 0.1), cells=(20, 20, 1),
        patches=[
            PatchIntent(name="movingWall", role="movingWall", velocity=(1, 0, 0)),
            PatchIntent(name="fixedWalls", role="wall"),
            PatchIntent(name="frontAndBack", role="empty"),
        ],
    )


def _field(spec, name):
    return next(f for f in spec.fields if f.name == name)


def test_turbulence_values_match_openfoam_cavity():
    # k = 1.5*(0.05*1)^2 = 0.00375; eps = Cmu^0.75 k^1.5 / (0.05*0.1) ~ 0.00754.
    # These are exactly the OpenFOAM cavity tutorial's inlet values.
    spec = intent_to_spec(cavity_intent())
    assert _field(spec, "k").internal_field == "uniform 0.00375"
    assert _field(spec, "epsilon").internal_field.startswith("uniform 0.00754")


def test_turbulent_field_set():
    spec = intent_to_spec(cavity_intent())
    assert {f.name for f in spec.fields} == {"U", "p", "k", "epsilon", "nut"}


def test_face_assignment_matches_cavity_layout():
    spec = intent_to_spec(cavity_intent())
    faces = {p.name: p.faces for p in spec.patches}
    assert faces["movingWall"] == ["yMax"]
    assert faces["fixedWalls"] == ["xMin", "xMax", "yMin"]
    assert faces["frontAndBack"] == ["zMin", "zMax"]


def test_boundary_conditions_by_role():
    spec = intent_to_spec(cavity_intent())
    mw = next(p for p in spec.patches if p.name == "movingWall")
    assert mw.bc["U"].type == "fixedValue" and mw.bc["U"].value == "uniform (1 0 0)"
    assert mw.bc["k"].type == "kqRWallFunction"
    assert mw.bc["nut"].type == "nutkWallFunction"
    fw = next(p for p in spec.patches if p.name == "fixedWalls")
    assert fw.bc["U"].type == "noSlip"
    fb = next(p for p in spec.patches if p.name == "frontAndBack")
    assert all(bc.type == "empty" for bc in fb.bc.values())


def test_laminar_has_no_turbulence():
    spec = intent_to_spec(cavity_intent(regime="laminar"))
    assert {f.name for f in spec.fields} == {"U", "p"}
    assert spec.turbulence.simulation_type == "laminar"


def test_fluid_species_thermo():
    intent = CaseIntent(
        flow_type="compressible",
        fluid_species="nitrogen",
        box_size=(0.1, 0.1, 0.1),
        patches=[PatchIntent(name="walls", role="wall")],
    )
    spec = intent_to_spec(intent)
    assert spec.fluid.thermo is not None
    assert spec.fluid.thermo.mol_weight == 28.013
    assert spec.fluid.thermo.cp == 1040


def test_bc_overrides_applied_to_spec():
    intent = CaseIntent(
        flow_type="compressible",
        geometry="box",
        box_size=(0.1, 0.1, 0.1),
        patches=[
            PatchIntent(
                name="inlet",
                role="inlet",
                bc_overrides={"p": {"type": "totalPressure", "value": "uniform 400000"}}
            ),
            PatchIntent(name="walls", role="wall"),  # closes the box
        ]
    )
    spec = intent_to_spec(intent)
    inlet_patch = next(p for p in spec.patches if p.name == "inlet")
    assert inlet_patch.bc["p"].type == "totalPressure"
    assert inlet_patch.bc["p"].value == "uniform 400000"


def test_requested_patch_names_and_only_requested_structure_reach_rendering():
    """Tutorial grounding must not copy reference patch names or optional files."""
    intent = CaseIntent(
        regime="laminar",
        geometry="box",
        box_size=(1, 1, 0.1),
        patches=[
            PatchIntent(name="custom_lid", role="movingWall", velocity=(2, 0, 0)),
            PatchIntent(name="custom_walls", role="wall"),
            PatchIntent(name="thin_direction", role="empty"),
        ],
    )
    spec = intent_to_spec(intent)
    case = render(spec)
    assert {patch.name for patch in spec.patches} == {"custom_lid", "custom_walls", "thin_direction"}
    assert "movingWall" not in case.zero["U"]
    assert "fixedWalls" not in case.zero["U"]
    assert "frontAndBack" not in case.zero["U"]
    assert "fvConstraints" not in case.system
    assert "functions" not in case.system


def test_turbulence_model_override_propagates():
    intent = CaseIntent(
        regime="turbulent",
        turbulence_model="kOmegaSST",
        geometry="box",
        box_size=(0.1, 0.1, 0.1),
        patches=[PatchIntent(name="walls", role="wall")]
    )
    spec = intent_to_spec(intent)
    assert spec.turbulence.model == "kOmegaSST"


def test_explicit_faces_tile_the_box():
    """LLM-chosen faces: a 6-patch box with each face named explicitly."""
    intent = CaseIntent(
        geometry="box", box_size=(1.0, 1.0, 1.0), cells=(10, 10, 10),
        patches=[
            PatchIntent(name="inlet", role="inlet", faces=["xMin"]),
            PatchIntent(name="outlet", role="outlet", faces=["xMax"]),
            PatchIntent(name="top", role="wall", faces=["yMax"]),
            PatchIntent(name="bottom", role="wall", faces=["yMin"]),
            PatchIntent(name="front", role="wall", faces=["zMax"]),
            PatchIntent(name="back", role="wall", faces=["zMin"]),
        ],
    )
    spec = intent_to_spec(intent)
    faces = {p.name: p.faces for p in spec.patches}
    assert faces["inlet"] == ["xMin"]
    assert faces["outlet"] == ["xMax"]
    assert faces["top"] == ["yMax"]
    assert faces["front"] == ["zMax"]


def test_two_inlets_require_explicit_faces():
    """Two inlets can't be expressed by the canonical map (both would take
    xMin); intent_to_spec must raise and explicit faces must resolve it."""
    intent = CaseIntent(
        geometry="box", box_size=(1.0, 1.0, 1.0),
        patches=[
            PatchIntent(name="in1", role="inlet"),
            PatchIntent(name="in2", role="inlet"),
            PatchIntent(name="out", role="outlet"),
            PatchIntent(name="walls", role="wall"),
        ],
    )
    with pytest.raises(ValueError):
        intent_to_spec(intent)

    intent.patches[0].faces = ["xMin"]
    intent.patches[1].faces = ["yMin"]
    intent.patches[2].faces = ["xMax"]
    intent.patches[3].faces = ["yMax", "zMin", "zMax"]
    spec = intent_to_spec(intent)
    faces = {p.name: p.faces for p in spec.patches}
    assert faces["in1"] == ["xMin"] and faces["in2"] == ["yMin"]
    assert faces["walls"] == ["yMax", "zMin", "zMax"]


def test_3d_box_closes_z_faces_into_wall():
    """A genuinely 3D box (no empty patch) must absorb the z faces or the mesh
    would be open."""
    intent = CaseIntent(
        geometry="box", box_size=(0.1, 0.1, 0.1), cells=(10, 10, 10),
        patches=[
            PatchIntent(name="inlet", role="inlet"),
            PatchIntent(name="outlet", role="outlet"),
            PatchIntent(name="walls", role="wall"),
        ],
    )
    spec = intent_to_spec(intent)
    walls = next(p for p in spec.patches if p.name == "walls")
    assert set(walls.faces) == {"yMin", "yMax", "zMin", "zMax"}


def test_symmetry_role_renders_symmetryplane():
    intent = CaseIntent(
        geometry="box", box_size=(1.0, 1.0, 1.0),
        patches=[
            PatchIntent(name="inlet", role="inlet"),
            PatchIntent(name="outlet", role="outlet"),
            PatchIntent(name="midplane", role="symmetry"),
            PatchIntent(name="walls", role="wall"),
        ],
    )
    spec = intent_to_spec(intent)
    mp = next(p for p in spec.patches if p.name == "midplane")
    assert mp.type == "symmetryPlane"
    assert mp.bc["U"].type == "symmetryPlane"
    assert mp.bc["p"].type == "symmetryPlane"
    assert mp.faces == ["yMin"]


def test_custom_fv_solution_and_schemes_propagate():
    custom_sol = "relaxationFactors { fields { p 0.05; } }"
    custom_sch = "divSchemes { default none; div(phi,U) Gauss upwind; }"
    intent = CaseIntent(
        geometry="box",
        box_size=(0.1, 0.1, 0.1),
        custom_fv_solution=custom_sol,
        custom_fv_schemes=custom_sch,
        patches=[PatchIntent(name="walls", role="wall")]
    )
    spec = intent_to_spec(intent)
    assert spec.custom_fv_solution == custom_sol
    assert spec.custom_fv_schemes == custom_sch


def _vof_intent(case_type="incompressibleVoF"):
    return CaseIntent(
        case_type=case_type, regime="laminar",
        geometry="box", box_size=(0.6, 0.3, 0.01), cells=(40, 20, 1),
        patches=[
            PatchIntent(name="atmosphere", role="outlet", pressure=0.0),
            PatchIntent(name="tank", role="wall"),
            PatchIntent(name="frontBack", role="empty"),
        ],
    )


def test_vof_case_type_field_pressure_and_defaults():
    spec = intent_to_spec(_vof_intent())
    assert spec.solver == "incompressibleVoF"
    assert spec.numerics == "incompressibleVoF"
    # free-surface hydrostatic pressure: p_rgh only, never p
    names = {f.name for f in spec.fields}
    assert names == {"U", "p_rgh", "alpha.water"}
    # gravity defaults to -g z, phases + sigma from the registry
    assert spec.gravity == (0, -9.81, 0)
    assert spec.phases == ["water", "air"]
    assert spec.surface_tension == 0.07
    assert spec.per_phase_properties is True
    # alpha.water BC on the outlet (inletOutlet-style would need a value; role default is zeroGradient)
    atm = next(p for p in spec.patches if p.name == "atmosphere")
    assert atm.bc["alpha.water"].type == "zeroGradient"
    assert atm.bc["p_rgh"].type == "fixedValue"


def test_multiphase_euler_per_phase_fields():
    intent = _vof_intent(case_type="multiphaseEuler")
    intent.regime = "turbulent"
    spec = intent_to_spec(intent)
    names = {f.name for f in spec.fields}
    assert {"alpha.air", "alpha.water", "U.air", "U.water", "T.air", "T.water",
            "k.air", "k.water", "p", "p_rgh"} <= names
    assert "T" not in names  # per-phase temperature only, no global T
    assert spec.phases == ["air", "water"]


def test_xifluid_species_and_combustion_fields():
    intent = _vof_intent(case_type="XiFluid")
    intent.regime = "turbulent"
    spec = intent_to_spec(intent)
    names = {f.name for f in spec.fields}
    assert {"b", "Xi", "Tu", "fu", "ft"} <= names
    assert spec.species == ["fu", "ft"]
    assert spec.gravity is None  # grounded: XiFluid has no g/p_rgh


def test_solid_displacement_forced_laminar_and_g_fields():
    intent = CaseIntent(
        case_type="solidDisplacement", regime="turbulent",
        geometry="box", box_size=(1.0, 1.0, 0.1),
        patches=[PatchIntent(name="mech", role="wall")],
    )
    spec = intent_to_spec(intent)
    names = {f.name for f in spec.fields}
    assert names == {"D", "T"}
    assert spec.turbulence.simulation_type == "laminar"  # solids never RAS
    assert spec.gravity is None
    assert spec.solid is True


def test_isothermal_fluid_fields():
    spec = intent_to_spec(_vof_intent(case_type="isothermalFluid"))
    names = {f.name for f in spec.fields}
    assert names == {"U", "p", "p_rgh", "T"}  # potential flow: no turbulence


def test_all_case_types_render_ready():
    """Every registered case_type expands to a spec — the registry stays in sync
    with the field planner."""
    from ir.case_types import CASE_TYPES
    for ct in CASE_TYPES:
        intent = CaseIntent(
            case_type=ct, regime="turbulent",
            geometry="box", box_size=(1.0, 1.0, 1.0), cells=(10, 10, 10),
            patches=[PatchIntent(name="walls", role="wall")],
        )
        try:
            intent_to_spec(intent)
        except Exception as e:  # fail the test with context
            raise AssertionError(f"case_type '{ct}' fails to expand: {e}") from e


def test_initial_U_seeded_from_dominant_inlet():
    intent = CaseIntent(
        regime="turbulent", flow_type="compressible",
        geometry="external_mesh",
        patches=[
            PatchIntent(name="inlet_slow", role="inlet", velocity=(0, 10, 0)),
            PatchIntent(name="inlet_fast", role="inlet", velocity=(0, 0, 200)),
            PatchIntent(name="outlet", role="outlet", pressure=100000),
            PatchIntent(name="walls", role="wall"),
        ],
    )
    spec = intent_to_spec(intent)
    u_field = _field(spec, "U")
    assert u_field.internal_field == "uniform (0 0 200)"


def test_compressible_outlet_uses_inlet_outlet_bc():
    intent = CaseIntent(
        regime="turbulent", flow_type="compressible",
        geometry="external_mesh",
        patches=[
            PatchIntent(name="inlet", role="inlet", velocity=(50, 0, 0)),
            PatchIntent(name="outlet", role="outlet", pressure=101325),
            PatchIntent(name="walls", role="wall"),
        ],
    )
    spec = intent_to_spec(intent)
    out_patch = next(p for p in spec.patches if p.name == "outlet")
    assert out_patch.bc["U"].type == "inletOutlet"
    assert out_patch.bc["T"].type == "inletOutlet"


def test_external_mesh_adaptive_delta_t_scales_with_velocity():
    # Fast flow (200 m/s) -> small initial deltaT to avoid first-step shock
    fast_intent = CaseIntent(
        regime="turbulent", flow_type="compressible",
        geometry="external_mesh",
        patches=[
            PatchIntent(name="inlet", role="inlet", velocity=(0, 0, 200)),
            PatchIntent(name="outlet", role="outlet", pressure=100000),
            PatchIntent(name="walls", role="wall"),
        ],
    )
    fast_spec = intent_to_spec(fast_intent)
    assert fast_spec.time.adjust_time_step == "yes"
    assert fast_spec.time.max_co == 0.5
    assert fast_spec.time.delta_t <= 1e-5

    # Slow flow (1 m/s) -> standard initial deltaT
    slow_intent = CaseIntent(
        regime="turbulent", flow_type="incompressible",
        geometry="external_mesh",
        patches=[
            PatchIntent(name="inlet", role="inlet", velocity=(0, 0, 1)),
            PatchIntent(name="outlet", role="outlet", pressure=0),
            PatchIntent(name="walls", role="wall"),
        ],
    )
    slow_spec = intent_to_spec(slow_intent)
    assert slow_spec.time.adjust_time_step == "yes"
    assert slow_spec.time.delta_t >= 1e-4


if __name__ == "__main__":
    n = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
            n += 1
    print(f"\n{n} tests passed")

