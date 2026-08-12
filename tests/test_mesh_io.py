"""Tests for the external-mesh boundary-file reader (mesh_io.py).

Pure logic - parses text, no Docker/LLM needed. Uses a small synthetic
boundary file mirroring the real one at
temp/provided-mesh/3D RHVT (OpenFOAM Files)/boundary (same FoamFile header
shape and patch dictionary layout, fewer patches for a fast, readable test).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mesh import load_external_mesh, parse_boundary_file

_SAMPLE_BOUNDARY = """/*--------------------------------*- C++ -*----------------------------------*\\
  =========                 |
  \\\\      /  F ield         | OpenFOAM
   \\\\    /   O peration     | Version:  13
    \\\\  /    A nd           | Website:  www.openfoam.org
     \\\\/     M anipulation  |
\\*---------------------------------------------------------------------------*/
FoamFile
{
    version     2.0;
    format      binary;
    arch        "LSB;label=32;scalar=64";
    class       polyBoundaryMesh;
    location    "constant/polyMesh";
    object      boundary;
}

// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //

3
(
    cold_outlet
    {
        type patch;
        nFaces 3645;
        startFace 10599030;
    }
    inlet_1
    {
        type patch;
        nFaces 84;
        startFace 10609335;
    }
    walls
    {
        type wall;
        nFaces 81981;
        startFace 10609839;
    }
)

// ************************************************************************* //
"""


def test_parse_boundary_file_skips_foamfile_header(tmp_path):
    p = tmp_path / "boundary"
    p.write_text(_SAMPLE_BOUNDARY)
    patches = parse_boundary_file(p)
    names = [pt.name for pt in patches]
    assert names == ["cold_outlet", "inlet_1", "walls"]
    assert "version" not in names and "class" not in names  # header keys not mistaken for patches


def test_parse_boundary_file_reads_type_and_counts(tmp_path):
    p = tmp_path / "boundary"
    p.write_text(_SAMPLE_BOUNDARY)
    patches = {pt.name: pt for pt in parse_boundary_file(p)}
    assert patches["cold_outlet"].type == "patch"
    assert patches["cold_outlet"].n_faces == 3645
    assert patches["cold_outlet"].start_face == 10599030
    assert patches["walls"].type == "wall"
    assert patches["walls"].n_faces == 81981


def test_load_external_mesh_direct_directory(tmp_path):
    (tmp_path / "boundary").write_text(_SAMPLE_BOUNDARY)
    patches = load_external_mesh(tmp_path)
    assert len(patches) == 3


def test_load_external_mesh_polymesh_layout(tmp_path):
    poly = tmp_path / "constant" / "polyMesh"
    poly.mkdir(parents=True)
    (poly / "boundary").write_text(_SAMPLE_BOUNDARY)
    patches = load_external_mesh(tmp_path)
    assert len(patches) == 3


def test_load_external_mesh_missing_raises(tmp_path):
    try:
        load_external_mesh(tmp_path)
    except FileNotFoundError:
        return
    raise AssertionError("expected FileNotFoundError")


def test_real_rhvt_boundary_file_parses():
    """Sanity-check against the actual mentor-provided mesh, if present."""
    mesh_dir = (
        Path(__file__).resolve().parents[1]
        / "temp" / "provided-mesh" / "3D RHVT (OpenFOAM Files)"
    )
    if not mesh_dir.is_dir():
        return  # skip silently if the (large, untracked) mesh isn't checked out
    patches = load_external_mesh(mesh_dir)
    names = {p.name for p in patches}
    assert names == {
        "cold_outlet", "hot_outlet",
        "inlet_1", "inlet_2", "inlet_3", "inlet_4", "inlet_5", "inlet_6",
        "walls",
    }
    walls = next(p for p in patches if p.name == "walls")
    assert walls.type == "wall"


if __name__ == "__main__":
    import tempfile

    n = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            with tempfile.TemporaryDirectory() as d:
                import inspect
                if "tmp_path" in inspect.signature(fn).parameters:
                    fn(Path(d))
                else:
                    fn()
            print(f"ok  {name}")
            n += 1
    print(f"\n{n} tests passed")
