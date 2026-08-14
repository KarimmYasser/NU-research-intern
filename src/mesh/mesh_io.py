"""Reads externally-provided OpenFOAM meshes.

Per the mentor's scope decision (docs/new-workflow.md, docs/phase-ii-architecture.md
§2.1 "Project rules" R1): this repo never generates meshes. A mesh is supplied
externally as a `constant/polyMesh/` directory (or a folder containing the raw
polyMesh files directly, as in temp/provided-mesh/). We only need to read the
lightweight `boundary` file to learn patch names/types/face counts — the large
`points`/`faces`/`owner`/`neighbour` files are copied byte-for-byte into the
case, never parsed.

The `boundary` file is a plain ASCII OpenFOAM dictionary (the FoamFile header's
`format binary;` only describes how *other* polyMesh files -- points/faces/owner
-- are encoded; `boundary` itself is always a small ASCII list), so a simple
regex-based parse is sufficient and avoids pulling in a full OpenFOAM parser.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path

# polyMesh files that make up a mesh; boundary is parsed, the rest are copied as-is.
_POLYMESH_FILES = ("points", "faces", "owner", "neighbour", "boundary")

_PATCH_BLOCK_RE = re.compile(
    r"(\w+)\s*\{\s*([^{}]*?)\s*\}", re.DOTALL
)
_KEY_RE = re.compile(r"(\w+)\s+([^;]+);")


@dataclass
class MeshPatch:
    name: str
    type: str  # patch / wall / symmetryPlane / empty / ...
    n_faces: int
    start_face: int


def parse_boundary_file(path: Path) -> list[MeshPatch]:
    """Parse a polyMesh `boundary` file into a list of MeshPatch entries.

    Strips the FoamFile header block and C++-style comments, then scans the
    remaining `(<name> {...} ...)` list for patch blocks.
    """
    text = path.read_text(errors="replace")
    text = re.sub(r"//[^\n]*", "", text)  # line comments
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)  # block comments
    # Drop the FoamFile{...} header so it can't be mistaken for a patch block.
    text = re.sub(r"FoamFile\s*\{.*?\}", "", text, flags=re.DOTALL)

    patches: list[MeshPatch] = []
    for name, body in _PATCH_BLOCK_RE.findall(text):
        fields = dict(_KEY_RE.findall(body))
        if "type" not in fields or "nFaces" not in fields:
            continue  # not a patch block (or malformed)
        patches.append(MeshPatch(
            name=name,
            type=fields["type"].strip(),
            n_faces=int(fields["nFaces"].strip()),
            start_face=int(fields.get("startFace", "0").strip()),
        ))
    return patches


def load_external_mesh(mesh_dir: Path) -> list[MeshPatch]:
    """Locate and parse the `boundary` file under `mesh_dir`.

    Accepts either a directory containing `boundary` directly, or the standard
    `constant/polyMesh/boundary` layout.
    """
    for candidate in (mesh_dir / "boundary", mesh_dir / "constant" / "polyMesh" / "boundary"):
        if candidate.is_file():
            return parse_boundary_file(candidate)
    raise FileNotFoundError(f"no boundary file found under {mesh_dir}")


def copy_polymesh(mesh_dir: Path, case_dir: Path) -> None:
    """Copy the raw polyMesh files into `<case_dir>/constant/polyMesh/`, unparsed.

    Looks in the same two layouts as load_external_mesh(). Only copies the
    known polyMesh files (plus a `sets/` subfolder if present); does not touch
    or reinterpret point/face/owner/neighbour data.
    """
    src = mesh_dir
    if not (src / "boundary").is_file():
        src = mesh_dir / "constant" / "polyMesh"
    if not (src / "boundary").is_file():
        raise FileNotFoundError(f"no polyMesh files found under {mesh_dir}")

    dst = case_dir / "constant" / "polyMesh"
    dst.mkdir(parents=True, exist_ok=True)
    for name in _POLYMESH_FILES:
        f = src / name
        if f.is_file():
            shutil.copyfile(f, dst / name)
    sets_dir = src / "sets"
    if sets_dir.is_dir():
        shutil.copytree(sets_dir, dst / "sets", dirs_exist_ok=True)
