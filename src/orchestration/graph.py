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

Two input modes:
  - `mesh_dir` provided: externally-supplied mesh. The boundary file is parsed
    for real patch names, polyMesh is copied as-is, and the ladder skips
    blockMesh (starts at checkMesh).
  - no `mesh_dir`: box geometry. Patch names come from the prompt, the renderer
    emits a blockMeshDict, and the full ladder (blockMesh -> checkMesh ->
    foamRun) runs.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

from langgraph.graph import END, StateGraph
from typing_extensions import TypedDict

from ir.intent import CaseIntent, intent_to_spec
from ir.safe_exit import (
    PipelineResult,
    RemediationAttempt,
    SafeExitReport,
    SafeExitValidationResult,
)
from ir.spec import CaseSpec
from llm.llm_client import LLMConfig
from mesh.mesh_io import MeshPatch, copy_polymesh, load_external_mesh
from preflight import PreflightResult, PreflightVerdict
from preflight.runner import run_preflight_with_agent
from rendering.case import CaseFiles, apply_file_overrides
from rendering.renderer import render
from validation.validation_agent import (
    FailureClass,
    ValidationAgent,
    ValidationResult,
)

from .pipeline import extract_file_patches, extract_intent

DEFAULT_MAX_ATTEMPTS = 3


class CaseGraphState(TypedDict, total=False):
    nl: str  # the user's natural-language problem statement
    mesh_dir: str | None  # path to an externally-provided mesh (box geometry if absent)
    case_dir: str  # where to write the rendered case
    config: LLMConfig | None

    known_patches: list[MeshPatch]
    intent: CaseIntent
    safe_exit: SafeExitReport | None  # populated when case is validated as genuinely unresolvable
    safe_exit_validation: SafeExitValidationResult | None  # authoritative validation record
    remediation_history: list[RemediationAttempt]  # ledger of attempted remedies
    invalid_safe_exit_count: int  # count of rejected safe exit proposals
    spec: CaseSpec
    case_files: CaseFiles
    file_overrides: dict[str, str] | None  # direct target file content patches from agent
    validation: ValidationResult
    preflight: PreflightResult  # Level A/B/C preflight gate result (set by preflight_case)

    procs: int | None
    timeout: int | None
    image: str | None
    check_mesh_all: bool | None

    attempt: int
    patch_attempt: int
    max_attempts: int
    error_feedback: str | None  # last validation failure, fed back to extract_intent
    intent_error: str | None  # structured error when extract_intent couldn't produce an intent
    patch_stats: dict[str, int]  # {"attempts": N, "resolved": M} — targeted-patch success tracking
    done: bool


# --- helpers ----------------------------------------------------------------

def _make_validation_agent(state: CaseGraphState) -> ValidationAgent:
    kwargs: dict = {}
    if (procs := state.get("procs")) is not None:
        kwargs["nprocs"] = procs
    if (timeout := state.get("timeout")) is not None:
        kwargs["timeout"] = timeout
    if (image := state.get("image")) is not None:
        kwargs["image"] = image
    if (check_mesh_all := state.get("check_mesh_all")) is not None:
        kwargs["check_mesh_all"] = check_mesh_all
    return ValidationAgent(**kwargs)


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
    """If a mesh was provided, read its boundary file to learn real patch
    names/types (mesh_io.parse_boundary_file). Otherwise the case is box
    geometry and the renderer will generate the mesh via blockMesh.
    """
    mesh_dir = state.get("mesh_dir")
    if not mesh_dir:
        print("\n[1/5] No external mesh provided - box geometry will be meshed by blockMesh.", flush=True)
        return {"known_patches": []}
    print(f"\n[1/5] Reading mesh boundary from {mesh_dir}...", flush=True)
    patches = load_external_mesh(Path(mesh_dir))
    print(f"      -> Loaded {len(patches)} patches: {[p.name for p in patches]}", flush=True)
    return {"known_patches": patches}


