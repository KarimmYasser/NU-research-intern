#!/usr/bin/env python3
"""Regenerate src/knowledge/v13_tutorials.json from the local OpenFOAM-13 repo.

The JSON drives the RAG file-tree retrieval (retrieve_case_graph): each tutorial
record carries the case's *structure* — the solver, flow type, field set, and a
`file_tree` listing every solver-relevant file under 0/, constant/ and system/
(optional files like system/fvConstraints and system/functions included) plus the
captured fvSchemes/fvSolution/physicalProperties snippets. Keeping the tree in
the JSON (not re-walking the repo at runtime) keeps KnowledgeRetriever a pure
read of data, mirroring every other registry.

Run from repo root:
    python src/knowledge/rebuild_tutorials.py

Only rewrites records whose relative_path still resolves in the repo; unknown
cases are preserved as-is.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
TUTORIALS_ROOT = REPO_ROOT / "temp" / "openfoam-repo" / "OpenFOAM-13" / "tutorials"
KNOWLEDGE = Path(__file__).resolve().parent
TARGET = KNOWLEDGE / "v13_tutorials.json"

# case-relative paths that are never solver input (mesh is copied separately;
# Allrun/Allclean are scripts, not dictionaries).
_SKIP_DIRS = {"polyMesh"}
_SKIP_FILES = {
    "Allrun", "Allclean", "Alltest", "README", "README.md",
    "blockMeshDict", "surfaceFeatureExtractDict", "snappyHexMeshDict",
    "extrudeMeshDict", "cartesianMeshDict", "rotateMeshDict", "setSet",
}
_SKIP_SUFFIX = (".orig", ".gz", ".png", ".stl", ".obj", ".md")


def _case_file_tree(case_dir: Path) -> dict[str, list[str]]:
    """Map 0/constant/system -> sorted solver-relevant file names.

    Each entry records files under the standard case dirs, excluding the mesh
    (copied from the provided mesh, not generated), mesh-generation dicts, and
    script/README noise. Field files (.orig variants, e.g. T.orig) are kept with
    their suffix stripped so the tree shows the canonical name OpenFOAM reads.
    """
    tree: dict[str, list[str]] = {}
    for top in ("0", "constant", "system"):
        top_dir = case_dir / top
        if not top_dir.is_dir():
            continue
        names: set[str] = set()
        for p in top_dir.rglob("*"):
            if p.is_dir() or p.name in _SKIP_FILES or p.name.endswith(_SKIP_SUFFIX):
                continue
            if p.name == "boundary" or any(part in _SKIP_DIRS for part in p.relative_to(case_dir).parts):
                continue
            name = re.sub(r"\.orig$", "", p.name)
            names.add(name)
        if names:
            tree[top] = sorted(names)
    return tree


def _assign_family(case_name: str, category: str) -> str | None:
    """Assign a semantic family from src/knowledge/case_families.json.

    Precedence (high -> low):
      1. An explicit (category, case) override for genuinely ambiguous names.
      2. The longest include_name_cue matched in the case name (specificity
         beats category base: "aerofoil"-in-a-compressible-case must not fall to
         the generic "naca" cue for incompressible external flow).
      3. The base_categories mapping (category -> family), unless the name
         matches an exclude cue for that family.
    Returns the family id, or None when nothing matched.
    """
    # Rare genuinely ambiguous names: (category, case) -> family.
    _OVERRIDES = {
        ("incompressibleFluid", "movingCone"): "moving_mesh",  # dynamic-mesh cone
        ("shockFluid", "movingCone"): "flow_compressible_external",  # hypersonic cone
    }
    ov = _OVERRIDES.get((category, case_name))
    if ov:
        return ov

    families_file = REPO_ROOT / "src" / "knowledge" / "case_families.json"
    try:
        families = json.loads(families_file.read_text(encoding="utf-8")).get("families", [])
    except Exception:
        return None

    name_l = case_name.lower()

    # 2. include cues: longest matched cue wins across all families (ties break
    #    to the earlier family in the taxonomy file, so order expresses priority).
    best: tuple[int, int, str | None] = (-1, -1, None)
    for i, fam in enumerate(families):
        for cue in fam.get("include_name_cues", []):
            cue_l = cue.lower()
            if cue_l in name_l and len(cue_l) > best[0]:
                best = (len(cue_l), -i, fam["id"])
    if best[2] is not None:
        return best[2]

    # 3. category base with exclude-cue veto.
    candidates = [f for f in families if category in f.get("base_categories", [])]
    for fam in candidates:
        if any(cue.lower() in name_l for cue in fam.get("exclude_name_cues", [])):
            continue
        return fam["id"]
    return candidates[0]["id"] if candidates else None


def _describe_case(tut: dict[str, Any], family_name: str) -> str:
    """One-line semantic description derived from the case structure.

    Not prose — a compact, factual account of what the case is (flow type,
    solver, field set settled by the energy/phase kinds present, and any
    notable optional system/ dictionaries). The LLM reads this plus the
    file_tree to judge relevance against a new prompt.
    """
    parts = [f"{tut.get('flow_type', '')} {family_name} tutorial"]
    solver = tut.get("solver")
    if solver:
        parts.append(f"(solver {solver})")
    fields = [f for f in (tut.get("fields") or []) if not f.endswith(".orig")]
    if fields:
        core = [f for f in fields if f in {"U", "p", "p_rgh", "T", "h", "e", "rho"}]
        extra = [f for f in fields if f not in {"U", "p", "p_rgh", "T", "h", "e", "rho"}]
        desc = "fields " + ", ".join(core or fields[:3])
        if extra:
            desc += " (+ " + ", ".join(extra[:6]) + ")"
        parts.append(desc)
    ft = tut.get("file_tree") or {}
    optional = sorted(
        f for f in ft.get("system", [])
        if f not in {"controlDict", "fvSchemes", "fvSolution", "blockMeshDict"}
    )
    if optional:
        parts.append("ships " + ", ".join(optional))
    return "; ".join(parts).strip()


def rebuild() -> None:
    data = json.loads(TARGET.read_text(encoding="utf-8"))
    tutorials = data["tutorials"] if isinstance(data, dict) else data
    families_file = REPO_ROOT / "src" / "knowledge" / "case_families.json"
    try:
        family_names = {f["id"]: f["name"] for f in json.loads(families_file.read_text(encoding="utf-8"))["families"]}
    except Exception:
        family_names = {}
    updated = 0
    for tut in tutorials:
        rel = tut.get("relative_path")
        if not rel:
            continue
        case_dir = TUTORIALS_ROOT / rel
        if not case_dir.is_dir():
            continue  # preserve unknown cases unchanged
        tree = _case_file_tree(case_dir)
        if tree:
            tut["file_tree"] = tree
            updated += 1
        family = _assign_family(tut.get("case_name", ""), tut.get("category", ""))
        if family:
            tut["family_id"] = family
            tut["family_name"] = family_names.get(family, family)
        tut["description"] = _describe_case(tut, tut.get("family_name", ""))
    out = {"tutorials": tutorials} if isinstance(data, dict) else tutorials
    TARGET.write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote file trees + family ids + descriptions for {updated} tutorial cases to {TARGET}")


if __name__ == "__main__":
    rebuild()
