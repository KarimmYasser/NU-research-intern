"""LangGraph orchestration for the CaseSpec pipeline.

Replaces the hand-rolled loop in orchestrator.py (per docs/agent-framework-
comparison.md §7: "orchestrator.py becomes the LangGraph graph"). Nodes wrap
the existing, already-tested building blocks (mesh_io, intent, renderer,
validation_agent) unchanged; this module only adds control flow and retry
routing on top of them.

Graph shape:

    read_mesh -> build_intent -> expand_spec -> render_case -> validate_case
                      ^                                              |
                      |               (DICTIONARY_SYNTAX)           |
                      |         patch_files <-----------------------+
                      |               |                             |
                      +---(other) ----+                            END

Routing rationale:
  - ENVIRONMENT        → END immediately (Docker/infra — not a case error)
  - DICTIONARY_SYNTAX  → patch_files (LLM reads the exact failing file and
                          emits corrected content; the renderer has coverage
                          gaps so these errors are often fixable this way)
  - MESH_*/SOLVER_*    → build_intent (full intent re-extraction)

Scope for this milestone (R1: no mesh generation in this repo): `mesh_dir` is
required and always points at an externally-provided mesh. blockMesh is never
run; the ladder starts at checkMesh.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, TypedDict, cast

from langgraph.graph import END, StateGraph

from ir.intent import CaseIntent, intent_to_spec
from ir.spec import CaseSpec
from llm.llm_client import LLMConfig
from mesh.mesh_io import MeshPatch, copy_polymesh, load_external_mesh
from rendering.case import CaseFiles, apply_file_overrides
from rendering.renderer import render
from validation.validation_agent import FailureClass, ValidationAgent, ValidationResult
from .pipeline import extract_file_patches, extract_intent

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
    file_overrides: Optional[dict[str, str]]  # direct target file content patches from agent
    validation: ValidationResult

    attempt: int
    max_attempts: int
    error_feedback: Optional[str]  # last validation failure, fed back to extract_intent
    done: bool


# --- helpers ----------------------------------------------------------------

def _collect_file_context(state: CaseGraphState) -> str:
    """Read the actual content of all files mentioned in the last validation errors."""
    val_res = state.get("validation")
    case_dir_str = state.get("case_dir")
    if not val_res or not val_res.errors or not case_dir_str:
        return ""
    case_dir = Path(case_dir_str)
    parts: list[str] = []
    seen: set[str] = set()
    for err in val_res.errors:
        if err.file_hint and err.file_hint not in seen:
            seen.add(err.file_hint)
            fpath = case_dir / err.file_hint
            if fpath.is_file():
                try:
                    content = fpath.read_text(encoding="utf-8")
                    parts.append(
                        f"\n\nContent of failing file ({err.file_hint}):\n"
                        f"```openfoam\n{content}\n```"
                    )
                except OSError:
                    pass
    return "".join(parts)


# --- nodes ------------------------------------------------------------------

def read_mesh(state: CaseGraphState) -> dict:
    """Read the provided mesh's boundary file to learn real patch names/types.

    No mesh generation happens here or anywhere in this repo (R1) - this only
    parses the small `boundary` dictionary (mesh_io.parse_boundary_file).
    """
    mesh_dir = state.get("mesh_dir", "")
    patches = load_external_mesh(Path(mesh_dir))
    return {"known_patches": patches}


def build_intent(state: CaseGraphState) -> dict:
    """LLM call: NL (+ real patch names, + prior failure & file content if retrying) -> CaseIntent.
    Clears stale file_overrides so a fresh full re-render happens on this attempt.
    """
    nl = state.get("nl", "")
    err_fb = state.get("error_feedback")
    if err_fb:
        file_context = _collect_file_context(state)
        nl = (
            f"{nl}\n\nThe previous attempt failed validation with this error:\n{err_fb}"
            f"{file_context}\n\nRead the error and file content above and fix the case."
        )
    known = state.get("known_patches")
    intent = extract_intent(nl, state.get("config"), known_patches=known)
    # Clear stale overrides so render_case does a clean render on this attempt.
    return {"intent": intent, "attempt": state.get("attempt", 0) + 1, "file_overrides": None}


def expand_spec(state: CaseGraphState) -> dict:
    """Deterministic CaseIntent -> CaseSpec expansion (intent.intent_to_spec)."""
    intent = state.get("intent")
    if intent is None:
        raise ValueError("Missing intent in CaseGraphState")
    return {"spec": intent_to_spec(intent)}


def render_case(state: CaseGraphState) -> dict:
    """Deterministic CaseSpec -> files (renderer.render), then write to disk,
    copy the untouched polyMesh files, and apply any direct agent file_overrides."""
    spec = state.get("spec")
    if spec is None:
        raise ValueError("Missing spec in CaseGraphState")
    case_dir_str = state.get("case_dir", "")
    mesh_dir_str = state.get("mesh_dir", "")
    case_dir = Path(case_dir_str)
    case = render(spec)
    case.write(case_dir)
    copy_polymesh(Path(mesh_dir_str), case_dir)

    file_overrides = state.get("file_overrides")
    if file_overrides:
        apply_file_overrides(case_dir, file_overrides)

    return {"case_files": case}


def patch_files(state: CaseGraphState) -> dict:
    """LLM call: read the failing file(s) and emit direct dictionary patches.

    Activated when the validation failure class is DICTIONARY_SYNTAX — the
    case structure is correct but a specific file has wrong content. Rather than
    re-running the full intent extraction, the agent reads the exact file that
    OpenFOAM rejected and returns corrected content as file_overrides.
    """
    val_res = state.get("validation")
    err_fb = state.get("error_feedback", "")
    file_context = _collect_file_context(state)

    prompt = (
        f"The OpenFOAM case failed with a dictionary/syntax error:\n{err_fb}"
        f"{file_context}\n\n"
        "Return corrected file content as JSON: {\"file_overrides\": {\"<relative/path>\": \"<full corrected content>\"}}"
    )

    overrides = extract_file_patches(prompt, state.get("config"))
    return {"file_overrides": overrides}


def validate_case(state: CaseGraphState) -> dict:
    """Run the validation ladder, skipping blockMesh since the mesh is
    externally provided (validation_agent.run_ladder(skip_mesh_generation=True))."""
    case_dir_str = state.get("case_dir", "")
    result = ValidationAgent().run_ladder(Path(case_dir_str), skip_mesh_generation=True)
    feedback = None
    if not result.ok:
        feedback = f"[{result.stage}] " + "\n".join(e.message for e in result.errors)
    return {"validation": result, "error_feedback": feedback, "done": result.ok}


# --- routing ------------------------------------------------------------

def route_after_validate(state: CaseGraphState) -> str:
    """Route by failure class.

    ENVIRONMENT (Docker/infra) stops immediately — nothing the agent can do.
    DICTIONARY_SYNTAX takes a targeted fix path (patch_files) rather than
    re-running the full intent pipeline: the LLM reads the exact failing file
    and emits corrected content. This handles renderer coverage gaps (e.g.
    wrong wall-function name in an alphat BC) without discarding a good intent.
    All other LLM-owned failures (mesh/solver) go back to build_intent.
    """
    val = state.get("validation")
    if val and val.ok:
        return END
    if state.get("attempt", 0) >= state.get("max_attempts", DEFAULT_MAX_ATTEMPTS):
        return END
    if val and val.errors:
        classes = {e.failure_class for e in val.errors}
        if FailureClass.ENVIRONMENT in classes:
            return END  # infra failure — stop, surface to operator
        if classes == {FailureClass.DICTIONARY_SYNTAX}:
            return "patch_files"  # targeted file-level fix
    return "build_intent"


def route_after_patch(state: CaseGraphState) -> str:
    """After file patching, re-render (apply overrides) and re-validate."""
    if state.get("attempt", 0) >= state.get("max_attempts", DEFAULT_MAX_ATTEMPTS):
        return END
    return "render_case"


def build_graph():
    """Assemble the StateGraph. Call .compile() on the result to get a runnable."""
    graph = StateGraph(CaseGraphState)
    graph.add_node("read_mesh", read_mesh)
    graph.add_node("build_intent", build_intent)
    graph.add_node("expand_spec", expand_spec)
    graph.add_node("render_case", render_case)
    graph.add_node("patch_files", patch_files)
    graph.add_node("validate_case", validate_case)

    graph.set_entry_point("read_mesh")
    graph.add_edge("read_mesh", "build_intent")
    graph.add_edge("build_intent", "expand_spec")
    graph.add_edge("expand_spec", "render_case")
    graph.add_edge("render_case", "validate_case")
    graph.add_conditional_edges(
        "validate_case",
        route_after_validate,
        {"build_intent": "build_intent", "patch_files": "patch_files", END: END},
    )
    graph.add_edge("patch_files", "render_case")
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
    return cast(CaseGraphState, app.invoke(initial))