def build_intent(state: CaseGraphState) -> dict:
    """LLM call: NL (+ real patch names, + prior failure & file content if retrying) -> CaseIntent.
    Clears stale file_overrides so a fresh full re-render happens on this attempt.
    """
    attempt = state.get("attempt", 0) + 1
    max_attempts = state.get("max_attempts", DEFAULT_MAX_ATTEMPTS)
    nl = state.get("nl", "")
    err_fb = state.get("error_feedback")
    if err_fb:
        print(f"\n[Retry {attempt}/{max_attempts}] Querying LLM to refine CaseIntent based on validation failure...", flush=True)
        file_context = _collect_file_context(state)
        nl = (
            f"{nl}\n\nThe previous attempt failed validation with this error:\n{err_fb}"
            f"{file_context}\n\nRead the error and file content above and fix the case."
        )
    else:
        print(f"\n[2/5] Querying LLM to build CaseIntent (Attempt {attempt}/{max_attempts})...", flush=True)
    known = state.get("known_patches")
    try:
        intent = extract_intent(
            nl,
            state.get("config"),
            known_patches=known,
            validation_result=state.get("validation"),
            preflight_result=state.get("preflight"),
            case_dir=state.get("case_dir"),
            remediation_history=state.get("remediation_history"),
        )
    except ValueError as exc:
        # Inner extract_intent loop exhausted (≤3 LLM repair attempts) without
        # producing a valid CaseIntent. Surfacing a structured failure (rather
        # than letting the exception crash the graph) lets the router end the
        # run cleanly instead of wedging on an empty intent.
        print(f"      -> Intent extraction FAILED: {exc}", flush=True)
        return {
            "intent_error": f"Could not extract a valid CaseIntent: {exc}",
            "attempt": attempt,
            "patch_attempt": 0,
            "file_overrides": None,
        }

    if intent.safe_exit is not None:
        from knowledge.intent_validator import IntentValidator
        validator = IntentValidator()
        safe_val = validator.validate_safe_exit(
            intent,
            prompt_text=nl,
            validation_result=state.get("validation"),
            preflight_result=state.get("preflight"),
            case_dir=state.get("case_dir"),
            remediation_history=state.get("remediation_history"),
            attempt=attempt,
        )
        if safe_val.is_valid:
            print(f"      -> Agent proposed validated Safe Exit: [{intent.safe_exit.category.value}] {intent.safe_exit.error_summary}", flush=True)
            return {
                "intent": intent,
                "safe_exit": intent.safe_exit,
                "safe_exit_validation": safe_val,
                "attempt": attempt,
                "patch_attempt": 0,
                "file_overrides": None,
                "done": True,
            }
        else:
            inv_count = state.get("invalid_safe_exit_count", 0) + 1
            print(f"      -> Proposed Safe Exit rejected ({len(safe_val.rejection_reasons)} reason(s)): {safe_val.rejection_reasons}", flush=True)
            if attempt >= max_attempts:
                return {
                    "intent_error": f"Invalid safe exit proposal exhausted retry budget: {'; '.join(safe_val.rejection_reasons)}",
                    "safe_exit_validation": safe_val,
                    "invalid_safe_exit_count": inv_count,
                    "attempt": attempt,
                    "done": False,
                }

    print(f"      -> Extracted intent (flow_type={intent.flow_type}, regime={intent.regime}, species={intent.fluid_species}, turb_model={intent.turbulence_model or 'kEpsilon'})", flush=True)
    if intent.custom_fv_solution:
        print("      -> Agent specified custom fvSolution relaxation/solvers", flush=True)
    if intent.custom_fv_schemes:
        print("      -> Agent specified custom fvSchemes discretization", flush=True)
    bc_ov_patches = [p.name for p in intent.patches if p.bc_overrides]
    if bc_ov_patches:
        print(f"      -> Agent BC overrides on patches: {bc_ov_patches}", flush=True)
    # Clear stale overrides and reset patch_attempt counter so render_case does a clean render.
    return {"intent": intent, "attempt": attempt, "patch_attempt": 0, "file_overrides": None}


def expand_spec(state: CaseGraphState) -> dict:
    """Deterministic CaseIntent -> CaseSpec expansion (intent.intent_to_spec)."""
    print("\n[3/5] Expanding CaseIntent into CaseSpec (RAG & thermophysical lookup)...", flush=True)
    intent = state.get("intent")
    if intent is None:
        raise ValueError("Missing intent in CaseGraphState")
    spec = intent_to_spec(intent)
    print("      -> Expanded spec successfully.", flush=True)
    return {"spec": spec}


