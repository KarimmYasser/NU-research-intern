"""Agent Skills Engine for OpenFOAM Case Generation.

Modular skills that inspect problem intents and provided mesh patches to automatically
apply physics knowledge, boundary condition mappings, and solver configurations.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, List

if TYPE_CHECKING:
    from .intent import CaseIntent, PatchIntent
    from .mesh_io import MeshPatch


class BaseSkill:
    """Base class for agentic skills."""

    name: str = "base_skill"
    description: str = "Base Skill"

    def is_applicable(self, prompt: str, intent: CaseIntent, known_patches: List[MeshPatch] | None = None) -> bool:
        return False

    def enrich_intent(self, intent: CaseIntent, known_patches: List[MeshPatch] | None = None) -> CaseIntent:
        return intent


class ExternalMeshPatchSkill(BaseSkill):
    """Skill to auto-map provided mesh patch names to physics roles when grounding."""

    name = "external_mesh_patch_mapper"
    description = "Automatically maps boundary patch names from external polyMesh to physics roles."

    def is_applicable(self, prompt: str, intent: CaseIntent, known_patches: List[MeshPatch] | None = None) -> bool:
        return bool(known_patches)

    def enrich_intent(self, intent: CaseIntent, known_patches: List[MeshPatch] | None = None) -> CaseIntent:
        if not known_patches:
            return intent

        patch_roles: dict[str, str] = {}
        for p in known_patches:
            name_lower = p.name.lower()
            if p.type == "cyclic" or "symmetry" in name_lower or "cyclic" in name_lower:
                patch_roles[p.name] = "cyclic"
            elif "inlet" in name_lower:
                patch_roles[p.name] = "inlet"
            elif "outlet" in name_lower:
                patch_roles[p.name] = "outlet"
            elif p.type == "wall" or "wall" in name_lower:
                patch_roles[p.name] = "wall"

        for patch in intent.patches:
            if patch.name in patch_roles:
                patch.role = patch_roles[patch.name]

        return intent


class CompressibleSwirlingFlowSkill(BaseSkill):
    """Skill for compressible, turbulent, swirling sector cases (e.g. Vortex Tube)."""

    name = "compressible_swirling_flow"
    description = "Enforces compressible thermophysics, steady-state SIMPLE solver, and cyclic BCs for swirling sector flows."

    def is_applicable(self, prompt: str, intent: CaseIntent, known_patches: List[MeshPatch] | None = None) -> bool:
        keywords = ["vortex", "swirl", "tangential", "sector", "compressible", "ranque", "hilsch"]
        text = prompt.lower()
        return any(kw in text for kw in keywords) or intent.flow_type == "compressible"

    def enrich_intent(self, intent: CaseIntent, known_patches: List[MeshPatch] | None = None) -> CaseIntent:
        intent.flow_type = "compressible"
        intent.solver_mode = "steadyState"
        if intent.p_ambient == 100000.0:
            intent.p_ambient = 400000.0  # default to operating pressure for vortex tube if unsupplied

        # Ensure rotational symmetry boundaries use cyclic role
        for p in intent.patches:
            if "symmetry" in p.name.lower() or "cyclic" in p.name.lower():
                p.role = "cyclic"

        return intent


class SkillRegistry:
    """Registry managing available agent skills."""

    def __init__(self):
        self.skills: List[BaseSkill] = [
            ExternalMeshPatchSkill(),
            CompressibleSwirlingFlowSkill(),
        ]

    def register(self, skill: BaseSkill) -> None:
        self.skills.append(skill)

    def apply_skills(self, intent: CaseIntent, prompt: str, known_patches: List[MeshPatch] | None = None) -> CaseIntent:
        """Apply all matching skills to refine the extracted CaseIntent."""
        for skill in self.skills:
            if skill.is_applicable(prompt, intent, known_patches):
                intent = skill.enrich_intent(intent, known_patches)
        return intent
