"""SafeExitReport — structured contract for unresolvable cases with strict evidence.

A safe exit is an auditable terminal outcome indicating that the requested case
cannot proceed within the pipeline's supported capabilities and permitted assumptions.
It must be grounded in trusted run state or direct user requirements.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


class SafeExitCategory(StrEnum):
    CORRUPT_OR_UNCLOSED_MESH = "corrupt_or_unclosed_mesh"
    CONTRADICTORY_PHYSICS = "contradictory_physics"
    MISSING_ESSENTIAL_DATA = "missing_essential_data"
    UNSUPPORTED_PHYSICS = "unsupported_physics"


class SafeExitEvidence(BaseModel):
    """Verifiable evidence grounded in execution state, logs, or prompt text."""

    source: Literal["solver_log", "check_mesh", "user_prompt", "case_file"]
    detail: str  # exact normalized substring from the trusted source
    file_path: str | None = None  # required when source == "case_file", relative to case_dir


class RemediationAttempt(BaseModel):
    """Authoritative ledger entry for an attempted automated remediation."""

    attempt: int
    issue_class: str
    action: str
    configuration_changes: dict[str, Any] = Field(default_factory=dict)
    outcome: Literal["failed", "not_applicable", "succeeded"]
    evidence_reference: str | None = None


class SafeExitReport(BaseModel):
    """Structured report produced when a case is genuinely unresolvable."""

    category: SafeExitCategory
    error_summary: str  # minimum 20 non-whitespace characters explaining the deadlock
    evidence: list[SafeExitEvidence] = Field(default_factory=list)
    exhausted_alternatives: list[str] = Field(default_factory=list)
    user_action_required: list[str] = Field(default_factory=list)
    remediation_attempt_ids: list[int] = Field(default_factory=list)


class SafeExitValidationResult(BaseModel):
    """Authoritative decision from IntentValidator regarding a SafeExitReport."""

    is_valid: bool
    rejection_reasons: list[str] = Field(default_factory=list)
    verified_evidence: list[SafeExitEvidence] = Field(default_factory=list)
    validated_at_attempt: int = 1


class PipelineResult(BaseModel):
    """Programmatic contract returned at the conclusion of a pipeline run."""

    success: bool
    exit_code: int
    safe_exit: SafeExitReport | None = None
    safe_exit_validation: SafeExitValidationResult | None = None
    error: str | None = None
    remediation_history: list[RemediationAttempt] = Field(default_factory=list)
    case_dir: str | None = None
    attempts_used: int = 0
