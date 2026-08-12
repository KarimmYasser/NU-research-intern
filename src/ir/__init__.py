"""Intermediate Representation (IR) sub-package."""

from .intent import CaseIntent, PatchIntent, PatchRole, intent_to_spec
from .spec import (
    BoxFace,
    BoxGeometry,
    CaseSpec,
    ExternalMesh,
    Field,
    FieldBC,
    FluidProperties,
    Geometry,
    NumericsPreset,
    Patch,
    PatchType,
    ThermoProperties,
    TimeControl,
    TurbulenceRAS,
)

__all__ = [
    "CaseSpec",
    "CaseIntent",
    "PatchIntent",
    "PatchRole",
    "intent_to_spec",
    "BoxFace",
    "PatchType",
    "BoxGeometry",
    "ExternalMesh",
    "Geometry",
    "FieldBC",
    "Patch",
    "Field",
    "TurbulenceRAS",
    "ThermoProperties",
    "FluidProperties",
    "TimeControl",
    "NumericsPreset",
]
