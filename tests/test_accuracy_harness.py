"""Tests for pipeline accuracy and reliability harnesses:
  - Courant number and time-step preflight audit
  - Automatic setFieldsDict rendering for VoF phase initialization
  - Multiphase phase-fraction alpha field validation
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ir import CaseIntent, PatchIntent, intent_to_spec
from knowledge import IntentValidator
from preflight import audit_case
from rendering import render


def test_courant_number_preflight_audit(tmp_path):
    """A transient case with unphysical high deltaT on high-velocity flow must be
    caught statically by Preflight Level A."""
    case_dir = tmp_path / "high_courant_case"
    (case_dir / "system").mkdir(parents=True)
    (case_dir / "0").mkdir(parents=True)

    # controlDict with deltaT = 1.0 (unphysical for 200 m/s flow)
    (case_dir / "system" / "controlDict").write_text("""
application foamRun;
solver fluid;
startTime 0;
endTime 10;
deltaT 1.0;
""")
    (case_dir / "system" / "fvSchemes").write_text("""
ddtSchemes
{
    default Euler;
}
""")
    (case_dir / "system" / "fvSolution").write_text("""
solvers
{
    p { solver GAMG; }
    U { solver PBiCGStab; }
}
PIMPLE { nOuterCorrectors 2; }
""")
    (case_dir / "0" / "U").write_text("""
dimensions [0 1 -1 0 0 0 0];
internalField uniform (0 0 200);
boundaryField
{
    inlet { type fixedValue; value uniform (0 0 200); }
}
""")

    findings = audit_case(case_dir)
    courant_findings = [f for f in findings if f.code == "high_courant_number"]
    assert len(courant_findings) == 1
    assert courant_findings[0].level == "fail"
    assert "Co > 10" in courant_findings[0].message


def test_vof_setfieldsdict_rendering():
    """VoF cases must automatically render a valid system/setFieldsDict."""
    intent = CaseIntent(
        case_type="incompressibleVoF",
        regime="laminar",
        geometry="box",
        box_size=(1.0, 1.0, 1.0),
        cells=(10, 10, 10),
        patches=[
            PatchIntent(name="walls", role="wall"),
            PatchIntent(name="atmosphere", role="outlet"),
        ],
    )
    spec = intent_to_spec(intent)
    case = render(spec)

    assert "setFieldsDict" in case.system
    set_fields = case.system["setFieldsDict"]
    assert "defaultFieldValues" in set_fields
    assert "volScalarFieldValue alpha.water 0" in set_fields
    assert "boxToCell" in set_fields


def test_intent_validator_catches_unphysical_alpha():
    """Phase fraction boundary values outside [0, 1] must be caught by IntentValidator."""
    validator = IntentValidator()
    intent = CaseIntent(
        case_type="incompressibleVoF",
        regime="laminar",
        geometry="box",
        box_size=(1.0, 1.0, 1.0),
        cells=(10, 10, 10),
        patches=[
            PatchIntent(
                name="inlet", role="inlet",
                bc_overrides={"alpha.water": {"type": "fixedValue", "value": "1.5"}}  # unphysical > 1
            ),
            PatchIntent(name="outlet", role="outlet"),
            PatchIntent(name="walls", role="wall"),
        ],
    )
    errors = validator.validate(intent)
    assert any("alpha.water" in str(e) and "physical bounds" in str(e) for e in errors), f"Errors: {errors}"


if __name__ == "__main__":
    test_courant_number_preflight_audit(Path("/tmp"))
    test_vof_setfieldsdict_rendering()
    test_intent_validator_catches_unphysical_alpha()
    print("All accuracy harness tests passed!")
