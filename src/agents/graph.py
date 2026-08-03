"""LangGraph orchestration for the CaseSpec pipeline.

Replaces the hand-rolled loop in orchestrator.py (per docs/agent-framework-
comparison.md §7: "orchestrator.py becomes the LangGraph graph"). Nodes wrap
the existing, already-tested building blocks (mesh_io, intent, renderer,
validation_agent) unchanged; this module only adds control flow and retry
routing on top of them.

Graph shape (docs/phase-ii-architecture.md §2.1 is the reference diagram):

    read_mesh -> extract_intent -> expand_spec -> render_case -> validate_case
                      ^                                                |
                      +---------------- retry (FailureClass routed) ---+
                                                                        |
                                                                       END (ok or attempts exhausted)

Scope for this milestone (R1: no mesh generation in this repo): `mesh_dir` is
required and always points at an externally-provided mesh. blockMesh is never
run; the ladder starts at checkMesh.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, TypedDict

from langgraph.graph import END, StateGraph

from .case import CaseFiles
from .intent import CaseIntent, intent_to_spec
from .llm_client import LLMConfig
from .mesh_io import MeshPatch, copy_polymesh, load_external_mesh
from .pipeline import extract_intent
from .renderer import render
from .spec import CaseSpec
from .validation_agent import ValidationAgent, ValidationResult

DEFAULT_MAX_ATTEMPTS = 3


class CaseGraphState(TypedDict, total=False):
    nl: str  # the user's natural-language problem statement
    mesh_dir: str  # path to the externally-provided mesh (required, R1)
    case_dir: str  # where to write the rendered case
    config: Optional[LLMConfig]

    known_patches: list[MeshPatch]
    intent: CaseIntent
    spec: CaseSpec
    case_files: CaseFiles
    validation: ValidationResult

    attempt: int
    max_attempts: int
    error_feedback: Optional[str]  # last validation failure, fed back to extract_intent
    done: bool


# --- nodes ------------------------------------------------------------------

def read_mesh(state: CaseGraphState) -> dict:
    """Read the provided mesh's boundary file to learn real patch names/types.

    No mesh generation happens here or anywhere in this repo (R1) - this only
    parses the small `boundary` dictionary (mesh_io.parse_boundary_file).
    """
    patches = load_external_mesh(Path(state["mesh_dir"]))
    return {"known_patches": patches}


def build_intent(state: CaseGraphState) -> dict:
    """LLM call: NL (+ real patch names, + prior failure if retrying) -> CaseIntent."""
    nl = state["nl"]
    if state.get("error_feedback"):
        nl = (
            f"{nl}\n\nThe previous attempt failed validation with this error - "
            f"fix the case intent to address it:\n{state['error_feedback']}"
        )
    intent = extract_intent(nl, state.get("config"), known_patches=state["known_patches"])
    return {"intent": intent, "attempt": state.get("attempt", 0) + 1}


def expand_spec(state: CaseGraphState) -> dict:
    """Deterministic CaseIntent -> CaseSpec expansion (intent.intent_to_spec)."""
    return {"spec": intent_to_spec(state["intent"])}


def render_case(state: CaseGraphState) -> dict:
    """Deterministic CaseSpec -> files (renderer.render), then write to disk
    and copy the untouched polyMesh files alongside them (R1: never generated,
    only ever copied)."""
    case_dir = Path(state["case_dir"])
    case = render(state["spec"])
    case.write(case_dir)
    copy_polymesh(Path(state["mesh_dir"]), case_dir)
    return {"case_files": case}


def validate_case(state: CaseGraphState) -> dict:
    """Run the validation ladder, skipping blockMesh since the mesh is
    externally provided (validation_agent.run_ladder(skip_mesh_generation=True))."""
    result = ValidationAgent().run_ladder(Path(state["case_dir"]), skip_mesh_generation=True)
    feedback = None
    if not result.ok:
        feedback = f"[{result.stage}] " + "\n".join(e.message for e in result.errors)
    return {"validation": result, "error_feedback": feedback, "done": result.ok}


# --- routing ------------------------------------------------------------

def route_after_validate(state: CaseGraphState) -> str:
    if state["validation"].ok:
        return END
    if state.get("attempt", 0) >= state.get("max_attempts", DEFAULT_MAX_ATTEMPTS):
        return END
    return "build_intent"


def build_graph():
    """Assemble the StateGraph. Call .compile() on the result to get a runnable."""
    graph = StateGraph(CaseGraphState)
    graph.add_node("read_mesh", read_mesh)
    graph.add_node("build_intent", build_intent)
    graph.add_node("expand_spec", expand_spec)
    graph.add_node("render_case", render_case)
    graph.add_node("validate_case", validate_case)

    graph.set_entry_point("read_mesh")
    graph.add_edge("read_mesh", "build_intent")
    graph.add_edge("build_intent", "expand_spec")
    graph.add_edge("expand_spec", "render_case")
    graph.add_edge("render_case", "validate_case")
    graph.add_conditional_edges("validate_case", route_after_validate,
                                {"build_intent": "build_intent", END: END})
    return graph


def run(nl: str, mesh_dir: Path, case_dir: Path, config: LLMConfig | None = None,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS) -> CaseGraphState:
    """Convenience entry point: compile the graph and run it to completion."""
    app = build_graph().compile()
    initial: CaseGraphState = {
        "nl": nl,
        "mesh_dir": str(mesh_dir),
        "case_dir": str(case_dir),
        "config": config,
        "attempt": 0,
        "max_attempts": max_attempts,
    }
    return app.invoke(initial)
