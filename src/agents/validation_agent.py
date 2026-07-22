"""Syntax Validation Agent: runs blockMesh / checkMesh against a drafted case,
parses failures into structured errors, and drives the retry loop.

Reference: docs/openfoam/blockmesh.md (workflow section) and the literature
review's FLARE finding (temp/paper-research-agentic-cfd-llm/pdfs/2606.03852_FLARE.pdf) -
error-message specificity fed back to the generating agent matters more than
the retry mechanism itself. So parse_errors() below must not just hand back
raw stderr - it should localize which file/section/line is implicated where
possible, and classify the failure so the orchestrator can route the fix to
whichever agent actually owns the broken file.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path


class FailureClass(Enum):
    # blockMesh itself refused to build a mesh - usually vertex ordering /
    # block-geometry inconsistency. Route back to the Meshing Agent.
    MESH_GENERATION = auto()
    # blockMesh succeeded but checkMesh flagged quality issues (non-orthogonality,
    # skewness, etc.) on an otherwise-valid mesh. Route back to the Meshing Agent,
    # different fix (grading/cell count) than a MESH_GENERATION failure.
    MESH_QUALITY = auto()
    # Parse/dictionary syntax error in a non-mesh file - route to whichever
    # agent owns that file (Physics Agent for 0/, constant/; Meshing Agent
    # for system/blockMeshDict; ambiguous for system/controlDict etc.)
    DICTIONARY_SYNTAX = auto()
    UNKNOWN = auto()


@dataclass
class ValidationError:
    failure_class: FailureClass
    file_hint: str | None  # e.g. "system/blockMeshDict" if identifiable from output
    message: str  # the localized, specific error text - not full raw stderr


@dataclass
class ValidationResult:
    ok: bool
    errors: list[ValidationError]


class ValidationAgent:
    def run_block_mesh(self, case_dir: Path) -> ValidationResult:
        result = subprocess.run(
            ["blockMesh"], cwd=case_dir, capture_output=True, text=True
        )
        if result.returncode == 0:
            return ValidationResult(ok=True, errors=[])
        return ValidationResult(ok=False, errors=self.parse_errors(result.stderr))

    def run_check_mesh(self, case_dir: Path) -> ValidationResult:
        result = subprocess.run(
            ["checkMesh"], cwd=case_dir, capture_output=True, text=True
        )
        # TODO(Phase II): checkMesh returns 0 even with quality warnings in some
        # versions - don't rely on returncode alone, grep stdout for "***" /
        # "FAILED" markers checkMesh uses for real failures vs warnings.
        if result.returncode == 0:
            return ValidationResult(ok=True, errors=[])
        return ValidationResult(ok=False, errors=self.parse_errors(result.stdout))

    def parse_errors(self, raw_output: str) -> list[ValidationError]:
        """Turn raw blockMesh/checkMesh output into localized, classified errors.

        TODO(Phase II): this is the highest-value piece of the whole validation
        agent per the FLARE finding above - implement real parsing (regex on
        OpenFOAM's "FOAM FATAL ERROR" blocks, which usually name the offending
        file/keyword) instead of passing raw_output straight through. Left as
        a naive passthrough for now so the rest of the scaffold has a concrete
        interface to build against.
        """
        if not raw_output.strip():
            return []
        return [
            ValidationError(
                failure_class=FailureClass.UNKNOWN,
                file_hint=None,
                message=raw_output.strip(),
            )
        ]
