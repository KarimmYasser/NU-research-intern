"""Deterministic physical-envelope constraint layer for CaseIntent numbers.

Implements the "range of a number instead of model experience" idea: every
numeric field the LLM emits (nu, p_ambient, temperature, velocities, pressures,
box_size, cells, surface_tension) is bounded by a curated, physically-plausible
envelope (physical_envelopes.json). Running immediately after Pydantic parsing
and before the IntentValidator:

- values already inside the envelope are passed through untouched;
- values that drift modestly outside get *clamped* to the nearest bound
  (direction-preserving for velocity vectors) and a note is recorded — the
  pipeline keeps running deterministically instead of trusting a single
  low-quality model guess;
- values so far out that they can only be unit/dimension slip-ups (e.g.
  nu=1e5) are returned as errors, so the pipeline can feed the *valid range*
  back into the LLM retry loop, mirroring IntentValidator feedback.

Envelope provenance: when a `fluid_species` is named, the `nu` envelope is
**derived, not hard-coded** — the reference is computed from the species'
dynamic viscosity and molecular weight (thermophysics.json) via the ideal-gas
law at the case's p_ambient/temperature, and the envelope is a decade band
around it. Fields that are case parameters rather than fluid properties
(temperature, pressures, box_size, cells, velocity) always use the curated
static ranges, as does nu when no species is named.

No LLM call, no Docker: pure registry lookup + arithmetic.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ir.intent import CaseIntent

from knowledge.intent_validator import IntentValidationError

_KNOWLEDGE_DIR = Path(__file__).resolve().parent
_R_IDEAL_GAS = 8.314  # J/(mol·K)


def _load_json(filename: str) -> dict[str, Any]:
    path = _KNOWLEDGE_DIR / filename
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


@dataclass
class Envelope:
    """A numeric field's plausible range plus the clamp/reject band around it."""
    field: str
    units: str
    min: float
    max: float
    reject_below: float
    reject_above: float
    rationale: str

    def describe(self) -> str:
        return f"[{self.min:g}, {self.max:g}] {self.units}"


