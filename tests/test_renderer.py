"""Regression tests for the deterministic CaseSpec -> v13 renderer (prototype #1).

Self-contained: asserts v13 invariants on the rendered cavity, without depending
on the (gitignored) golden tutorial. The byte-exact comparison against the real
tutorial is done separately in scratch verification.

Run: `python tests/test_renderer.py`  (also pytest-discoverable).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ir import (
    BoxGeometry,
    CaseIntent,
    CaseSpec,
    ExternalMesh,
    Field,
    FieldBC,
    FluidProperties,
    Patch,
    PatchIntent,
    TimeControl,
    TurbulenceRAS,
    intent_to_spec,
)
from rendering import BANNER, FOOTER, render


def cavity_spec() -> CaseSpec:
    return CaseSpec(
        solver="incompressibleFluid",
        geometry=BoxGeometry(
            convert_to_meters=0.1, x=(0, 1), y=(0, 1), z=(0, 0.1),
            cells=(20, 20, 1), grading=(1, 1, 1),
        ),
        patches=[
            Patch(name="movingWall", type="wall", faces=["yMax"], bc={
                "U": FieldBC(type="fixedValue", value="uniform (1 0 0)"),
                "p": FieldBC(type="zeroGradient"),
            }),
            Patch(name="fixedWalls", type="wall", faces=["xMin", "xMax", "yMin"], bc={
                "U": FieldBC(type="noSlip"),
                "p": FieldBC(type="zeroGradient"),
            }),
            Patch(name="frontAndBack", type="empty", faces=["zMin", "zMax"], bc={
                "U": FieldBC(type="empty"),
                "p": FieldBC(type="empty"),
            }),
        ],
        fields=[
            Field(name="U", field_class="volVectorField",
                  dimensions=(0, 1, -1, 0, 0, 0, 0), internal_field="uniform (0 0 0)"),
            Field(name="p", field_class="volScalarField",
                  dimensions=(0, 2, -2, 0, 0, 0, 0), internal_field="uniform 0"),
        ],
        turbulence=TurbulenceRAS(simulation_type="RAS", model="kEpsilon", turbulence=True),
        fluid=FluidProperties(viscosity_model="constant", nu="1e-05 [m^2/s]"),
        time=TimeControl(start_time=0, end_time=10, delta_t=0.005, write_interval=100),
    )


def test_renders_expected_file_set():
    c = render(cavity_spec())
    assert set(c.system) == {"blockMeshDict", "controlDict", "fvSchemes", "fvSolution"}
    assert set(c.constant) == {"physicalProperties", "momentumTransport"}
    assert set(c.zero) == {"U", "p"}


def test_v13_conventions_not_v10():
    c = render(cavity_spec())
    # v13 solver module in controlDict, not an old solver binary.
    assert "solver          incompressibleFluid;" in c.system["controlDict"]
    # v13 property file names; the v10 names must never appear.
    all_text = "\n".join([*c.system.values(), *c.constant.values(), *c.zero.values()])
    assert "transportProperties" not in all_text
    assert "turbulenceProperties" not in all_text
    assert "kEpsilon" in c.constant["momentumTransport"]
    assert "turbulence      on;" in c.constant["momentumTransport"]


def test_geometry_and_bcs():
    c = render(cavity_spec())
    bm = c.system["blockMeshDict"]
    assert "convertToMeters 0.1;" in bm
    assert "(3 7 6 2)" in bm  # movingWall face (yMax), correct hex winding
    u = c.zero["U"]
    assert "dimensions      [0 1 -1 0 0 0 0];" in u
    assert "value           uniform (1 0 0);" in u  # movingWall lid velocity
    for patch in ("movingWall", "fixedWalls", "frontAndBack"):
        assert patch in u  # boundaryField covers every mesh patch


def test_every_file_has_banner_and_footer():
    c = render(cavity_spec())
    for content in [*c.system.values(), *c.constant.values(), *c.zero.values()]:
        assert content.startswith(BANNER)
        assert content.rstrip().endswith(FOOTER)


# --- second case: the NU assessment (turbulent channel, external mesh) --------
# A structurally different case (inlet/outlet, wall functions, all 5 turbulence
# fields, external mesh) that proves the renderer isn't overfit to the cavity,
# and that it emits a clean v13 case free of the assessment's planted errors.

_CHANNEL_FIELDS = ["U", "p", "k", "epsilon", "nut", "nuTilda"]


def _wall_bc():
    return {
        "U": FieldBC(type="noSlip"),
        "p": FieldBC(type="zeroGradient"),
        "k": FieldBC(type="kqRWallFunction", value="uniform 0.375"),
        "epsilon": FieldBC(type="epsilonWallFunction", value="uniform 14.855"),
        "nut": FieldBC(type="nutkWallFunction", value="uniform 0"),
        "nuTilda": FieldBC(type="zeroGradient"),
    }


def channel_spec() -> CaseSpec:
    return CaseSpec(
        solver="incompressibleFluid",
        geometry=ExternalMesh(),
        patches=[
            Patch(name="inlet", type="patch", bc={
                "U": FieldBC(type="fixedValue", value="uniform (10 0 0)"),
                "p": FieldBC(type="zeroGradient"),
                "k": FieldBC(type="fixedValue", value="uniform 0.375"),
                "epsilon": FieldBC(type="fixedValue", value="uniform 14.855"),
                "nut": FieldBC(type="calculated", value="uniform 0"),
                "nuTilda": FieldBC(type="fixedValue", value="uniform 0"),
            }),
            Patch(name="outlet", type="patch", bc={
                "U": FieldBC(type="zeroGradient"),
                "p": FieldBC(type="fixedValue", value="uniform 0"),
                "k": FieldBC(type="zeroGradient"),
                "epsilon": FieldBC(type="zeroGradient"),
                "nut": FieldBC(type="calculated", value="uniform 0"),
                "nuTilda": FieldBC(type="zeroGradient"),
            }),
            Patch(name="upperWall", type="wall", bc=_wall_bc()),
            Patch(name="lowerWall", type="wall", bc=_wall_bc()),  # p covers it (fix)
            Patch(name="frontAndBack", type="empty",
                  bc={f: FieldBC(type="empty") for f in _CHANNEL_FIELDS}),
        ],
        fields=[
            Field(name="U", field_class="volVectorField",
                  dimensions=(0, 1, -1, 0, 0, 0, 0), internal_field="uniform (0 0 0)"),
            Field(name="p", field_class="volScalarField",
                  dimensions=(0, 2, -2, 0, 0, 0, 0), internal_field="uniform 0"),
            Field(name="k", field_class="volScalarField",
                  dimensions=(0, 2, -2, 0, 0, 0, 0), internal_field="uniform 0.375"),
            Field(name="epsilon", field_class="volScalarField",
                  dimensions=(0, 2, -3, 0, 0, 0, 0), internal_field="uniform 14.855"),
            Field(name="nut", field_class="volScalarField",
                  dimensions=(0, 2, -1, 0, 0, 0, 0), internal_field="uniform 0"),
            Field(name="nuTilda", field_class="volScalarField",
                  dimensions=(0, 2, -1, 0, 0, 0, 0), internal_field="uniform 0"),
        ],
        turbulence=TurbulenceRAS(model="kEpsilon", turbulence=True, print_coeffs=True),
        fluid=FluidProperties(nu="1e-06 [m^2/s]", comment="Air Properties"),
        time=TimeControl(start_from="latestTime", end_time=0.3, delta_t=0.0001,
                         write_control="adjustableRunTime", write_interval=0.01,
                         run_time_modifiable="yes", adjust_time_step="yes", max_co=5),
        numerics="incompressibleFluid_upwind",
        functions=["#includeFunc patchAverage(patch=inlet, fields=(p U))"],
    )


def test_channel_external_mesh_no_blockmesh():
    c = render(channel_spec())
    assert "blockMeshDict" not in c.system  # mesh is external
    assert set(c.zero) == set(_CHANNEL_FIELDS)


def test_channel_fixes_assessment_errors():
    c = render(channel_spec())
    all_patches = {"inlet", "outlet", "upperWall", "lowerWall", "frontAndBack"}
    # #1 FoamFile uses { } not [ ]
    assert "FoamFile\n[" not in c.constant["momentumTransport"]
    # #2 no invalid '%%' comment
    everything = [*c.system.values(), *c.constant.values(), *c.zero.values()]
    assert not any("%%" in t for t in everything)
    # #3 every field's boundaryField covers every patch (incl. p/lowerWall)
    for f in _CHANNEL_FIELDS:
        for patch in all_patches:
            assert patch in c.zero[f], f"{f} missing {patch}"
    # #4 v13 solver, no old application binary
    assert "solver          incompressibleFluid;" in c.system["controlDict"]
    assert "application" not in c.system["controlDict"]
    # #6 semicolon after Euler
    assert "default         Euler;" in c.system["fvSchemes"]
    # wall functions rendered
    assert "kqRWallFunction" in c.zero["k"]
    assert "nutkWallFunction" in c.zero["nut"]


# --- case-type constant/ layer -------------------------------------------------
# The deterministic expander emits per-family physics; the renderer must write the
# corresponding constant/ files (g, phaseProperties, per-phase physicalProperties,
# dynamicMeshDict, solid/film property dicts), grounded in the v13 tutorials.


def _box_intent(case_type: str, regime: str = "laminar") -> CaseIntent:
    return CaseIntent(
        case_type=case_type, regime=regime,
        geometry="box", box_size=(1.0, 1.0, 0.1), cells=(10, 10, 1),
        patches=[
            PatchIntent(name="inlet", role="inlet", velocity=(1, 0, 0)),
            PatchIntent(name="outlet", role="outlet"),
            PatchIntent(name="tank", role="wall"),
        ],
    )


def test_vof_constant_layer():
    c = render(intent_to_spec(_box_intent("incompressibleVoF")))
    # per-phase properties replace the base physicalProperties
    assert "physicalProperties" not in c.constant
    assert "physicalProperties.water" in c.constant
    assert "physicalProperties.air" in c.constant
    assert "rho             1000;" in c.constant["physicalProperties.water"]
    assert "nu              1.48e-05;" in c.constant["physicalProperties.air"]
    pp = c.constant["phaseProperties"]
    assert "phases          (water air);" in pp
    assert "sigma           0.07;" in pp
    g = c.constant["g"]
    assert "value           (0 -9.81 0);" in g
    assert "class       uniformDimensionedVectorField;" in g


def test_vof_nphase_pairwise_sigmas():
    c = render(intent_to_spec(_box_intent("incompressibleMultiphaseVoF")))
    pp = c.constant["phaseProperties"]
    assert "phases          (water oil mercury air);" in pp
    assert "sigmas" in pp
    assert "(air mercury) 0.07" in pp
    # every phase gets its own transport dict
    for ph in ("water", "oil", "mercury", "air"):
        assert f"physicalProperties.{ph}" in c.constant


def test_driftflux_relative_velocity_model():
    c = render(intent_to_spec(_box_intent("incompressibleDriftFlux")))
    pp = c.constant["phaseProperties"]
    assert "relativeVelocityModel simple;" in pp
    assert "(simple|general)Coeffs" in pp
    assert "physicalProperties.sludge" in c.constant


def test_multiphase_euler_phaseproperties():
    c = render(intent_to_spec(_box_intent("multiphaseEuler", regime="turbulent")))
    pp = c.constant["phaseProperties"]
    assert "referencePhase  water;" in pp
    assert "type            purePhaseModel;" in pp
    assert "surfaceTension" in pp
    assert "blending" in pp
    assert "phases          (air water);" in pp
    for file in ("physicalProperties.air", "physicalProperties.water"):
        assert file in c.constant


def test_compressible_vof_sigma_dict_and_thermo():
    c = render(intent_to_spec(_box_intent("compressibleVoF")))
    pp = c.constant["phaseProperties"]
    assert "type    liquidProperties;" in pp
    assert "phase   water;" in pp
    water = c.constant["physicalProperties.water"]
    assert "properties      liquid;" in water
    assert "H2O;" in water
    air = c.constant["physicalProperties.air"]
    assert "equationOfState perfectGas;" in air


def test_solid_displacement_constant_layer():
    intent = CaseIntent(
        case_type="solidDisplacement",
        geometry="box", box_size=(1.0, 1.0, 0.1),
        patches=[PatchIntent(name="mech", role="wall")],
    )
    c = render(intent_to_spec(intent))
    assert "momentumTransport" not in c.constant  # solids have no turbulence dict
    phys = c.constant["physicalProperties"]
    for key in ("rho", "nu", "E", "Cv", "kappa"):
        assert f"{{{key}" in phys or f"\n{key}\n" in phys
    assert "value       2e+11;" in phys


def test_moving_mesh_renders_dynamicmeshdict():
    c = render(intent_to_spec(_box_intent("movingMesh")))
    dmd = c.constant["dynamicMeshDict"]
    assert "motionSolver    displacementSBRStress;" in dmd
    assert 'libs            ("libfvMotionSolvers.so");' in dmd


def test_film_families_render_film_physical():
    for ct in ("film", "isothermalFilm"):
        c = render(intent_to_spec(_box_intent(ct)))
        phys = c.constant["physicalProperties"]
        assert "properties      liquid;" in phys
        assert "deltaWet        1e-8;" in phys
        assert "type    constant;" in phys
        assert "sigma   0.07;" in phys
        assert c.constant["g"]  # films are gravity-driven


def test_all_case_types_render():
    from ir.case_types import CASE_TYPES
    for ct in CASE_TYPES:
        c = render(intent_to_spec(_box_intent(ct)))
        # every rendered file carries a valid header and footer
        for content in [*c.system.values(), *c.constant.values(), *c.zero.values()]:
            assert content.startswith(BANNER), ct
            assert content.rstrip().endswith(FOOTER), ct


if __name__ == "__main__":
    n = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
            n += 1
    print(f"\n{n} tests passed")
