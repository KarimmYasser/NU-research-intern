"""Knowledge Base & RAG Engine for OpenFOAM v13 Case Generation.

Retrieves localized OpenFOAM v13 solver models, thermophysical property models,
and boundary condition rules to ground LLM intent extraction and IR generation.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

KNOWLEDGE_DIR = Path(__file__).resolve().parent


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

    def get_species_thermo(self, species_name: str | None) -> dict[str, Any] | None:
        """Lookup thermophysical properties for a species by name."""
        if not species_name:
            return None
        target = species_name.lower().strip()
        for item in self.thermophysics:
            if item.get("species") == target or item.get("model_name") == target:
                return item
        return None

    def get_tutorial_numerics(self, solver_name: str | None = None, flow_type: str | None = None) -> dict[str, str]:
        """Fetch fvSchemes and fvSolution snippets from matching v13 tutorial cases."""
        tutorials_file = self.knowledge_dir / "v13_tutorials.json"
        if not tutorials_file.is_file():
            return {}
        try:
            tut_data = json.loads(tutorials_file.read_text(encoding="utf-8"))
            tutorials = tut_data.get("tutorials", []) if isinstance(tut_data, dict) else tut_data
        except Exception:
            return {}

        target_solver = (solver_name or "").lower()
        target_flow = (flow_type or "").lower()

        best_match = None
        for tut in tutorials:
            tut_solver = tut.get("solver", "").lower()
            tut_flow = tut.get("flow_type", "").lower()
            snippets = tut.get("snippets", {})

            if snippets.get("fvSchemes") and snippets.get("fvSolution"):
                if target_solver in tut_solver and target_flow in tut_flow:
                    best_match = snippets
                    break
                elif target_flow in tut_flow and best_match is None:
                    best_match = snippets

        if best_match:
            return {
                "fvSchemes": best_match.get("fvSchemes", ""),
                "fvSolution": best_match.get("fvSolution", ""),
            }
        return {}

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

        if retrieved["thermophysics"]:
            lines.append("Available Fluid Species:")
            species_list = [t.get("species", t.get("model_name")) for t in self.thermophysics if "species" in t or "model_name" in t]
            lines.append(f"- {', '.join(species_list)}")

        return "\n".join(lines)
