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

    File names follow OpenFOAM v13 conventions (see docs/openfoam/case-structure.md):
    constant/physicalProperties and constant/momentumTransport, NOT the older
    transportProperties/turbulenceProperties used in most pre-v13 tutorials and
    in the LLM+CFD papers in the literature review (temp/paper-research-agentic-cfd-llm/).
    """

    zero: dict[str, str] = field(default_factory=dict)  # e.g. "U", "p" -> file content
    constant: dict[str, str] = field(default_factory=dict)  # e.g. "physicalProperties"
    system: dict[str, str] = field(default_factory=dict)  # e.g. "blockMeshDict", "controlDict"

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
