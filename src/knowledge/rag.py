"""Knowledge Base & RAG Engine for OpenFOAM v13 Case Generation.

Retrieves localized OpenFOAM v13 solver models, thermophysical property models,
and boundary condition rules to ground LLM intent extraction and IR generation.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

KNOWLEDGE_DIR = Path(__file__).resolve().parent


_STOP_WORDS = {
    "a", "an", "the", "of", "for", "and", "or", "in", "on", "at", "over",
    "with", "from", "into", "through", "is", "are", "be", "to", "flow", "flows",
    "using", "used", "can", "so", "what", "how", "gas", "air", "simulation",
    "simulate", "want", "need", "specific", "please",
}


class KnowledgeRetriever:
    """Zero-dependency local RAG retriever indexing OpenFOAM v13 knowledge."""

    def __init__(self, knowledge_dir: Path | None = None):
        self.knowledge_dir = knowledge_dir or KNOWLEDGE_DIR
        self.solvers = self._load_json("solvers.json").get("solvers", [])
        self.boundary_conditions = self._load_json("boundary_conditions.json").get("boundary_conditions", [])
        self.thermophysics = self._load_json("thermophysics.json").get("thermophysics", [])
        self.families = self._load_json("case_families.json").get("families", [])

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

    # ------------------------------------------------------------------
    # Chunkless RAG: case-graph traversal across structured records.
    #
    # The registries are coherent structural units (whole solvers, whole
    # tutorial cases), so "retrieval" is a graph walk — query -> solver ->
    # its tutorial cases -> each case's file tree + fields + snippets — not
    # chunked-text similarity. The walk is deterministic (no extra LLM calls),
    # which keeps the chunkless approach cheap on the fast path.
    # ------------------------------------------------------------------

    def _load_tutorials(self) -> list[dict[str, Any]]:
        tutorials_file = self.knowledge_dir / "v13_tutorials.json"
        if not tutorials_file.is_file():
            return []
        try:
            tut_data = json.loads(tutorials_file.read_text(encoding="utf-8"))
            return tut_data.get("tutorials", []) if isinstance(tut_data, dict) else tut_data
        except Exception:
            return []

    def _query_words(self, query: str) -> set[str]:
        return {w for w in set(re.findall(r"\w+", query.lower())) if w not in _STOP_WORDS}

    def _record_score(self, item: dict[str, Any], words: set[str]) -> float:
        # Underscores/hyphens are part of ids ("thermal_buoyant"); normalise
        # them to spaces so word-boundary matching sees "buoyant" inside it.
        text = re.sub(r"[_-]", " ", json.dumps(item).lower())
        tokens = set(re.findall(r"[a-z]+", text))
        # Match on word prefixes: "exchanger" hits "exchangers". But keep
        # word-boundary semantics so "compress" never matches "incompressible".
        return sum(2.0 for w in words
                   if any(t == w or (len(w) > 4 and t.startswith(w)) for t in tokens))

    def _match_tutorials(self, solver: str, flow_type: str | None, words: set[str],
                         top_k: int = 3, family_ids: set[str] | None = None,
                         primary_family: str | None = None) -> list[dict[str, Any]]:
        """Rank tutorial cases: family membership first (the semantic layer),
        with the *primary* matched family ranked above secondary ones, then
        (solver, flow) exactness, then keyword overlap.
        """
        tutorials = self._load_tutorials()
        target_solver = (solver or "").lower()
        target_flow = (flow_type or "").lower()

        def rank(t: dict[str, Any]) -> tuple:
            t_solver = (t.get("solver") or "").lower()
            t_flow = (t.get("flow_type") or "").lower()
            fid = t.get("family_id")
            # Primary family membership dominates the exact case-name match:
            # "column" in the query must not outrank a same-family Euler bubble
            # column just because a dense-particle case happens to share a name.
            family_bonus = (20.0 if primary_family and fid == primary_family
                            else 6.0 if family_ids and fid in family_ids else 0.0)
            solver_bonus = 3.0 if target_solver and t_solver == target_solver else 0.0
            flow_bonus = 1.0 if target_flow and t_flow == target_flow else 0.0
            exact = 10.0 if t.get("case_name") and t["case_name"].lower() in {
                w for w in words if len(w) > 3} else 0.0
            kw = self._record_score(t, words)
            return (family_bonus + exact + solver_bonus + flow_bonus + kw,
                    t_solver == target_solver)

        ranked = sorted(tutorials, key=rank, reverse=True)
        return [t for t in ranked if rank(t)[0] > 0][:top_k] or ranked[:top_k]

    def _match_families(self, words: set[str], top_k: int = 2) -> list[dict[str, Any]]:
        """Score the semantic case families against the query by their *human
        description* (not case names) — this is where chunkless hierarchy lives:
        a vortex-tube prompt matches the compressible-internal family description
        even though no v13 tutorial is a literal vortex tube.

        Families are ranked by (matched query words, description-weighted score)
        so a family that shares several distinct physics words with the prompt
        outranks one that shares one word with its id or an include cue.
        """
        def fields(f: dict[str, Any]) -> tuple[int, float]:
            base = 2.0
            desc_weight = 4.0  # description carries the semantic signal
            desc_tokens = set(re.findall(r"[a-z]+", re.sub(r"[_-]", " ", f.get("description", "").lower())))
            hit_words = {w for w in words
                         if any(t == w or (len(w) > 4 and t.startswith(w)) for t in desc_tokens)}
            hit_score = sum(desc_weight for _ in hit_words)
            hit_score += sum(base for w in words
                             if any(t == w or (len(w) > 4 and t.startswith(w)) for t in
                                    set(re.findall(r"[a-z]+", re.sub(r"[_-]", " ", json.dumps(f).lower())))))
            return (len(hit_words), hit_score)

        ranked = sorted(self.families, key=fields, reverse=True)
        # Keep the top match even at zero hits only when nothing else exists.
        if ranked and fields(ranked[0])[1] > 0:
            return ranked[:top_k]
        return []

    def retrieve_case_graph(self, query: str, top_cases: int = 2,
                            solver: str | None = None,
                            flow_type: str | None = None) -> dict[str, Any]:
        """Chunkless RAG deep path: walk the knowledge graph and return the
        linked, structurally-coherent records for *query*.

        The walk is *query → semantic family (matched on the family's human
        description) → member tutorial cases*. Families give the hierarchy the
        agent reasons over; cases give concrete structure to reuse.

        Returns:
            solver    — the recommended solver record (whole record, not a snippet)
            families  — matched semantic families (id, name, description)
            cases     — member tutorial cases, each with case_name, family_name,
                        fields, description, file_tree and its captured snippets
            boundary_conditions — whole BC records for grounding
            ground    — a prompt-ready markdown block that explains *why* each
                        family/case was chosen (what the reference case is, what
                        it ships, and how to reuse it structurally)
        """
        words = self._query_words(query)
        families = self._match_families(words, top_k=2)
        family_ids = {f["id"] for f in families}
        primary_family = families[0]["id"] if families else None

        # Solver: if a semantic family matched, prefer the solver its member
        # cases actually run (the family is the stronger signal than keywords —
        # "vortex tube" names no solver but its family is compressible/fluid).
        if solver:
            matched = [s for s in self.solvers if s.get("name", "").lower() == solver.lower()]
            top_solver = matched[0] if matched else self.solvers[0]
        elif primary_family:
            fam_solvers: Counter[str] = Counter()
            fam_flows: Counter[str] = Counter()
            for t in self._load_tutorials():
                if t.get("family_id") == primary_family:
                    if t.get("solver"):
                        fam_solvers[t["solver"]] += 1
                    if t.get("flow_type"):
                        fam_flows[t["flow_type"]] += 1
            fam_solver = fam_solvers.most_common(1)[0][0] if fam_solvers else None
            hit = [s for s in self.solvers if s.get("name", "") == fam_solver]
            if hit:
                top_solver = hit[0]
            elif fam_flows:
                # Family solver not in the registry (e.g. shockFluid): choose the
                # registry solver whose flow_type matches the family's majority.
                dom_flow = fam_flows.most_common(1)[0][0]
                pref = [s for s in self.solvers if s.get("flow_type") == dom_flow]
                top_solver = pref[0] if pref else max(self.solvers, key=lambda s: self._record_score(s, words))
            else:
                top_solver = max(self.solvers, key=lambda s: self._record_score(s, words))
        else:
            top_solver = max(self.solvers, key=lambda s: self._record_score(s, words))

        family_str = family_ids or {None}

        cases = self._match_tutorials(top_solver.get("name", ""), flow_type or top_solver.get("flow_type"),
                                      words, top_k=top_cases, family_ids=family_str,
                                      primary_family=primary_family)

        # Whole BC records as grounding (the patch_boundary_types field in the
        # tutorial index is unpopulated, so fall back to role-level guidance).
        bc_rows = self.boundary_conditions[:4]

        lines = ["\n--- OpenFOAM Reference Case (chunkless graph retrieval) ---"]

        lines.append(f"Recommended solver: {top_solver.get('name')} ({top_solver.get('flow_type')}) "
                     f"— {top_solver.get('description', '')}")
        req = top_solver.get("required_fields")
        if req:
            lines.append(f"Required fields: {', '.join(req)}")

        if families:
            lines.append("Matched case family (reasoned by description, not word matches):")
            for f in families:
                lines.append(f"- {f['name']}: {f['description']}")
        else:
            lines.append("No semantic family matched; showing nearest solver cases.")

        for c in cases:
            lines.append(f"- Reference case: {c.get('relative_path')} "
                         f"(solver={c.get('solver')}, flow={c.get('flow_type')}, "
                         f"family={c.get('family_name')})")
            desc = c.get("description")
            if desc:
                lines.append(f"  {desc}")
            ft = c.get("file_tree") or {}
            for zone, files in ft.items():
                optional = [f for f in files if f not in {
                    "U", "p", "T", "k", "epsilon", "omega", "nut", "alphat", "R",
                    "controlDict", "fvSchemes", "fvSolution", "physicalProperties",
                    "momentumTransport", "thermophysicalProperties", "g",
                }]
                note = f"  (optional: {', '.join(optional)})" if optional else ""
                lines.append(f"  {zone}/: {', '.join(files)}{note}")
            fields = c.get("fields")
            if fields:
                lines.append(f"  fields: {', '.join(fields)}")

        if bc_rows:
            lines.append("Reference-case boundary-condition guidance:")
            for b in bc_rows:
                lines.append(f"- {b.get('role')}/{b.get('patch_type')}: {b.get('description')}")

        ground = "\n".join(lines)
        return {
            "solver": top_solver,
            "families": families,
            "cases": cases,
            "boundary_conditions": bc_rows,
            "ground": ground,
        }

    def needs_deep_path(self, query: str) -> bool:
        """Cheap heuristic: does this prompt need the structure-aware deep path?

        Fires on physics-specific vocabulary (compressible/thermal/multiphase
        phenomena, case families) that the keyword fast path can't capture by
        flat similarity. Kept deliberately small — the deep path is free
        (deterministic), but it does lengthen the prompt.
        """
        text = query.lower()
        if any(m in text for m in ["vortex", "tube", "shock", "nozzle", "supersonic",
                                   "transonic", "multiphase", "vo f", "phase", "film",
                                   "droplet", "spray", "species", "combustion",
                                   "buoyant", "conjugate", "particle", "drift",
                                   "cavita", "boil", "solid", "swirl", "thermal"]):
            return True
        # Two+ generic physics words also qualifies (expanded vocabulary only).
        words = self._query_words(query)
        hits = sum(1 for w in words if w in {"compress", "thermal", "phase",
                                             "multi", "heat", "velocity", "pressure",
                                             "high", "gas", "shock", "swirl"})
        return hits >= 2

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
        """Format retrieved RAG knowledge into a markdown prompt grounding snippet.

        Hybrid: the cheap keyword path always runs; when the complexity heuristic
        fires, the chunkless case-graph retrieval (whole reference cases + their
        file trees) is appended so the LLM sees the structure of a matching v13
        tutorial case instead of flat snippets.
        """
        lines = ["\n--- OpenFOAM v13 Domain Knowledge & Guidelines ---"]
        retrieved = self.search(query)

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

        if self.needs_deep_path(query):
            lines.append(self.retrieve_case_graph(query)["ground"].strip())

        return "\n".join(lines)
