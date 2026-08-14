"""Tests for the deterministic physical-envelope constraint layer.

The pipeline must not trust the LLM's emitted numbers; every numeric field
(nu, p_ambient, temperature, velocities, pressures, box_size, cells,
surface_tension) is clamped into a curated plausible range, and only
unit-level outliers are surfaced as errors. No LLM, no Docker.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from ir.intent import CaseIntent, PatchIntent
from knowledge.envelopes import EnvelopeConstraint
from knowledge.intent_validator import IntentValidationError


def _intent(**kwargs) -> CaseIntent:
    defaults = {
        "regime": "laminar",
        "nu": 1e-05,
        "geometry": "box",
        "box_size": (0.1, 0.1, 0.1),
        "cells": (20, 20, 1),
        "patches": [
            PatchIntent(name="in", role="inlet", velocity=(1, 0, 0)),
            PatchIntent(name="out", role="outlet", pressure=0.0),
            PatchIntent(name="walls", role="wall"),
        ],
    }
    defaults.update(kwargs)
    return CaseIntent(**defaults)


# --- in-envelope values pass through untouched ----------------------------


def test_in_range_values_are_not_touched():
    intent = _intent(nu=1e-6, p_ambient=101325.0, temperature=300.0,
                     surface_tension=0.072)
    constrained, notes, errors = EnvelopeConstraint().apply(intent)
    assert not notes and not errors
    assert constrained.nu == 1e-6
    assert constrained.p_ambient == 101325.0
    assert constrained.temperature == 300.0
    assert constrained.surface_tension == 0.072


def test_box_size_and_cells_in_range_pass():
    intent = _intent(box_size=(0.5, 0.3, 0.2), cells=(40, 30, 5))
    constrained, notes, errors = EnvelopeConstraint().apply(intent)
    assert not notes and not errors
    assert constrained.box_size == (0.5, 0.3, 0.2)
    assert constrained.cells == (40, 30, 5)


# --- modest outliers are clamped deterministically ------------------------


def test_nu_clamped_to_worst_plausible_bound():
    intent = _intent(nu=0.5)  # cP-style slip-up
    constrained, notes, errors = EnvelopeConstraint().apply(intent)
    assert not errors
    assert constrained.nu == 1e-1
    assert any("nu" in n and "clamped" in n for n in notes)


def test_low_nu_clamped_up():
    intent = _intent(nu=1e-8)
    constrained, notes, errors = EnvelopeConstraint().apply(intent)
    assert not errors
    assert constrained.nu == pytest.approx(1e-7)
    assert notes


def test_temperature_clamped_high():
    intent = _intent(temperature=5000.0)
    constrained, notes, errors = EnvelopeConstraint().apply(intent)
    assert not errors
    assert constrained.temperature == 4000.0
    assert notes


def test_patch_velocity_rescaled_preserving_direction():
    intent = _intent(patches=[
        PatchIntent(name="in", role="inlet", velocity=(1000, 1000, 0)),
    ])
    constrained, notes, errors = EnvelopeConstraint().apply(intent)
    assert not errors
    vx, vy, _vz = constrained.patches[0].velocity
    assert pytest.approx((vx * vx + vy * vy) ** 0.5, abs=1e-6) == 1e3
    assert pytest.approx(vx / vy) == 1.0  # direction untouched
    assert notes


def test_zero_velocity_not_clamped():
    intent = _intent(patches=[
        PatchIntent(name="in", role="inlet", velocity=(0, 0, 0)),
    ])
    constrained, notes, errors = EnvelopeConstraint().apply(intent)
    assert not notes and not errors
    assert constrained.patches[0].velocity == (0, 0, 0)


def test_single_cells_axis_clamped():
    intent = _intent(cells=(20, 1000, 1))
    constrained, notes, errors = EnvelopeConstraint().apply(intent)
    assert not errors
    assert constrained.cells == (20, 500, 1)
    assert any("cells[1]" in n for n in notes)


# --- unit-level outliers are errors, not silent clamps --------------------


def test_absurd_nu_is_rejected():
    intent = _intent(nu=1e5)
    constrained, notes, errors = EnvelopeConstraint().apply(intent)
    assert notes == []
    assert len(errors) == 1
    assert errors[0].field == "nu"
    assert "[1e-07, 0.1]" in errors[0].message
    assert constrained.nu == 1e5  # unchanged: pipeline must reject, not trust


def test_patch_pressure_outlier_rejected():
    intent = _intent(patches=[
        PatchIntent(name="out", role="outlet", pressure=1e12),
    ])
    _, notes, errors = EnvelopeConstraint().apply(intent)
    assert not notes
    assert any(e.field == "patches[0](out).pressure" for e in errors)


def test_absurd_velocity_rejected():
    intent = _intent(patches=[
        PatchIntent(name="in", role="inlet", velocity=(1e6, 0, 0)),
    ])
    _, notes, errors = EnvelopeConstraint().apply(intent)
    assert not notes
    assert any(e.field == "patches[0](in).velocity" for e in errors)


# --- errors carry the IntentValidationError shape ------------------------


def test_reject_errors_are_intent_validation_error():
    intent = _intent(nu=1e5)
    _, _, errors = EnvelopeConstraint().apply(intent)
    assert all(isinstance(e, IntentValidationError) for e in errors)


# --- species-derived nu envelope (determined, not hard-coded) -------------


def test_air_nu_envelope_derived_from_thermo_data():
    """nu = mu/rho with ideal-gas rho; air at 1atm/300K must give ~1.58e-5."""
    intent = _intent(fluid_species="air", nu=1e-5, p_ambient=101325.0,
                     temperature=300.0)
    constrained, notes, errors = EnvelopeConstraint().apply(intent)
    assert not errors
    assert constrained.nu == 1e-5  # air's true value sits inside the derived band
    assert not notes


def test_air_nu_slipup_rejected_outside_derived_band():
    """nu=0.5 is within the static clamp band but ~4 decades above air's true
    nu — the derived envelope must reject it, not silently clamp."""
    intent = _intent(fluid_species="air", nu=0.5, p_ambient=101325.0,
                     temperature=300.0)
    constrained, notes, errors = EnvelopeConstraint().apply(intent)
    assert not notes
    assert len(errors) == 1
    assert "air" in errors[0].message or "mu=" in errors[0].message
    assert constrained.nu == 0.5  # untouched: pipeline must reject, not trust


def test_unknown_species_falls_back_to_static_envelope():
    intent = _intent(fluid_species="quarkSoup", nu=0.5)
    constrained, notes, errors = EnvelopeConstraint().apply(intent)
    assert not errors
    assert constrained.nu == 1e-1  # static clamp band, species unknown
    assert notes


def test_nu_envelope_honors_case_temperature():
    """Hot air is less dense, so kin. viscosity grows: the derived band must
    shift upward with T, keeping the same nominal nu inside the band."""
    cold = _intent(fluid_species="air", nu=1e-5, p_ambient=101325.0, temperature=200.0)
    hot = _intent(fluid_species="air", nu=1e-5, p_ambient=101325.0, temperature=300.0)
    c = EnvelopeConstraint()
    _, n1, e1 = c.apply(cold)
    _, n2, e2 = c.apply(hot)
    assert not e1 and not e2
    assert not n1 and not n2
    env_hot = c._envelope_for("nu", hot)
    env_cold = c._envelope_for("nu", cold)
    assert env_hot is not None and env_cold is not None
    assert env_hot.min > env_cold.min  # hotter -> less dense -> larger nu range