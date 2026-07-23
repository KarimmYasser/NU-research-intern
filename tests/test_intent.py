"""Tests for the deterministic CaseIntent -> CaseSpec expander (intent_to_spec).

Pure logic - no LLM, no Docker - so it runs fast anywhere. The end-to-end
NL->run path (extract_intent + generate) is an integration test needing the
local model endpoint and Docker, exercised separately.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agents.intent import CaseIntent, PatchIntent, intent_to_spec


def cavity_intent(regime: str = "turbulent") -> CaseIntent:
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


if __name__ == "__main__":
    n = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
            n += 1
    print(f"\n{n} tests passed")
