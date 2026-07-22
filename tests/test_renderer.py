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

from agents.renderer import BANNER, FOOTER, render
from agents.spec import (
    BoxGeometry,
    CaseSpec,
    Field,
    FieldBC,
    FluidProperties,
    Patch,
    TimeControl,
    TurbulenceRAS,
)


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


if __name__ == "__main__":
    n = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
            n += 1
    print(f"\n{n} tests passed")
