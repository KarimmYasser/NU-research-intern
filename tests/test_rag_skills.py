"""Unit tests for the local Knowledge Base RAG Retriever and Skill Engine."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agents.intent import CaseIntent, PatchIntent
from agents.mesh_io import MeshPatch
from agents.rag import KnowledgeRetriever
from agents.skills import CompressibleSwirlingFlowSkill, ExternalMeshPatchSkill, SkillRegistry


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


def test_compressible_swirling_skill():
    skill = CompressibleSwirlingFlowSkill()
    intent = CaseIntent(
        regime="turbulent",
        flow_type="incompressible",
        patches=[PatchIntent(name="left_symmetry", role="wall")],
    )

    prompt = "Ranque-Hilsch vortex tube 60 degree sector compressible flow"
    assert skill.is_applicable(prompt, intent)

    enriched = skill.enrich_intent(intent)
    assert enriched.flow_type == "compressible"
    assert enriched.solver_mode == "steadyState"
    assert enriched.p_ambient == 400000.0
    assert next(p for p in enriched.patches if p.name == "left_symmetry").role == "cyclic"


def test_external_mesh_patch_skill():
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

    enriched = skill.enrich_intent(intent, known_patches)
    roles = {p.name: p.role for p in enriched.patches}
    assert roles["cold_outlet"] == "outlet"
    assert roles["left_symmetry"] == "cyclic"
    assert roles["inlet"] == "inlet"


def test_skill_registry():
    registry = SkillRegistry()
    intent = CaseIntent(
        regime="turbulent",
        patches=[PatchIntent(name="left_symmetry", role="wall")],
    )
    prompt = "vortex tube compressible"
    known_patches = [MeshPatch(name="left_symmetry", type="cyclic", n_faces=100, start_face=0)]

    enriched = registry.apply_skills(intent, prompt, known_patches)
    assert enriched.flow_type == "compressible"
    assert enriched.patches[0].role == "cyclic"


if __name__ == "__main__":
    test_rag_search()
    test_rag_prompt_formatting()
    test_compressible_swirling_skill()
    test_external_mesh_patch_skill()
    test_skill_registry()
    print("All RAG & Skill tests passed!")
