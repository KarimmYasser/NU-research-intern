"""Knowledge Base & RAG Engine for OpenFOAM v13 Case Generation.

Retrieves localized OpenFOAM v13 solver models, thermophysical property models,
and boundary condition rules to ground LLM intent extraction and IR generation.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

KNOWLEDGE_DIR = Path(__file__).resolve().parents[1] / "knowledge"


class KnowledgeRetriever:
    """Zero-dependency local RAG retriever indexing OpenFOAM v13 knowledge."""

    def __init__(self, knowledge_dir: Path | None = None):
        self.knowledge_dir = knowledge_dir or KNOWLEDGE_DIR
        self.solvers = self._load_json("solvers.json").get("solvers", [])
        self.boundary_conditions = self._load_json("boundary_conditions.json").get("boundary_conditions", [])
        self.thermophysics = self._load_json("thermophysics.json").get("thermophysics", [])

    def _load_json(self, filename: str) -> dict[str, Any]:
        path = self.knowledge_dir / filename
        if not path.is_file():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def search(self, query: str, top_k: int = 3) -> dict[str, list[dict[str, Any]]]:
        """Search knowledge base for items relevant to the natural language prompt."""
        words = set(re.findall(r"\w+", query.lower()))

        def score(item: dict[str, Any]) -> float:
            text = json.dumps(item).lower()
            return sum(2.0 if word in text else 0.0 for word in words)

        matched_solvers = sorted(self.solvers, key=score, reverse=True)[:top_k]
        matched_bcs = sorted(self.boundary_conditions, key=score, reverse=True)[:top_k]
        matched_thermo = sorted(self.thermophysics, key=score, reverse=True)[:top_k]

        return {
            "solvers": [s for s in matched_solvers if score(s) > 0] or self.solvers[:1],
            "boundary_conditions": [b for b in matched_bcs if score(b) > 0] or self.boundary_conditions[:3],
            "thermophysics": [t for t in matched_thermo if score(t) > 0] or self.thermophysics[:1],
        }

    def format_prompt_context(self, query: str) -> str:
        """Format retrieved RAG knowledge into a markdown prompt grounding snippet."""
        retrieved = self.search(query)
        lines = ["\n--- OpenFOAM v13 Domain Knowledge & Guidelines ---"]

        if retrieved["solvers"]:
            lines.append("Recommended Solvers:")
            for s in retrieved["solvers"]:
                lines.append(f"- {s['name']} ({s['flow_type']}): {s['description']}")

        if retrieved["boundary_conditions"]:
            lines.append("Boundary Condition Guidance:")
            for bc in retrieved["boundary_conditions"]:
                lines.append(f"- role {bc['role']} ({bc['patch_type']}): {bc['description']} ({bc.get('notes', '')})")

        return "\n".join(lines)
