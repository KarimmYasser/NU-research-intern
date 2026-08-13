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


# ─── Chunkless RAG: case-graph retrieval ─────────────────────────────────────

def test_needs_deep_path_heuristic():
    retriever = KnowledgeRetriever()
    # Physics-specific vocabulary fires the deep path.
    assert retriever.needs_deep_path("Compressible vortex tube with swirl at 50 m/s")
    assert retriever.needs_deep_path("Multiphase droplet spray combustion")
    # Plain single-phase internal flow stays on the cheap fast path.
    assert not retriever.needs_deep_path("Lid-driven cavity flow in a square box")
    assert not retriever.needs_deep_path("Flow past a cylinder in a channel")


def test_retrieve_case_graph_returns_whole_records():
    retriever = KnowledgeRetriever()
    graph = retriever.retrieve_case_graph("compressible vortex tube shock tube")
    assert graph["solver"]["name"] == "fluid"
    assert graph["cases"], "expected matched tutorial cases"
    for c in graph["cases"]:
        # Whole records: file tree (incl. optional system/ files) + field set.
        assert "file_tree" in c, "case record must carry its file tree"
        assert set(c["file_tree"]).issubset({"0", "constant", "system"})
        assert "fields" in c
    # The ground block surfaces the optional files the reference case ships.
    ground = graph["ground"]
    assert "Reference case" in ground
    assert "system/" in ground


def test_deep_path_surfaces_optional_files():
    """A shock-tube-class prompt must show the agent the auxiliary files a real
    v13 reference case ships (functions / fvConstraints / setFieldsDict...), so
    the LLM knows to emit them via extra_files instead of guessing."""
    retriever = KnowledgeRetriever()
    ctx = retriever.format_prompt_context("compressible vortex tube")
    assert "Reference Case" in ctx  # deep path fired
    assert "(optional:" in ctx  # optional-files annotation rendered
    graph = retriever.retrieve_case_graph("vortex tube")
    system_files = {f for c in graph["cases"] for f in (c.get("file_tree") or {}).get("system", [])}
    assert "functions" in system_files or "fvConstraints" in system_files or \
        "setFieldsDict" in system_files
    # A flat query stays off the deep path entirely.
    plain = retriever.format_prompt_context("lid driven cavity")
    assert "Reference Case" not in plain


def test_family_hierarchy_reasons_by_description_not_words():
    """The whole point of the taxonomy: a prompt with NO literal tutorial name
    (e.g. vortex tube) must resolve to the compressible-internal family via its
    description, and the primary family's members must rank above keyword ties."""
    retriever = KnowledgeRetriever()
    graph = retriever.retrieve_case_graph("compressible vortex tube tangential nozzles")
    assert graph["families"], "a semantic family must be matched"
    assert graph["families"][0]["id"] == "flow_compressible_internal"
    # Member cases are from the matched family, and carry the family annotation.
    assert all(c.get("family_id") in {"flow_compressible_internal",
                                      "flow_compressible_external"} for c in graph["cases"])
    context = retriever.format_prompt_context("compressible vortex tube")
    assert "Matched case family" in context


def test_family_solver_from_matching_family():
    """'mach 2 airfoil' names no solver, but the compressible-external family
    implies a compressible solver and its member aerofoil case, not the keyword-
    default incompressible solver with irrelevant internal-flow cases."""
    retriever = KnowledgeRetriever()
    graph = retriever.retrieve_case_graph("airfoil at mach 2")
    assert graph["solver"]["flow_type"] == "compressible"
    assert graph["families"][0]["id"] == "flow_compressible_external"
    assert any("aerofoil" in c.get("relative_path", "") or "wedge" in c.get("relative_path", "")
               for c in graph["cases"])


def test_family_disambiguation_multiphase():
    """'bubble column' must land on Euler bubbly flow (bubbleColumn case), not a
    dense-particle case sharing the word 'column'."""
    retriever = KnowledgeRetriever()
    graph = retriever.retrieve_case_graph("bubble column")
    assert graph["families"][0]["id"] == "multiphase_euler"
    assert any("bubbleColumn" in c.get("relative_path", "") for c in graph["cases"])


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
