"""Meshing Agent: owns system/blockMeshDict.

Reference: docs/openfoam/blockmesh.md (annotated real cavity + pipe examples).
"""

from __future__ import annotations

from openai import OpenAI

from .case import CaseFiles

SYSTEM_PROMPT = """\
You are the Meshing Agent in a multi-agent OpenFOAM case-generation pipeline.

You own system/blockMeshDict: vertices, blocks, (optionally) edges/geometry for
curved surfaces, and the boundary patch definitions (name + type + faces).

Rules that matter (see docs/openfoam/blockmesh.md for detail):
- Vertex ordering within a hex block must be consistent (bottom face four
  points, then top face four points in the same rotational order) or the
  mesh will have negative-volume cells and blockMesh will reject it.
- Every boundary patch you define here becomes a name the Physics Agent must
  reference exactly in 0/*/boundaryField. Decide and report your patch names
  before physics fields are drafted, or reconcile afterward - see
  orchestrator.py for which handoff order this pipeline uses.
- For simple parametric domains (box, channel, single cylinder), the flat
  vertex/block form is enough. Only use named/projected vertices and a
  geometry{} block (see the pipe example) if the prompt actually requires
  curved geometry.

Output valid OpenFOAM dictionary syntax only, matching the FoamFile header format.
"""


class MeshingAgent:
    def __init__(self, client: OpenAI, model: str):
        self.client = client
        self.model = model

    def draft_block_mesh_dict(self, prompt: str) -> tuple[CaseFiles, list[str]]:
        """Generate system/blockMeshDict for the given natural-language prompt.

        Returns the CaseFiles (with .system["blockMeshDict"] populated) plus
        the list of boundary patch names it chose, so the orchestrator can
        pass them to the Physics Agent.
        """
        # TODO(Phase II): build the actual chat completion call, parse response
        # into blockMeshDict text, extract patch names from the boundary{}
        # block for handoff. Left unimplemented - no local LLM endpoint to
        # test against in this environment.
        raise NotImplementedError
