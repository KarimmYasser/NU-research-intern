"""Structural contracts for the local OpenFOAM v13 knowledge registries."""
# ruff: noqa: I001

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ir.case_types import CASE_TYPES


KNOWLEDGE_DIR = Path(__file__).resolve().parents[1] / "src" / "knowledge"


def _load(filename: str) -> dict:
    return json.loads((KNOWLEDGE_DIR / filename).read_text(encoding="utf-8"))


def test_all_knowledge_registries_are_valid_json():
    for filename in (
        "bc_types.json",
        "boundary_conditions.json",
        "physical_envelopes.json",
        "solvers.json",
        "thermophysics.json",
        "turbulence_models.json",
    ):
        assert _load(filename), filename


def test_tutorial_index_has_unique_paths_and_bounded_source_excerpts():
    tutorials = _load("v13_tutorials.json")["tutorials"]
    paths = [tutorial["relative_path"] for tutorial in tutorials]
    assert paths and len(paths) == len(set(paths))
    for tutorial in tutorials:
        assert tutorial["solver"]
        assert isinstance(tutorial.get("file_tree"), dict)
        snippets = tutorial.get("snippets", {})
        for content in snippets.values():
            assert isinstance(content, str)
            assert len(content) <= 1_200
        patch_types = tutorial.get("patch_boundary_types", {})
        assert isinstance(patch_types, dict)
        for patch_name, field_types in patch_types.items():
            assert patch_name
            assert isinstance(field_types, dict)
            assert all(field_name and bc_type for field_name, bc_type in field_types.items())


def test_bc_registry_has_complete_and_unique_validator_metadata():
    entries = _load("bc_types.json")["bc_types"]
    names = [entry["type"] for entry in entries]
    assert len(names) == len(set(names))
    for entry in entries:
        assert set(entry) == {"type", "applicable_fields", "required_keys", "optional_keys", "description"}
        assert entry["applicable_fields"]
        assert isinstance(entry["required_keys"], list)
        assert isinstance(entry["optional_keys"], list)


def test_rag_solver_catalog_matches_supported_case_types():
    solvers = _load("solvers.json")["solvers"]
    indexed = {entry["name"] for entry in solvers}
    # fluid_buoyant deliberately maps to the same OpenFOAM module as fluid.
    expected = set(CASE_TYPES) - {"fluid_buoyant"}
    assert indexed == expected
    for entry in solvers:
        assert entry["required_fields"]
        assert len(entry["pressure_dimensions"]) == 7


def test_turbulence_models_and_thermo_have_unique_lookup_keys():
    turbulence = _load("turbulence_models.json")
    models = turbulence["ras_models"] + turbulence["les_models"]
    names = [model["name"] for model in models]
    assert len(names) == len(set(names))

    species = _load("thermophysics.json")["thermophysics"]
    keys = [item["species"] for item in species]
    assert len(keys) == len(set(keys))
    for item in species:
        mixture = item["mixture"]
        assert mixture["specie"]["molWeight"] > 0
        assert mixture["transport"]["mu"] > 0
        assert mixture["transport"]["Pr"] > 0


def test_physical_envelopes_are_ordered_and_have_unique_fields():
    envelopes = _load("physical_envelopes.json")["envelopes"]
    fields = [entry["field"] for entry in envelopes]
    assert len(fields) == len(set(fields))
    for entry in envelopes:
        assert entry["reject_below"] <= entry["min"] <= entry["max"] <= entry["reject_above"]