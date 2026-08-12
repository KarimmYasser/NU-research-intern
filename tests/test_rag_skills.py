"""Unit tests for the local Knowledge Base RAG Retriever and Skill Engine."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ir import CaseIntent, PatchIntent
from knowledge import ExternalMeshPatchSkill, KnowledgeRetriever, PromptPressureSkill, SkillRegistry
from mesh import MeshPatch


def test_rag_search():
    retriever = KnowledgeRetriever()

    # Search for compressible swirling vortex tube query
    results = retriever.search("compressible swirling vortex tube air")
    assert any(s["name"] == "fluid" for s in results["solvers"])
    assert any(b["role"] == "cyclic" for b in results["boundary_conditions"])

    # Search for cavity query
    cavity_results = retriever.search("incompressible lid driven cavity")
    assert any(s["name"] == "incompressibleFluid" for s in cavity_results["solvers"])


def test_rag_prompt_formatting():
    retriever = KnowledgeRetriever()
    context = retriever.format_prompt_context("compressible vortex tube")
    assert "OpenFOAM v13 Domain Knowledge" in context
    assert "fluid" in context
    assert "cyclic" in context


def test_external_mesh_patch_skill_cyclic_stays_cyclic():
    """A mesh patch whose *type* is cyclic is a periodic boundary - even when
    its name says 'symmetry'. Type is authoritative over the name heuristic."""
    skill = ExternalMeshPatchSkill()
    intent = CaseIntent(
        regime="turbulent",
        patches=[
            PatchIntent(name="cold_outlet", role="wall"),
            PatchIntent(name="left_symmetry", role="wall"),
            PatchIntent(name="inlet", role="wall"),
        ],
    )
    known_patches = [
        MeshPatch(name="cold_outlet", type="patch", n_faces=10, start_face=0),
        MeshPatch(name="left_symmetry", type="cyclic", n_faces=10, start_face=10),
        MeshPatch(name="inlet", type="patch", n_faces=10, start_face=20),
    ]

    enriched = skill.enrich_intent(intent, known_patches=known_patches)
    roles = {p.name: p.role for p in enriched.patches}
    assert roles["cold_outlet"] == "outlet"
    assert roles["left_symmetry"] == "cyclic"
    assert roles["inlet"] == "inlet"


def test_external_mesh_patch_skill_symmetry_plane():
    """A mesh patch typed symmetryPlane maps to the symmetry role, not cyclic."""
    skill = ExternalMeshPatchSkill()
    intent = CaseIntent(patches=[PatchIntent(name="midplane", role="wall")])
    known_patches = [
        MeshPatch(name="midplane", type="symmetryPlane", n_faces=50, start_face=0),
    ]
    enriched = skill.enrich_intent(intent, known_patches=known_patches)
    assert enriched.patches[0].role == "symmetry"


def test_prompt_pressure_skill():
    skill = PromptPressureSkill()
    intent = CaseIntent(
        regime="turbulent",
        flow_type="compressible",
        patches=[PatchIntent(name="inlet", role="inlet")],
    )

    # '4 bar' should be picked up when the LLM left p_ambient at its default.
    assert skill.is_applicable("compressible flow at 4 bar operating pressure", intent)
    enriched = skill.enrich_intent(intent, prompt="compressible flow at 4 bar operating pressure")
    assert enriched.p_ambient == 400000.0

    # An explicitly-LLM-chosen pressure is respected (not the default -> skill off).
    chosen = CaseIntent(
        regime="turbulent", flow_type="compressible", p_ambient=250000.0,
        patches=[PatchIntent(name="inlet", role="inlet")],
    )
    assert not skill.is_applicable("operating pressure 4 bar", chosen)

    # Unit conversions.
    for text, expected in [("5 atm", 101325.0 * 5), ("2.5 MPa", 2.5e6), ("300 kPa", 3e5)]:
        intent = CaseIntent(
            regime="turbulent", flow_type="compressible",
            patches=[PatchIntent(name="inlet", role="inlet")],
        )
        enriched = skill.enrich_intent(intent, prompt=f"inlet at {text}")
        assert enriched.p_ambient == expected, f"{text} -> {enriched.p_ambient}"


def test_prompt_pressure_skill_inapplicable_for_incompressible():
    skill = PromptPressureSkill()
    intent = CaseIntent(
        regime="turbulent", flow_type="incompressible",
        patches=[PatchIntent(name="inlet", role="inlet")],
    )
    assert not skill.is_applicable("operating pressure 4 bar", intent)


def test_skill_registry():
    registry = SkillRegistry()
    intent = CaseIntent(
        regime="turbulent",
        flow_type="compressible",
        patches=[PatchIntent(name="left_symmetry", role="wall")],
    )
    prompt = "compressible vortex tube at 4 bar operating pressure"
    known_patches = [MeshPatch(name="left_symmetry", type="cyclic", n_faces=100, start_face=0)]

    enriched = registry.apply_skills(intent, prompt, known_patches)
    # Role mapping corrected from the mesh type; pressure read from the prompt.
    assert enriched.patches[0].role == "cyclic"
    assert enriched.p_ambient == 400000.0
    # The registry never overrides physics decisions wholesale (no forced
    # compressible/steadyState), so an incompressible intent stays incompressible.
    assert enriched.flow_type == "compressible"


if __name__ == "__main__":
    test_rag_search()
    test_rag_prompt_formatting()
    test_external_mesh_patch_skill_cyclic_stays_cyclic()
    test_external_mesh_patch_skill_symmetry_plane()
    test_prompt_pressure_skill()
    test_prompt_pressure_skill_inapplicable_for_incompressible()
    test_skill_registry()
    print("All RAG & Skill tests passed!")
