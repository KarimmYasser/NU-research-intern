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
_PRIMARY_TEMPLATE_SNIPPET_CHARS = 1_200
_PRIMARY_TEMPLATE_SNIPPETS = (
    "physicalProperties",
    "momentumTransport",
    "fvSchemes",
    "fvSolution",
)
_FAMILY_SNIPPET_CHARS = 800
_PROMPT_CONTEXT_MAX_CHARS = 12_000


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
        self.tutorials = self._load_tutorials()

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

    def tutorial_provenance(self) -> dict[str, Any]:
        """Return index provenance without exposing mutable tutorial records."""
        data = self._load_json("v13_tutorials.json")
        provenance = data.get("provenance", {}) if isinstance(data, dict) else {}
        return dict(provenance) if isinstance(provenance, dict) else {}

    @staticmethod
    def _tutorial_identifier(tutorial: dict[str, Any]) -> str:
        """Use the source-relative path because tutorial names are not unique."""
        return str(tutorial.get("relative_path") or tutorial.get("case_name") or "unknown")

    def _query_words(self, query: str) -> set[str]:
        return {w for w in set(re.findall(r"\w+", query.lower())) if w not in _STOP_WORDS}

    @staticmethod
    def _case_name_tokens(case_name: str) -> list[str]:
        """Split OpenFOAM camel-case tutorial names into normalized words."""
        return [token.lower() for token in re.findall(r"[A-Z]?[a-z]+|\d+", case_name)]

    @staticmethod
    def _phrase_match_score(query: str, case_tokens: list[str]) -> float:
        """Reward ordered multi-word requests matching a tutorial case name.

        Word overlap alone cannot distinguish ``dam break`` from a prompt whose
        words happen to include ``dam`` and ``break`` in unrelated contexts.
        Case names are tokenized from their camel-case spelling (``damBreak``),
        then compared with contiguous normalized query phrases.
        """
        query_tokens = re.findall(r"[a-z]+|\d+", query.lower())
        longest = 0
        for size in range(2, min(len(case_tokens), len(query_tokens)) + 1):
            phrases = {tuple(query_tokens[i:i + size]) for i in range(len(query_tokens) - size + 1)}
            case_phrases = {
                tuple(case_tokens[i:i + size]) for i in range(len(case_tokens) - size + 1)
            }
            if phrases & case_phrases:
                longest = size
        return 12.0 * longest

    def _record_score(self, item: dict[str, Any], words: set[str]) -> float:
        # Underscores/hyphens are part of ids ("thermal_buoyant"); normalise
        # them to spaces so word-boundary matching sees "buoyant" inside it.
        text = re.sub(r"[_-]", " ", json.dumps(item).lower())
        tokens = set(re.findall(r"[a-z]+", text))
        # Match on word prefixes: "exchanger" hits "exchangers". But keep
        # word-boundary semantics so "compress" never matches "incompressible".
        return sum(2.0 for w in words
                   if any(t == w or (len(w) > 4 and t.startswith(w)) for t in tokens))

    @staticmethod
    def _requested_physics(query: str) -> set[str]:
        """Infer coarse retrieval constraints from user wording, not case state.

        These are ranking signals only.  They never select a renderer case type
        and therefore cannot bypass CaseIntent validation or deterministic spec
        expansion.
        """
        text = query.lower()
        requested: set[str] = set()
        cues = {
            "steady": ("steady", "stationary"),
            "transient": ("transient", "unsteady", "time dependent", "time-dependent"),
            "turbulent": ("turbulent", "rans", "les", "turbulence"),
            "thermal": ("thermal", "temperature", "heat", "buoyant", "convection", "radiation"),
            "species": ("species", "combustion", "reacting", "flame", "mixture"),
            "dynamic_mesh": ("dynamic mesh", "moving mesh", "6dof", "six dof", "overset", "morphing"),
            "multi_region": ("multi-region", "multiregion", "conjugate heat", "cht", "solid region"),
            "multiphase": ("multiphase", "two phase", "two-phase", "vof", "free surface", "dam break", "bubble"),
        }
        for feature, terms in cues.items():
            if any(term in text for term in terms):
                requested.add(feature)
        if "laminar" in text:
            requested.add("laminar")
        return requested

    @staticmethod
    def _physics_compatibility(tutorial: dict[str, Any], requested: set[str]) -> float:
        """Score structural compatibility; absent user constraints remain neutral."""
        features = set(tutorial.get("physics_features") or [])
        fields = tutorial.get("fields") or []
        constants = (tutorial.get("file_tree") or {}).get("constant", [])
        if not features:  # Backward-compatible for an index not yet rebuilt.
            if "dynamicMeshDict" in constants:
                features.add("dynamic_mesh")
            if "phaseProperties" in constants or any(str(f).startswith("alpha.") for f in fields):
                features.add("multiphase")
            if "T" in fields or any(str(f).startswith("T.") for f in fields):
                features.add("thermal")
            if any(str(f).startswith(("Y", "Xi", "Tu")) for f in fields):
                features.add("species")
        score = 0.0
        for feature in requested:
            if feature == "laminar":
                score += 3.0 if "turbulent" not in features else -2.0
            elif feature in {"steady", "transient"}:
                score += 4.0 if feature in features else -1.0
            else:
                score += 8.0 if feature in features else -3.0
        return score

    def _match_tutorials(self, solver: str, flow_type: str | None, words: set[str], query: str,
                         top_k: int = 3, family_ids: set[str] | None = None,
                         primary_family: str | None = None) -> list[dict[str, Any]]:
        """Rank tutorial cases: family membership first (the semantic layer),
        with the *primary* matched family ranked above secondary ones, then
        (solver, flow) exactness, then keyword overlap.
        """
        tutorials = self.tutorials
        target_solver = (solver or "").lower()
        target_flow = (flow_type or "").lower()
        requested_physics = self._requested_physics(query)

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
            # Case names are often camel-cased (damBreak, obliqueShock). Split
            # them so a user asking for the same named phenomenon gets that
            # concrete tutorial ahead of a generic same-family reference.
            case_token_list = self._case_name_tokens(t.get("case_name") or "")
            case_tokens = set(case_token_list)
            exact = 10.0 * len(case_tokens & {w for w in words if len(w) > 3})
            phrase = self._phrase_match_score(query, case_token_list)
            kw = self._record_score(t, words)
            compatibility = self._physics_compatibility(t, requested_physics)
            return (family_bonus + exact + phrase + solver_bonus + flow_bonus + compatibility + kw,
                    t_solver == target_solver)

        # Sort the stable source identifier first, then score descending. This
        # keeps ties reproducible without reversing the lexical path order.
        ranked = sorted(tutorials, key=self._tutorial_identifier)
        ranked.sort(key=rank, reverse=True)
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
            for t in self.tutorials:
                if t.get("family_id") == primary_family:
                    if t.get("solver"):
                        fam_solvers[t["solver"]] += 1
                    if t.get("flow_type"):
                        fam_flows[t["flow_type"]] += 1
            fam_solver = fam_solvers.most_common(1)[0][0] if fam_solvers else None
            # `fluid` is the broad, renderer-supported representative for the
            # generic compressible families.  Their tutorial membership also
            # contains specialised shockFluid cases; selecting the most common
            # module would make a generic external-aerodynamics prompt retrieve
            # shock-tube references instead of the matching fluid aerofoil/wedge
            # cases. Promote shockFluid only when the user names shock physics.
            if (primary_family in {"flow_compressible_internal", "flow_compressible_external"}
                    and "shock" not in words and "supersonic" not in words
                    and "hypersonic" not in words):
                fam_solver = "fluid"
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
                                      words, query, top_k=top_cases, family_ids=family_str,
                                      primary_family=primary_family)
        primary_template = cases[0] if cases else None
        template_contract = self._template_contract(primary_template)

        # Whole role-level BC records complement the primary tutorial's populated
        # patch_boundary_types reference metadata.
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
            "primary_template": primary_template,
            "template_contract": template_contract,
            "boundary_conditions": bc_rows,
            "ground": ground,
        }

    def _template_contract(self, template: dict[str, Any] | None) -> dict[str, Any]:
        """Return structural evidence that may inform, but never drive, rendering."""
        if not template:
            return {}
        return {
            "source": f"tutorials/{self._tutorial_identifier(template)}",
            "solver": template.get("solver"),
            "fields": list(template.get("fields") or []),
            "file_tree": dict(template.get("file_tree") or {}),
            "physics_features": list(template.get("physics_features") or []),
            "patch_boundary_types": dict(template.get("patch_boundary_types") or {}),
            "provenance": self.tutorial_provenance(),
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
        tutorials = self.tutorials

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

    def _format_primary_template(self, template: dict[str, Any] | None) -> str:
        """Render one bounded, source-addressable tutorial as the case blueprint."""
        if not template:
            return (
                "\n### Primary OpenFOAM v13 Tutorial Template\n"
                "No local tutorial template is available. Use only the validated "
                "solver and boundary-condition registries."
            )

        lines = [
            "\n### Primary OpenFOAM v13 Tutorial Template",
            f"Source: tutorials/{self._tutorial_identifier(template)}",
            f"Case: {template.get('case_name', 'unknown')} "
            f"(solver={template.get('solver')}, flow={template.get('flow_type')})",
        ]
        if template.get("family_name"):
            lines.append(f"Family: {template['family_name']}")
        for zone, files in (template.get("file_tree") or {}).items():
            lines.append(f"Structural files {zone}/: {', '.join(files)}")
        fields = template.get("fields") or []
        if fields:
            lines.append(f"Reference fields: {', '.join(fields)}")
        patch_types = template.get("patch_boundary_types") or {}
        if patch_types:
            patch_summary = []
            for patch_name in sorted(patch_types):
                field_types = patch_types[patch_name]
                if isinstance(field_types, dict):
                    details = ", ".join(
                        f"{field}={bc_type}" for field, bc_type in sorted(field_types.items())
                    )
                    patch_summary.append(f"{patch_name} ({details})")
            if patch_summary:
                lines.append("Reference boundary-patch structure: " + "; ".join(patch_summary))
        lines.append(
            "Use this tutorial as the structural starting point: preserve only its "
            "applicable dictionary/field structure, then adapt geometry, actual mesh "
            "patch names, boundary-condition values, and physical settings to the request. "
            "The reference patch names above are examples only: user-requested names and "
            "provided-mesh names are authoritative. "
            "Do not blindly copy its geometry, patch names, numerical values, optional "
            "files, or unsupported solver features; registry validation remains authoritative."
        )
        snippets = template.get("snippets") or {}
        for name in _PRIMARY_TEMPLATE_SNIPPETS:
            content = snippets.get(name)
            if isinstance(content, str) and content.strip():
                lines.append(f"Reference {name} excerpt:\n{content[:_PRIMARY_TEMPLATE_SNIPPET_CHARS]}")
        family_snippets = template.get("family_snippets") or {}
        for name, content in family_snippets.items():
            if isinstance(content, str) and content.strip():
                lines.append(
                    f"Reference specialised dictionary excerpt ({name}; evidence only, do not copy wholesale):\n"
                    f"{content[:_FAMILY_SNIPPET_CHARS]}"
                )
        return "\n".join(lines)

    @staticmethod
    def _apply_prompt_budget(context: str) -> str:
        """Keep all prompt grounding bounded deterministically at a safe boundary."""
        if len(context) <= _PROMPT_CONTEXT_MAX_CHARS:
            return context
        marker = "\n[Prompt grounding truncated deterministically; primary template retained above.]"
        budget = _PROMPT_CONTEXT_MAX_CHARS - len(marker)
        cut = context.rfind("\n", 0, budget)
        return context[:cut if cut > 0 else budget] + marker

    def format_prompt_context(self, query: str) -> str:
        """Format retrieved RAG knowledge into a markdown prompt grounding snippet.

        Every request receives one primary official v13 tutorial template. When
        the complexity heuristic fires, extra case-graph references are appended
        for comparison without replacing that primary template.
        """
        lines = ["\n--- OpenFOAM v13 Domain Knowledge & Guidelines ---"]
        retrieved = self.search(query)
        graph = self.retrieve_case_graph(query, top_cases=2)
        lines.append(self._format_primary_template(graph["primary_template"]))

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
            lines.append(graph["ground"].strip())

        return self._apply_prompt_budget("\n".join(lines))
