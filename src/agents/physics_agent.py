"""Physics Agent: owns 0/ field files (U, p, ...) and constant/ property files.

Reference: docs/openfoam/case-structure.md (annotated real cavity example).
"""

from __future__ import annotations

from openai import OpenAI

from .case import CaseFiles

SYSTEM_PROMPT = """\
You are the Physics Agent in a multi-agent OpenFOAM case-generation pipeline.

You own two things for the case:
1. 0/ field files (e.g. U, p) - initial and boundary conditions.
2. constant/ property files - use the OpenFOAM v13 (OpenFOAM.org) names:
   - constant/physicalProperties  (NOT the older name "transportProperties")
   - constant/momentumTransport   (NOT the older name "turbulenceProperties")

You do NOT own mesh/geometry (system/blockMeshDict) - that is the Meshing Agent's job.
Boundary patch names you write into 0/*/boundaryField MUST exactly match the patch
names the Meshing Agent defines in system/blockMeshDict - this is the single most
common failure point in generated cases, so be precise and consistent.

Output valid OpenFOAM dictionary syntax only, matching the FoamFile header format.
"""


class PhysicsAgent:
    def __init__(self, client: OpenAI, model: str):
        self.client = client
        self.model = model

    def draft_fields(self, prompt: str, patch_names: list[str]) -> CaseFiles:
        """Generate 0/ and constant/ files for the given natural-language prompt.

        patch_names must come from whatever the Meshing Agent already decided
        (or be negotiated with it) - see orchestrator.py for the handoff order.
        """
        # TODO(Phase II): build the actual chat completion call, parse the
        # response into individual file contents, populate CaseFiles.zero /
        # .constant. Left unimplemented - no local LLM endpoint to test
        # against in this environment.
        raise NotImplementedError