def render_case(state: CaseGraphState) -> dict:
    """Deterministic CaseSpec -> files (renderer.render), then write to disk,
    copy the untouched polyMesh files, and apply any direct agent file_overrides."""
    case_dir_str = state.get("case_dir", "")
    mesh_dir_str = state.get("mesh_dir", "")
    print(f"\n[4/5] Rendering OpenFOAM dictionary files to {case_dir_str}...", flush=True)
    spec = state.get("spec")
    if spec is None:
        raise ValueError("Missing spec in CaseGraphState")
    case_dir = Path(case_dir_str)
    case = render(spec)
    case.write(case_dir)
    if mesh_dir_str:
        copy_polymesh(Path(mesh_dir_str), case_dir)

    file_overrides = state.get("file_overrides")
    if file_overrides:
        skipped = apply_file_overrides(case_dir, file_overrides)
        if skipped:
            # A patch whose key nests below an existing file (e.g. an LLM
            # emitting "system/fvSolution/solvers" instead of the whole-file
            # "system/fvSolution") cannot be applied as a whole-file override.
            # Skip rather than crash; the failing validation re-runs and re-feeds
            # the error if the case is still broken.
            print(f"      -> skipped {len(skipped)} nested/illegal override(s) not applied as whole files",
                  flush=True)

    print("      -> Rendered case files written successfully.", flush=True)
    return {"case_files": case}


def preflight_case(state: CaseGraphState) -> dict:
    """Run the preflight gate (Levels A/B/C) before the full Docker ladder.

    A static audit first (no Docker); if that passes, mesh checks and a bounded
    solver dry-run predict whether the full `foamRun` will converge. FAILs are
    routed back to build_intent/patch_files with the audit as evidence, so a
    known-bad case never spends minutes on a doomed validation run.
    """
    case_dir = Path(state.get("case_dir", ""))
    print(f"\n[4.5/5] Preflight gate on {case_dir} (audit + bounded dry run)...", flush=True)
    agent = _make_validation_agent(state)
    result = run_preflight_with_agent(case_dir, agent)
    state["preflight"] = result
    feedback = None
    history = list(state.get("remediation_history") or [])
    if result.verdict is PreflightVerdict.FAIL:
        feedback = "[preflight] " + result.as_evidence()
        history.append(RemediationAttempt(
            attempt=state.get("attempt", 1),
            issue_class="PREFLIGHT_AUDIT",
            action="preflight_case",
            configuration_changes={},
            outcome="failed",
            evidence_reference=result.as_evidence()[:100] if result else None,
        ))
    print(f"      -> Preflight: {result.verdict.name} "
          f"({len([f for f in result.audit if f.level == 'fail'])} FAIL, "
          f"{len([f for f in result.audit if f.level == 'warn'])} WARN)", flush=True)
    return {"preflight": result, "error_feedback": feedback, "remediation_history": history}


def patch_files(state: CaseGraphState) -> dict:
    """LLM call: read the failing file(s) and emit direct dictionary patches.

    Activated when the validation failure class is DICTIONARY_SYNTAX — the
    case structure is correct but a specific file has wrong content. Rather than
    re-running the full intent extraction, the agent reads the exact file that
    OpenFOAM rejected and returns corrected content as file_overrides.
    """
    patch_attempt = state.get("patch_attempt", 0) + 1
    print(f"\n[Self-Correction {patch_attempt}/3] Querying LLM for targeted dictionary patch...", flush=True)
    err_fb = state.get("error_feedback", "")
    file_context = _collect_file_context(state)

    prompt = (
        f"The OpenFOAM case failed with a dictionary/syntax error:\n{err_fb}"
        f"{file_context}\n\n"
        "Return corrected file content as JSON: {\"file_overrides\": {\"<relative/path>\": \"<full corrected content>\"}}"
    )

    overrides = extract_file_patches(prompt, state.get("config"))
    print(f"      -> LLM returned patches for: {list(overrides.keys())}", flush=True)
    stats = dict(state.get("patch_stats") or {})
    stats["attempts"] = stats.get("attempts", 0) + 1
    history = list(state.get("remediation_history") or [])
    history.append(RemediationAttempt(
        attempt=patch_attempt,
        issue_class="DICTIONARY_SYNTAX",
        action="extract_file_patches",
        configuration_changes={"file_overrides": list(overrides.keys())},
        outcome="succeeded" if overrides else "failed",
        evidence_reference=err_fb[:100] if err_fb else None,
    ))
    return {
        "file_overrides": overrides,
        "patch_attempt": patch_attempt,
        "patch_stats": stats,
        "remediation_history": history,
    }


