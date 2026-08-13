"""Tests for compressible flow, rotational cyclic boundary conditions, and steady-state SIMPLE solvers.
Focused on the Ranque-Hilsch Vortex Tube (RHVT) case specification.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ir import CaseIntent, PatchIntent, intent_to_spec
from mesh import load_external_mesh
from rendering import render


def vortex_tube_intent() -> CaseIntent:
    return CaseIntent(
        regime="turbulent",
        flow_type="compressible",
        solver_mode="steadyState",
        p_ambient=400000.0,
        temperature=300.0,
        geometry="external_mesh",
        patches=[
            PatchIntent(
                name="inlet",
                role="inlet",
                velocity=(0.0, -0.359397644, 0.933184512),
                temperature=300.0,
            ),
            PatchIntent(name="cold_outlet", role="outlet", pressure=400000.0),
            PatchIntent(name="hot_outlet", role="outlet", pressure=400000.0),
            PatchIntent(name="walls", role="wall"),
            PatchIntent(name="left_symmetry", role="cyclic"),
            PatchIntent(name="right_symmetry", role="cyclic"),
        ],
    )


def test_vortex_tube_intent_to_spec():
    intent = vortex_tube_intent()
    spec = intent_to_spec(intent)

    assert spec.solver == "fluid"
    assert spec.numerics == "compressibleFluid_steadyState"
    assert spec.fluid.flow_type == "compressible"

    field_names = {f.name for f in spec.fields}
    assert field_names == {"U", "p", "T", "k", "epsilon", "nut", "alphat"}

    # Pressure dimensions in Pascals [kg / (m * s^2)]
    p_field = next(f for f in spec.fields if f.name == "p")
    assert p_field.dimensions == (1, -1, -2, 0, 0, 0, 0)
    assert p_field.internal_field == "uniform 400000"

    # Cyclic patches should have type == "cyclic" across all fields without values
    for patch_name in ("left_symmetry", "right_symmetry"):
        patch = next(p for p in spec.patches if p.name == patch_name)
        assert patch.type == "cyclic"
        for field in field_names:
            bc = patch.bc[field]
            assert bc.type == "cyclic"
            assert bc.value in (None, "$internalField")

    # Walls patch should use wall functions for turbulence and thermal diffusivity
    walls = next(p for p in spec.patches if p.name == "walls")
    assert walls.bc["k"].type == "kqRWallFunction"
    assert walls.bc["epsilon"].type == "epsilonWallFunction"
    assert walls.bc["nut"].type == "nutkWallFunction"
    assert walls.bc["alphat"].type == "compressible::alphatJayatillekeWallFunction"


def test_vortex_tube_rendering():
    spec = intent_to_spec(vortex_tube_intent())
    case = render(spec)

    assert "controlDict" in case.system
    assert "fvSchemes" in case.system
    assert "fvSolution" in case.system
    assert "physicalProperties" in case.constant
    assert "momentumTransport" in case.constant

    for f in ("U", "p", "T", "k", "epsilon", "nut", "alphat"):
        assert f in case.zero

    # Physical properties check
    phys = case.constant["physicalProperties"]
    assert "thermoType" in phys
    assert "heRhoThermo" in phys
    assert "perfectGas" in phys
    assert "molWeight" in phys
    assert "Cp" in phys

    # fvSchemes & fvSolution check
    schemes = case.system["fvSchemes"]
    assert "steadyState" in schemes
    assert "limitedLinear01" in schemes

    sol = case.system["fvSolution"]
    assert "SIMPLE" in sol
    assert "relaxationFactors" in sol


def test_grounded_mesh_patch_extraction(tmp_path):
    import pytest
    mesh_dir = Path(__file__).resolve().parents[1] / "temp" / "case-files"
    if not (mesh_dir / "boundary").is_file() and not (mesh_dir / "constant" / "polyMesh" / "boundary").is_file():
        pytest.skip("local scratch mesh directory temp/case-files not present")
    known_patches = load_external_mesh(mesh_dir)
    names = {p.name for p in known_patches}

    assert names == {
        "cold_outlet",
        "hot_outlet",
        "inlet",
        "left_symmetry",
        "right_symmetry",
        "walls",
    }

    cyclic_patches = [p for p in known_patches if p.type == "cyclic"]
    assert len(cyclic_patches) == 2


def test_vortex_tube_transient_numerics():
    intent = vortex_tube_intent()
    intent.solver_mode = "transient"
    spec = intent_to_spec(intent)
    case = render(spec)

    assert spec.numerics == "compressibleFluid_transient"
    schemes = case.system["fvSchemes"]
    assert "Euler" in schemes
    assert "steadyState" not in schemes
    sol = case.system["fvSolution"]
    assert "PIMPLE" in sol
    assert "nOuterCorrectors 2" in sol
    assert "rho        0.05" in sol


def test_multi_inlet_swirling_vortex_tube():
    """Multi-inlet nozzle geometry: intent_to_spec + renderer must seed internal U
    from the strongest inlet vector and render all discrete inlet BCs cleanly."""
    intent = CaseIntent(
        regime="turbulent",
        flow_type="compressible",
        solver_mode="transient",
        p_ambient=501325.0,
        temperature=305.0,
        geometry="external_mesh",
        patches=[
            PatchIntent(name="inlet_1", role="inlet", velocity=(0, 0, 200), temperature=300),
            PatchIntent(name="inlet_2", role="inlet", velocity=(0, -173.2, 100), temperature=300),
            PatchIntent(name="inlet_3", role="inlet", velocity=(0, -173.2, -100), temperature=300),
            PatchIntent(name="inlet_4", role="inlet", velocity=(0, 0, -200), temperature=300),
            PatchIntent(name="inlet_5", role="inlet", velocity=(0, 173.2, -200), temperature=300),
            PatchIntent(name="inlet_6", role="inlet", velocity=(0, 173.2, 200), temperature=300),
            PatchIntent(name="cold_outlet", role="outlet", pressure=501325.0),
            PatchIntent(name="hot_outlet", role="outlet", pressure=501325.0),
            PatchIntent(name="walls", role="wall"),
            PatchIntent(name="left_symmetry", role="cyclic"),
            PatchIntent(name="right_symmetry", role="cyclic"),
        ],
    )
    spec = intent_to_spec(intent)
    u_field = next(f for f in spec.fields if f.name == "U")
    # dominant velocity vector mag^2 is 0^2 + 173.2^2 + 200^2 = 70000 -> vector magnitude ~264 m/s
    assert "uniform (0" in u_field.internal_field

    case = render(spec)
    u_content = case.zero["U"]
    assert "inlet_1" in u_content
    assert "inlet_6" in u_content
    assert "type            fixedValue;" in u_content
    assert "left_symmetry" in u_content
    assert "value           $internalField;" in u_content


def test_cht_conjugate_heat_transfer_rendering():
    """CHT conjugate heat transfer setup: compressible thermal coupling, energy
    solvers, and physicalProperties with wall functions."""
    intent = CaseIntent(
        regime="turbulent",
        flow_type="compressible",
        fluid_species="air",
        p_ambient=101325.0,
        temperature=350.0,
        geometry="box",
        box_size=(0.5, 0.1, 0.1),
        cells=(50, 10, 10),
        patches=[
            PatchIntent(name="inlet", role="inlet", velocity=(5, 0, 0), temperature=350.0),
            PatchIntent(name="outlet", role="outlet", pressure=101325.0),
            PatchIntent(name="heated_wall", role="wall", temperature=450.0),
            PatchIntent(name="fixedWalls", role="wall"),
            PatchIntent(name="frontAndBack", role="empty"),
        ],
    )
    spec = intent_to_spec(intent)
    case = render(spec)

    assert "T" in case.zero
    assert "alphat" in case.zero
    t_content = case.zero["T"]
    assert "heated_wall" in t_content
    assert "uniform 450" in t_content

    alphat_content = case.zero["alphat"]
    assert "compressible::alphatJayatillekeWallFunction" in alphat_content


if __name__ == "__main__":
    test_vortex_tube_intent_to_spec()
    test_vortex_tube_rendering()
    test_vortex_tube_transient_numerics()
    test_multi_inlet_swirling_vortex_tube()
    test_cht_conjugate_heat_transfer_rendering()
    print("All vortex tube and CHT tests passed!")

