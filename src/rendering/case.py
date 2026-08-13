"""In-memory representation of an OpenFOAM case being assembled by the agents,
plus the on-disk writer. Kept framework-agnostic so it works the same whether
the orchestrator ends up being LangGraph, AutoGen, or CrewAI.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class CaseFiles:
    """Maps OpenFOAM case-relative paths to file contents.

    File names follow OpenFOAM v13 conventions (see docs/domain/openfoam/case-structure.md):
    constant/physicalProperties and constant/momentumTransport, NOT the older
    transportProperties/turbulenceProperties used in most pre-v13 tutorials and
    in the LLM+CFD papers in the literature review (temp/paper-research-agentic-cfd-llm/).
    """

    zero: dict[str, str] = field(default_factory=dict)  # e.g. "U", "p" -> file content
    constant: dict[str, str] = field(default_factory=dict)  # e.g. "physicalProperties"
    system: dict[str, str] = field(default_factory=dict)  # e.g. "blockMeshDict", "controlDict"
    extra: dict[str, str] = field(default_factory=dict)  # arbitrary case-relative paths (agent-provided)

    def write(self, case_dir: Path) -> None:
        for name, content in self.zero.items():
            (case_dir / "0").mkdir(parents=True, exist_ok=True)
            (case_dir / "0" / name).write_text(content)
        for name, content in self.constant.items():
            (case_dir / "constant").mkdir(parents=True, exist_ok=True)
            (case_dir / "constant" / name).write_text(content)
        for name, content in self.system.items():
            (case_dir / "system").mkdir(parents=True, exist_ok=True)
            (case_dir / "system" / name).write_text(content)
        for rel_path, content in self.extra.items():
            p = case_dir / rel_path
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)


def apply_file_overrides(case_dir: Path, overrides: dict[str, str]) -> list[str]:
    """Apply direct file content patches emitted by the agent to target case files.

    Patches are whole-file overrides: each key is a case-relative file path whose
    content replaces the whole file. A nested-key override (e.g. an LLM that
    expresses a *section* of `system/fvSolution` as `"system/fvSolution/solvers"`)
    has a parent path that is already an existing file and cannot be made a
    directory - skip it rather than crash the graph with NotADirectoryError.
    Returns the list of skipped (rejected) override paths for logging.
    """
    skipped: list[str] = []
    for rel_path, content in overrides.items():
        if not rel_path or not content:
            continue
        p = case_dir / rel_path
        # A parent that exists as a file means the key is nested below a real
        # file - not a valid whole-file override in this scheme.
        parent = p.parent
        if parent.exists() and not parent.is_dir():
            skipped.append(rel_path)
            continue
        try:
            parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)
        except (NotADirectoryError, OSError):
            skipped.append(rel_path)
            continue
    return skipped