class EnvelopeConstraint:
    """Clamp CaseIntent numbers into physically-plausible envelopes."""

    def __init__(self, knowledge_dir: Path | None = None):
        kdir = knowledge_dir or _KNOWLEDGE_DIR
        raw = _load_json("physical_envelopes.json").get("envelopes", [])
        self._envelopes: dict[str, Envelope] = {
            e["field"]: Envelope(**e) for e in raw
        }
        # species -> mixture block, for deriving the nu envelope from thermo data.
        self._species_thermo: dict[str, dict[str, Any]] = {}
        for item in _load_json("thermophysics.json").get("thermophysics", []):
            key = (item.get("species") or item.get("model_name") or "").lower().strip()
            if key:
                self._species_thermo[key] = item

    # ------------------------------------------------------------------
    # Envelope provenance
    # ------------------------------------------------------------------

    def _nu_envelope(self, intent: CaseIntent) -> Envelope | None:
        """Derive a species-grounded nu envelope from thermo data, or None.

        nu = mu / rho, with ideal-gas rho = p·Mw/(R·T) at the case's operating
        conditions. The envelope is a decade band around that reference so the
        model still has room to choose, but a value that "sounds like" the wrong
        fluid (e.g. 0.5 for air) now has no way through.
        """
        item = self._species_thermo.get((intent.fluid_species or "").lower().strip())
        if item is None:
            return None
        mix = item.get("mixture", {})
        mu = mix.get("transport", {}).get("mu")
        mol_weight = mix.get("specie", {}).get("molWeight")
        if not mu or not mol_weight:
            return None
        p = intent.p_ambient or 100000.0
        t = intent.temperature or 300.0
        if p <= 0 or t <= 0:
            return None
        rho = p * (mol_weight * 1e-3) / (_R_IDEAL_GAS * t)
        if rho <= 0:
            return None
        nu_ref = mu / rho
        return Envelope(
            field="nu",
            units="m^2/s",
            min=nu_ref / 10.0,
            max=nu_ref * 10.0,
            reject_below=nu_ref / 100.0,
            reject_above=nu_ref * 100.0,
            rationale=(
                f"derived from {intent.fluid_species} thermo (mu={mu:g} Pa·s, "
                f"Mw={mol_weight:g} g/mol) via ideal-gas rho at p={p:g} Pa, "
                f"T={t:g} K"
            ),
        )

    def _envelope_for(self, field: str, intent: CaseIntent) -> Envelope | None:
        if field == "nu":
            derived = self._nu_envelope(intent)
            if derived is not None:
                return derived
        return self._envelopes.get(field)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def apply(self, intent: CaseIntent) -> tuple[CaseIntent, list[str], list[IntentValidationError]]:
        """Constrain *intent*'s numbers. Returns (constrained_intent, notes, errors).

        Notes describe deterministic clamps already applied; errors describe
        values so far outside the envelope that only a units/dimension error can
        explain them (caller should re-prompt the LLM with these).
        """
        notes: list[str] = []
        errors: list[IntentValidationError] = []

        nu = getattr(intent, "nu", None)
        if isinstance(nu, (int, float)):
            v, n, e = self._clamp_scalar("nu", nu, envelope=self._envelope_for("nu", intent))
            if n or e:
                intent = intent.model_copy(update={"nu": v})
                notes += n; errors += e

        for scalar_field in ("p_ambient", "temperature", "surface_tension"):
            val = getattr(intent, scalar_field, None)
            if not isinstance(val, (int, float)):
                continue
            v, n, e = self._clamp_scalar(scalar_field, val)
            if n or e:
                intent = intent.model_copy(update={scalar_field: v})
                notes += n; errors += e

        box_size = getattr(intent, "box_size", None)
        if box_size:
            v, n, e = self._clamp_tuple("box_size", box_size)
            if n or e:
                intent = intent.model_copy(update={"box_size": tuple(v)})
                notes += n; errors += e

        cells = getattr(intent, "cells", None)
        if cells:
            v, n, e = self._clamp_int_tuple("cells", cells)
            if n or e:
                intent = intent.model_copy(update={"cells": tuple(v)})
                notes += n; errors += e

        # Per-patch numeric fields (velocity magnitude, pressure, temperature).
        new_patches: list[Any] = []
        for i, patch in enumerate(intent.patches):
            updates: dict[str, Any] = {}
            prefix = f"patches[{i}]({patch.name})"

            vel = getattr(patch, "velocity", None)
            if vel:
                v, n, e = self._clamp_vector("velocity", vel, prefix)
                if n or e:
                    updates["velocity"] = v
                    notes += n; errors += e

            for scalar_field in ("pressure", "temperature"):
                val = getattr(patch, scalar_field, None)
                if not isinstance(val, (int, float)):
                    continue
                v, n, e = self._clamp_scalar(scalar_field, val, prefix)
                if n or e:
                    updates[scalar_field] = v
                    notes += n; errors += e

            new_patches.append(patch if not updates else patch.model_copy(update=updates))

        if new_patches:
            intent = intent.model_copy(update={"patches": new_patches})

        return intent, notes, errors

    # ------------------------------------------------------------------
    # Scalar / vector clamp helpers
    # ------------------------------------------------------------------

    def _clamp_scalar(self, field: str, value: float, prefix: str = "",
                      envelope: Envelope | None = None) -> tuple[float, list[str], list[IntentValidationError]]:
        env = envelope if envelope is not None else self._envelopes.get(field)
        if env is None:
            return value, [], []
        label = f"{prefix}.{field}" if prefix else field

        if value < env.reject_below or value > env.reject_above:
            return value, [], [IntentValidationError(
                field=label,
                message=(
                    f"{field}={value:g} {env.units} is physically implausible; "
                    f"valid range is {env.describe()} ({env.rationale}). "
                    "Re-check the units in the prompt and return a corrected value."
                )
            )]
        if env.min <= value <= env.max:
            return value, [], []
        bound = env.min if value < env.min else env.max
        return bound, [self._clamp_note(label, field, value, bound, env)], []

    def _clamp_vector(self, field: str, value: tuple[float, float, float], prefix: str = "") -> tuple[tuple[float, float, float], list[str], list[IntentValidationError]]:
        env = self._envelopes.get(field)
        if env is None:
            return value, [], []
        label = f"{prefix}.{field}"
        mag = math.sqrt(sum(c * c for c in value))
        if mag == 0.0:
            return value, [], []  # zero velocity is fine
        if mag < env.reject_below or mag > env.reject_above:
            return value, [], [IntentValidationError(
                field=label,
                message=(
                    f"{field} magnitude {mag:g} m/s is physically implausible; "
                    f"valid range is {env.describe()} ({env.rationale}). "
                    "Re-check the units in the prompt and return a corrected value."
                )
            )]
        if env.min <= mag <= env.max:
            return value, [], []
        bound = env.min if mag < env.min else env.max
        scale = bound / mag
        clamped = tuple(round(c * scale, 12) for c in value)
        return clamped, [self._clamp_note(label, field, mag, bound, env)], []

    def _clamp_tuple(self, field: str, values: tuple[float, float, float]) -> tuple[list[float], list[str], list[IntentValidationError]]:
        env = self._envelopes.get(field)
        if env is None:
            return list(values), [], []
        notes: list[str] = []
        errors: list[IntentValidationError] = []
        clamped: list[float] = []
        for axis, v in enumerate(values):
            if v < env.reject_below or v > env.reject_above:
                errors.append(IntentValidationError(
                    field=f"{field}[{axis}]",
                    message=(
                        f"{field}[{axis}]={v:g} {env.units} is physically implausible; "
                        f"valid range is {env.describe()}. Re-check units."
                    )
                ))
                clamped.append(v)
            elif env.min <= v <= env.max:
                clamped.append(v)
            else:
                bound = env.min if v < env.min else env.max
                clamped.append(bound)
                notes.append(self._clamp_note(f"{field}[{axis}]", field, v, bound, env))
        return clamped, notes, errors

    def _clamp_int_tuple(self, field: str, values: tuple[int, int, int]) -> tuple[list[int], list[str], list[IntentValidationError]]:
        env = self._envelopes.get(field)
        if env is None:
            return list(values), [], []
        notes: list[str] = []
        errors: list[IntentValidationError] = []
        clamped: list[int] = []
        for axis, v in enumerate(values):
            v = int(v)
            if v < env.reject_below or v > env.reject_above:
                errors.append(IntentValidationError(
                    field=f"{field}[{axis}]",
                    message=(
                        f"{field}[{axis}]={v} cells is physically implausible; "
                        f"valid range is {env.describe()}. Re-check the prompt."
                    )
                ))
                clamped.append(v)
            elif env.min <= v <= env.max:
                clamped.append(v)
            else:
                bound = int(env.min if v < env.min else env.max)
                clamped.append(bound)
                notes.append(self._clamp_note(f"{field}[{axis}]", field, v, bound, env))
        return clamped, notes, errors

    @staticmethod
    def _clamp_note(label: str, field: str, value: float, bound: float, env: Envelope) -> str:
        return (
            f"{label}: {value:g} {env.units} outside physical range "
            f"{env.describe()}; clamped deterministically to {bound:g}."
        )
