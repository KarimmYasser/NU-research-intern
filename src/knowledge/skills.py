"""Agent Skills Engine for OpenFOAM Case Generation.

Modular skills that inspect problem intents and provided mesh patches to automatically
apply physics knowledge, boundary condition mappings, and solver configurations.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, List

if TYPE_CHECKING:
    from ir.intent import CaseIntent, PatchRole
    from mesh.mesh_io import MeshPatch


class BaseSkill:
    """Base class for agentic skills."""

    name: str = "base_skill"
    description: str = "Base Skill"

    def is_applicable(self, prompt: str, intent: CaseIntent, known_patches: List[MeshPatch] | None = None) -> bool:
        return False

    def enrich_intent(self, intent: CaseIntent, prompt: str = "", known_patches: List[MeshPatch] | None = None) -> CaseIntent:
        return intent


class ExternalMeshPatchSkill(BaseSkill):
    """Skill to auto-map provided mesh patch names to physics roles when grounding.

    Data-driven: the mesh's own patch *type* is the authoritative signal (a
    symmetryPlane patch is a symmetry boundary, a cyclic patch is periodic),
    with the name as a fallback heuristic. No case-specific name assumptions.
    """

    name = "external_mesh_patch_mapper"
    description = "Automatically maps boundary patch names from external polyMesh to physics roles."

    def is_applicable(self, prompt: str, intent: CaseIntent, known_patches: List[MeshPatch] | None = None) -> bool:
        return bool(known_patches)

    def enrich_intent(self, intent: CaseIntent, prompt: str = "", known_patches: List[MeshPatch] | None = None) -> CaseIntent:
        if not known_patches:
            return intent

        patch_roles: dict[str, PatchRole] = {}
        for p in known_patches:
            patch_type = p.type.lower()
            name_lower = p.name.lower()
            if "cyclic" in patch_type or "cyclic" in name_lower:
                patch_roles[p.name] = "cyclic"
            elif "symmetry" in patch_type or "symmetry" in name_lower:
                patch_roles[p.name] = "symmetry"
            elif "inlet" in patch_type or "inlet" in name_lower:
                patch_roles[p.name] = "inlet"
            elif "outlet" in patch_type or "outlet" in name_lower:
                patch_roles[p.name] = "outlet"
            elif "wall" in patch_type or "wall" in name_lower:
                patch_roles[p.name] = "wall"

        for patch in intent.patches:
            if patch.name in patch_roles:
                patch.role = patch_roles[patch.name]

        return intent


# Units -> Pa. `(m)?(bar)` needs the mbar case before the bare `bar` branch.
_PRESSURE_RE = re.compile(
    r"(\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\s*(mbar|MPa|kPa|bar|atm|psi|Pa)\b", re.IGNORECASE
)
_PRESSURE_TO_PA = {
    "pa": 1.0,
    "kpa": 1e3,
    "mpa": 1e6,
    "mbar": 100.0,
    "bar": 1e5,
    "atm": 101325.0,
    "psi": 6894.76,
}


class PromptPressureSkill(BaseSkill):
    """Grounded skill: read an explicit operating/ambient pressure out of the
    prompt text (e.g. '4 bar', '5 atm') when the LLM left p_ambient at its
    default. Purely data-driven from the prompt — no per-case constants."""

    name = "prompt_ambient_pressure"
    description = "Extracts an explicit operating/ambient pressure stated in the prompt (e.g. '4 bar', '5 atm') when the intent did not specify one."

    def is_applicable(self, prompt: str, intent: CaseIntent, known_patches: List[MeshPatch] | None = None) -> bool:
        return (
            intent.flow_type == "compressible"
            and intent.p_ambient == 100000.0  # the LLM didn't pick a value
            and _PRESSURE_RE.search(prompt) is not None
        )

    def enrich_intent(self, intent: CaseIntent, prompt: str = "", known_patches: List[MeshPatch] | None = None) -> CaseIntent:
        m = _PRESSURE_RE.search(prompt)
        if m is None:
            return intent
        value = float(m.group(1)) * _PRESSURE_TO_PA[m.group(2).lower()]
        intent.p_ambient = value
        return intent


class SkillRegistry:
    """Registry managing available agent skills."""

    def __init__(self):
        self.skills: List[BaseSkill] = [
            ExternalMeshPatchSkill(),
            PromptPressureSkill(),
        ]

    def register(self, skill: BaseSkill) -> None:
        self.skills.append(skill)

    def apply_skills(self, intent: CaseIntent, prompt: str, known_patches: List[MeshPatch] | None = None) -> CaseIntent:
        """Apply all matching skills to refine the extracted CaseIntent."""
        for skill in self.skills:
            if skill.is_applicable(prompt, intent, known_patches):
                intent = skill.enrich_intent(intent, prompt=prompt, known_patches=known_patches)
        return intent
