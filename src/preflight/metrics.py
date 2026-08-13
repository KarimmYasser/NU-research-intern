"""Level B preflight: quantitative checkMesh metrics parser.

Pulls structured mesh-quality numbers out of a checkMesh stdout dump so the
preflight report says *how* bad a mesh is, not just that it failed. Thresholds
follow OpenFOAM's own defaults (checkMesh marks `***` on non-orthogonality > 70
and skewness > 4; cells with negative/zero volume always fail).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# OpenFOAM's own quality warnings (from checkMesh source).
NON_ORTHO_WARN = 70.0
SKEW_WARN = 4.0
ASPECT_WARN = 1000.0


@dataclass
class MeshMetrics:
    cells: int | None = None
    non_orthogonality_max: float | None = None
    non_orthogonality_avg: float | None = None
    max_skewness: float | None = None
    max_aspect_ratio: float | None = None
    min_volume: float | None = None
    max_volume: float | None = None
    total_volume: float | None = None
    min_face_area: float | None = None
    max_face_area: float | None = None
    failed_checks: int = 0  # "Failed N mesh checks."
    ok: bool = True  # "Mesh OK." present (checkMesh exit-status equivalent)
    raw: str = ""

    def as_dict(self) -> dict:
        return {f.name: getattr(self, f.name) for f in field(self)}

    def summarize(self) -> str:
        parts = [f"cells={self.cells or '?'}"]
        if self.non_orthogonality_max is not None:
            parts.append(f"non-ortho(max={self.non_orthogonality_max:g}, avg={self.non_orthogonality_avg:g})"
                         if self.non_orthogonality_avg is not None
                         else f"non-ortho(max={self.non_orthogonality_max:g})")
        if self.max_skewness is not None:
            parts.append(f"skew={self.max_skewness:g}")
        if self.max_aspect_ratio is not None:
            parts.append(f"aspect={self.max_aspect_ratio:g}")
        if self.min_volume is not None:
            parts.append(f"V=[{self.min_volume:g}, {self.max_volume:g}]")
        return " ".join(parts)


_NUM = r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?"


def parse_checkmesh_metrics(stdout: str) -> MeshMetrics:
    """Parse a checkMesh stdout dump into MeshMetrics. Tolerant: any number we
    cannot find stays None rather than crashing the preflight."""
    m = MeshMetrics(raw=stdout)

    def _num(pattern: str, group: int = 1) -> float | None:
        hit = re.search(pattern, stdout)
        try:
            return float(hit.group(group)) if hit else None
        except (ValueError, IndexError):
            return None

    hit = re.search(r"cells:\s*(\d+)", stdout)
    if hit:
        m.cells = int(hit.group(1))

    # v13 style: `Mesh non-orthogonality Max: 41.26 average: 4.97`
    hit = re.search(r"non-orthogonality\s+Max:\s*(" + _NUM + r")\s+average:\s*(" + _NUM + r")", stdout)
    if hit:
        m.non_orthogonality_max = float(hit.group(1))
        m.non_orthogonality_avg = float(hit.group(2))
    else:  # older style: `Maximum non-orthogonality = 41.26`
        hit = re.search(r"Maximum non-orthogonality\s*(?:=|:)\s*(" + _NUM + r")", stdout)
        if hit:
            m.non_orthogonality_max = float(hit.group(1))

    m.max_skewness = _num(r"Max skewness\s*=\s*(" + _NUM + r")\b")
    m.max_aspect_ratio = _num(r"Max aspect ratio\s*=\s*(" + _NUM + r")\b")
    m.min_volume = _num(r"Min volume\s*=\s*(" + _NUM + r")\b")
    m.max_volume = _num(r"Max volume\s*=\s*(" + _NUM + r")\b")
    m.total_volume = _num(r"Total volume\s*=\s*(" + _NUM + r")\b")
    m.min_face_area = _num(r"Minimum face area\s*=\s*(" + _NUM + r")\b")
    m.max_face_area = _num(r"Maximum face area\s*=\s*(" + _NUM + r")\b")

    failed = re.search(r"Failed\s+(\d+)\s+mesh checks", stdout)
    m.failed_checks = int(failed.group(1)) if failed else 0
    m.ok = "Mesh OK." in stdout and m.failed_checks == 0
    return m


def evaluate_mesh_metrics(m: MeshMetrics) -> list[tuple[str, str, str]]:
    """Return (code, level, message) rows for the metrics vs OpenFOAM thresholds.

    Levels: pass / warn / fail. A cell-volume ≤ 0 or a failed check summary is a
    hard fail; non-orthogonality and skewness degrade from warn to fail above
    2x the OpenFOAM-warn threshold.
    """
    rows: list[tuple[str, str, str]] = []
    if m.failed_checks > 0:
        rows.append(("checkmesh_failed", "fail",
                     f"checkMesh reports {m.failed_checks} failed check(s)"))
    elif not m.ok:
        rows.append(("checkmesh_no_ok", "warn", "checkMesh output lacks 'Mesh OK.' summary"))
    else:
        rows.append(("checkmesh_ok", "pass", "checkMesh: Mesh OK."))

    if m.min_volume is not None and m.min_volume <= 0:
        rows.append(("nonpositive_cell_volume", "fail",
                     f"min cell volume = {m.min_volume:g} (≤ 0) — degenerate/negative cells"))

    if m.non_orthogonality_max is not None:
        if m.non_orthogonality_max > 2 * NON_ORTHO_WARN:
            rows.append(("non_ortho_fail", "fail",
                         f"max non-orthogonality {m.non_orthogonality_max:g}° (OpenFOAM warns > {NON_ORTHO_WARN:g})"))
        elif m.non_orthogonality_max > NON_ORTHO_WARN:
            rows.append(("non_ortho_warn", "warn",
                         f"max non-orthogonality {m.non_orthogonality_max:g}° — needs nNonOrthogonalCorrectors"))
        else:
            rows.append(("non_ortho_ok", "pass",
                         f"max non-orthogonality {m.non_orthogonality_max:g}° ≤ {NON_ORTHO_WARN:g}"))

    if m.max_skewness is not None:
        if m.max_skewness > 2 * SKEW_WARN:
            rows.append(("skew_fail", "fail",
                         f"max skewness {m.max_skewness:g} (OpenFOAM warns > {SKEW_WARN:g})"))
        elif m.max_skewness > SKEW_WARN:
            rows.append(("skew_warn", "warn",
                         f"max skewness {m.max_skewness:g} — check cell topology"))
        else:
            rows.append(("skew_ok", "pass", f"max skewness {m.max_skewness:g} ≤ {SKEW_WARN:g}"))

    if m.max_aspect_ratio is not None and m.max_aspect_ratio > ASPECT_WARN:
        rows.append(("aspect_warn", "warn",
                     f"max aspect ratio {m.max_aspect_ratio:g} — stretched cells harden the linear solve"))
    return rows