def validate_case(state: CaseGraphState) -> dict:
    """Run the validation ladder. blockMesh is skipped only when the mesh is
    externally provided (validation_agent.run_ladder(skip_mesh_generation=True));
    box-geometry cases run the full ladder including blockMesh."""
    case_dir_str = state.get("case_dir", "")
    mesh_dir_str = state.get("mesh_dir")
    print(f"\n[5/5] Running Docker OpenFOAM validation ladder on {case_dir_str}...", flush=True)
    result = _make_validation_agent(state).run_ladder(Path(case_dir_str), skip_mesh_generation=bool(mesh_dir_str))
    feedback = None
    history = list(state.get("remediation_history") or [])
    if not result.ok:
        feedback = f"[{result.stage}] " + "\n".join(e.message for e in result.errors)
        err_classes = [e.failure_class.name for e in result.errors]
        print(f"      -> Validation FAILED at stage [{result.stage}]: {err_classes}", flush=True)
        history.append(RemediationAttempt(
            attempt=state.get("attempt", 1),
            issue_class=",".join(err_classes) if err_classes else "VALIDATION_FAILURE",
            action="validate_case",
            configuration_changes={},
            outcome="failed",
            evidence_reference=feedback[:100] if feedback else None,
        ))
    else:
        print(f"      -> Validation PASSED at stage [{result.stage}]!", flush=True)
        history.append(RemediationAttempt(
            attempt=state.get("attempt", 1),
            issue_class="NONE",
            action="validate_case",
            configuration_changes={},
            outcome="succeeded",
            evidence_reference=None,
        ))
    # If a targeted patch was applied this trip and validation now passes, the
    # patch_files path did its job. Record it so patch resolution rate is measurable.
    patch_stats = state.get("patch_stats")
    if patch_stats and patch_stats.get("attempts") and state.get("file_overrides"):
        if result.ok:
            print(f"      -> patch resolved the failure (success rate: {patch_stats['attempts']} attempts)", flush=True)
        else:
            print(f"      -> patch did NOT resolve (attempt #{patch_stats['attempts']})", flush=True)
    return {"validation": result, "error_feedback": feedback, "done": result.ok, "remediation_history": history}



# --- routing ------------------------------------------------------------

def route_after_preflight(state: CaseGraphState) -> str:
    """Route after the preflight gate.

    FAIL -> build_intent (the audit/dry-run evidence is already in
    error_feedback). Environment-only skip (no Docker) or PASS/WARN -> proceed
    to the full validation ladder, which is the source of truth.
    """
    pre = state.get("preflight")
    if pre is not None and pre.verdict is PreflightVerdict.FAIL:
        if "Docker/OpenFOAM unavailable" in (pre.message or ""):
            return "validate_case"  # env skip — ladder will surface it
        if state.get("attempt", 0) >= state.get("max_attempts", DEFAULT_MAX_ATTEMPTS):
            print("      -> Max attempts reached after preflight failures. Ending run.", flush=True)
            return END
        print("      -> Preflight FAILED — routing back to build_intent with audit evidence.", flush=True)
        return "build_intent"
    return "validate_case"


def route_after_validate(state: CaseGraphState) -> str:
    """Route by failure class.

    ENVIRONMENT (Docker/infra) stops immediately — nothing the agent can do.
    DICTIONARY_SYNTAX takes a targeted fix path (patch_files) up to 3 times per intent;
    if patch limit is reached, falls back to build_intent for a full intent rebuild.
    All other LLM-owned failures (mesh/solver) go back to build_intent.
    """
    val = state.get("validation")
    if state.get("intent_error"):
        return END  # extraction failed at the LLM layer — end cleanly, surface error
    if val and val.ok:
        return END
    if state.get("attempt", 0) >= state.get("max_attempts", DEFAULT_MAX_ATTEMPTS):
        return END
    if val and val.errors:
        classes = {e.failure_class for e in val.errors}
        if FailureClass.ENVIRONMENT in classes:
            return END  # infra failure — stop, surface to operator
        if classes == {FailureClass.DICTIONARY_SYNTAX}:
            if state.get("patch_attempt", 0) < 3:
                return "patch_files"  # targeted file-level fix
            print("      -> Max patch attempts reached for dictionary syntax errors. Falling back to full intent rebuild.", flush=True)
    return "build_intent"


