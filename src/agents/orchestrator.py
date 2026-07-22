"""Skeleton orchestrator wiring the three agents together.

Handoff order (open design choice - sanity-check with supervisor, see
docs/first-meeting-prep.md): Meshing Agent drafts geometry/patches FIRST,
then Physics Agent drafts fields using those patch names, because boundary
patch names are defined by the mesh and must be matched by the fields, not
the other way around. This mirrors how a human sets up a case by hand.

On a validation failure, the orchestrator routes the structured error to
whichever agent owns the implicated file (see FailureClass in
validation_agent.py) and asks it to redraft, rather than restarting the
whole case from scratch every retry.
"""

from __future__ import annotations

from pathlib import Path

from .case import CaseFiles
from .llm_client import LLMConfig, make_client
from .meshing_agent import MeshingAgent
from .physics_agent import PhysicsAgent
from .validation_agent import FailureClass, ValidationAgent

MAX_RETRIES = 3  # TODO(Phase II): tune this - literature review flags diminishing
# returns per retry round (temp/paper-research-agentic-cfd-llm/pdfs/2606.17514_...).


def generate_case(prompt: str, case_dir: Path, model: str | None = None) -> CaseFiles:
    config = LLMConfig(model=model) if model else LLMConfig()
    client = make_client(config)

    meshing = MeshingAgent(client, config.model)
    physics = PhysicsAgent(client, config.model)
    validator = ValidationAgent()

    mesh_files, patch_names = meshing.draft_block_mesh_dict(prompt)
    physics_files = physics.draft_fields(prompt, patch_names)

    case = CaseFiles(
        zero=physics_files.zero,
        constant=physics_files.constant,
        system=mesh_files.system,
    )
    # TODO(Phase II): system/controlDict, fvSchemes, fvSolution aren't owned by
    # either agent above yet - decide who writes these (probably Physics Agent,
    # since fvSchemes/fvSolution reference field names) and wire it in.

    for attempt in range(MAX_RETRIES):
        case.write(case_dir)
        result = validator.run_block_mesh(case_dir)
        if result.ok:
            check = validator.run_check_mesh(case_dir)
            if check.ok:
                return case
            result = check

        # TODO(Phase II): route by FailureClass instead of always going back
        # to the Meshing Agent - MESH_GENERATION/MESH_QUALITY belong there,
        # but DICTIONARY_SYNTAX in a 0/ or constant/ file belongs to the
        # Physics Agent instead. Needs a redraft(prior_content, errors) method
        # on each agent, not yet implemented below.
        for err in result.errors:
            if err.failure_class in (FailureClass.MESH_GENERATION, FailureClass.MESH_QUALITY):
                pass  # TODO: mesh_files, patch_names = meshing.redraft(...)
            else:
                pass  # TODO: physics_files = physics.redraft(...)

    raise RuntimeError(f"Failed to produce a valid case after {MAX_RETRIES} attempts")
