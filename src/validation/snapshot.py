"""Archive the configuration of a run that worked.

Case directories are re-rendered in place: every retry overwrites the previous
dictionaries, and the solver log lives only in memory. A configuration that ran
well is therefore destroyed by the next attempt — which is exactly what happened
to the RHVT reference result on 2026-08-18 (see
docs/evidence/rhvt-residuals-2026-08-18.md).

So when the ladder succeeds we copy the *inputs* — `system/`, `0/`, and
`constant/` minus the mesh — plus the solver log into `<case>/.runs/<stamp>/`.
That is a few tens of kB even for a 10.6M-face case, because the mesh, the time
directories and the `processor*/` folders are all excluded.

This is deliberately not a general backup tool. It preserves enough to
*reproduce* and to *cite* a good run, nothing more.
"""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

SNAPSHOT_DIRNAME = ".runs"

#: Copied when present. `constant/` is filtered by `_is_mesh_path`.
_SNAPSHOT_SOURCES = ("system", "0", "constant")

#: A dictionary this large is not a dictionary; skip it rather than bloat the case.
_MAX_FILE_BYTES = 5 * 1024 * 1024

#: Mesh data is regenerable or externally supplied, and is the bulk of the case.
_MESH_DIR_NAMES = frozenset({"polyMesh", "extendedFeatureEdgeMesh", "triSurface"})

#: Snapshots are small, but the self-correction loop can validate many times in one
#: unattended run. Keep the most recent few and discard the rest.
DEFAULT_KEEP = 10

#: A solver log grows with the step count (a 1000-step cavity writes ~13k lines, 1.5 MB).
#: Keep the header, which identifies the build and the case, and the tail, which holds
#: the residuals and the outcome; drop the middle.
_LOG_HEAD_LINES = 200
_LOG_TAIL_LINES = 2000


def _is_mesh_path(rel: Path) -> bool:
    return any(part in _MESH_DIR_NAMES for part in rel.parts)


def _copy_inputs(case_dir: Path, dest: Path) -> tuple[list[str], list[str]]:
    """Copy dictionaries and initial fields. Returns (copied, skipped) rel-paths."""
    copied: list[str] = []
    skipped: list[str] = []
    for name in _SNAPSHOT_SOURCES:
        src_root = case_dir / name
        if not src_root.is_dir():
            continue
        for src in sorted(src_root.rglob("*")):
            if not src.is_file():
                continue
            rel = src.relative_to(case_dir)
            if _is_mesh_path(rel):
                continue
            try:
                if src.stat().st_size > _MAX_FILE_BYTES:
                    skipped.append(f"{rel} (too large)")
                    continue
                target = dest / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, target)
                copied.append(str(rel))
            except OSError as exc:  # unreadable file must not fail the run
                skipped.append(f"{rel} ({exc.strerror or exc})")
    return copied, skipped


def _trim_log(log: str) -> tuple[str, bool]:
    """Keep the head and tail of a long solver log. Returns (text, was_trimmed)."""
    lines = log.splitlines()
    if len(lines) <= _LOG_HEAD_LINES + _LOG_TAIL_LINES:
        return log, False
    dropped = len(lines) - _LOG_HEAD_LINES - _LOG_TAIL_LINES
    kept = [
        *lines[:_LOG_HEAD_LINES],
        "",
        f"[... {dropped} lines omitted by the run snapshot ...]",
        "",
        *lines[-_LOG_TAIL_LINES:],
    ]
    return "\n".join(kept) + "\n", True


def prune_snapshots(case_dir: Path, keep: int = DEFAULT_KEEP) -> list[str]:
    """Delete all but the `keep` most recent snapshots. Returns removed names.

    Names are UTC timestamps, so lexical order is chronological. Pruning is
    best-effort: a directory that cannot be removed is left in place rather than
    raising, because this runs after a validation that already succeeded.
    """
    if keep < 1:
        return []
    snapshots = list_snapshots(case_dir)
    removed: list[str] = []
    for old in snapshots[:-keep] if len(snapshots) > keep else []:
        try:
            shutil.rmtree(old)
            removed.append(old.name)
        except OSError:
            continue
    return removed


def snapshot_run(
    case_dir: Path,
    log: str = "",
    *,
    stage: str = "all",
    nprocs: int = 1,
    extra: dict | None = None,
    keep: int = DEFAULT_KEEP,
) -> Path:
    """Archive a successful run's inputs and log under `<case>/.runs/<stamp>/`.

    Older snapshots beyond `keep` are pruned. Raises OSError only if the snapshot
    directory itself cannot be created; individual unreadable files are recorded
    as skipped instead.
    """
    case_dir = Path(case_dir)
    # Microsecond precision keeps names unique *and* lexically monotonic, so
    # `list_snapshots` can sort by name and pruning always drops the oldest. A
    # collision suffix would not do: pruning frees earlier names, and the next
    # snapshot would reuse one and sort itself to the front.
    now = datetime.now(UTC)
    stamp = f"{now.strftime('%Y%m%dT%H%M%S')}.{now.microsecond:06d}Z"
    dest = case_dir / SNAPSHOT_DIRNAME / f"{stamp}-{stage}"
    dest.mkdir(parents=True, exist_ok=True)

    copied, skipped = _copy_inputs(case_dir, dest)

    log_text, log_trimmed = _trim_log(log)
    if log_text:
        (dest / "solver.log").write_text(log_text, encoding="utf-8")

    manifest = {
        "captured_utc": stamp,
        "case_dir": str(case_dir),
        "stage": stage,
        "nprocs": nprocs,
        "files": copied,
        "skipped": skipped,
        "log_lines": len(log.splitlines()) if log else 0,
        "log_trimmed": log_trimmed,
        "note": "Inputs of a run that succeeded. Mesh, time directories and "
                "processor* folders are deliberately excluded.",
    }
    if extra:
        manifest.update(extra)
    (dest / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    prune_snapshots(case_dir, keep)
    return dest


def list_snapshots(case_dir: Path) -> list[Path]:
    """Existing snapshots for a case, oldest first."""
    root = Path(case_dir) / SNAPSHOT_DIRNAME
    if not root.is_dir():
        return []
    return sorted(p for p in root.iterdir() if p.is_dir())