def route_after_patch(state: CaseGraphState) -> str:
    """After file patching, re-render (apply overrides) and re-validate."""
    if state.get("attempt", 0) >= state.get("max_attempts", DEFAULT_MAX_ATTEMPTS):
        return END
    if state.get("patch_attempt", 0) >= 3:
        return "build_intent"
    return "render_case"


def build_graph():
    """Assemble the StateGraph. Call .compile() on the result to get a runnable."""
    graph = StateGraph(cast(type, CaseGraphState))
    graph.add_node("read_mesh", read_mesh)
    graph.add_node("build_intent", build_intent)
    graph.add_node("expand_spec", expand_spec)
    graph.add_node("render_case", render_case)
    graph.add_node("patch_files", patch_files)
    graph.add_node("preflight_case", preflight_case)
    graph.add_node("validate_case", validate_case)

    graph.set_entry_point("read_mesh")
    graph.add_edge("read_mesh", "build_intent")
    graph.add_conditional_edges(
        "build_intent",
        lambda state: END if (state.get("intent_error") or state.get("safe_exit")) else "expand_spec",
        {"expand_spec": "expand_spec", END: END},
    )
    graph.add_edge("expand_spec", "render_case")
    graph.add_edge("render_case", "preflight_case")
    graph.add_conditional_edges(
        "preflight_case",
        route_after_preflight,
        {"build_intent": "build_intent", "validate_case": "validate_case", END: END},
    )
    graph.add_conditional_edges(
        "validate_case",
        route_after_validate,
        {"build_intent": "build_intent", "patch_files": "patch_files", END: END},
    )
    graph.add_edge("patch_files", "render_case")
    return graph


def run(nl: str, case_dir: Path, mesh_dir: Path | None = None, config: LLMConfig | None = None,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS, procs: int | None = None,
        timeout: int | None = None, image: str | None = None,
        check_mesh_all: bool | None = None) -> CaseGraphState:
    """Convenience entry point: compile the graph and run it to completion.

    `mesh_dir` is optional. When given, the case is built around the provided
    mesh (patch names read from its boundary file). When omitted, the case uses
    box geometry and is meshed by blockMesh during validation.
    """
    app = build_graph().compile()
    initial: CaseGraphState = {
        "nl": nl,
        "mesh_dir": str(mesh_dir) if mesh_dir else None,
        "case_dir": str(case_dir),
        "config": config,
        "attempt": 0,
        "max_attempts": max_attempts,
        "patch_stats": {"attempts": 0},
        "remediation_history": [],
        "invalid_safe_exit_count": 0,
        "procs": procs,
        "timeout": timeout,
        "image": image,
        "check_mesh_all": check_mesh_all,
    }
    return cast(CaseGraphState, app.invoke(initial))


def run_pipeline(nl: str, case_dir: Path, mesh_dir: Path | None = None, config: LLMConfig | None = None,
                 max_attempts: int = DEFAULT_MAX_ATTEMPTS, procs: int | None = None,
                 timeout: int | None = None, image: str | None = None,
                 check_mesh_all: bool | None = None) -> PipelineResult:
    """Execute the case generation graph and return a structured PipelineResult."""
    final_state = run(
        nl=nl,
        case_dir=case_dir,
        mesh_dir=mesh_dir,
        config=config,
        max_attempts=max_attempts,
        procs=procs,
        timeout=timeout,
        image=image,
        check_mesh_all=check_mesh_all,
    )
    safe_exit = final_state.get("safe_exit")
    safe_val = final_state.get("safe_exit_validation")
    val = final_state.get("validation")
    intent_err = final_state.get("intent_error")
    history = final_state.get("remediation_history", [])
    attempts = final_state.get("attempt", 0)

    if safe_exit is not None:
        return PipelineResult(
            success=False,
            exit_code=2,
            safe_exit=safe_exit,
            safe_exit_validation=safe_val,
            remediation_history=history,
            case_dir=str(case_dir),
            attempts_used=attempts,
        )

    is_ok = bool(val and val.ok and not intent_err)
    err_msg = intent_err or ("; ".join(e.message for e in val.errors) if (val and not val.ok) else None)
    return PipelineResult(
        success=is_ok,
        exit_code=0 if is_ok else 1,
        safe_exit=None,
        safe_exit_validation=safe_val,
        error=err_msg,
        remediation_history=history,
        case_dir=str(case_dir),
        attempts_used=attempts,
    )

