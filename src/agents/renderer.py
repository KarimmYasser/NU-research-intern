"""Deterministic renderer: CaseSpec (IR) -> OpenFOAM v13 files.

This is the load-bearing component of the Phase II architecture
(docs/phase-ii-architecture.md §2). The LLM never writes dictionary syntax; it
emits a validated CaseSpec (spec.py) and this module renders guaranteed-valid
v13 files. Because it is deterministic, its correctness is unit-testable against
golden tutorials rather than dependent on model behaviour.

Scope (tier 1): box geometry + incompressibleFluid solver + RAS turbulence —
enough to render the cavity tutorial exactly. Wider coverage extends this.
"""

from __future__ import annotations

from .case import CaseFiles
from .spec import BoxGeometry, CaseSpec, Field, Patch

# The standard OpenFOAM banner (matches the .org v13 tutorials verbatim).
BANNER = r"""/*--------------------------------*- C++ -*----------------------------------*\
  =========                 |
  \\      /  F ield         | OpenFOAM: The Open Source CFD Toolbox
   \\    /   O peration     | Website:  https://openfoam.org
    \\  /    A nd           | Version:  13
     \\/     M anipulation  |
\*---------------------------------------------------------------------------*/"""

_SEP = "// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //"
FOOTER = "// ************************************************************************* //"


def _num(v: float) -> str:
    """Render a number the way the tutorials do: 10.0->'10', 0.0->'0', 1e-05->'1e-05'."""
    return f"{v:g}"


def _kv(key: str, value: str, indent: int = 0, width: int = 15) -> str:
    """A `key   value;` line aligning the value to the tutorial's column 16."""
    return f"{' ' * indent}{key:<{width}} {value};"


def _foam(key: str, value: str) -> str:
    # FoamFile-block entries indent 4 with a narrower key column than the body.
    return _kv(key, value, indent=4, width=11)


def _header(class_: str, object_: str, location: str | None = None) -> str:
    lines = [BANNER, "FoamFile", "{", _foam("format", "ascii"), _foam("class", class_)]
    if location is not None:
        lines.append(_foam("location", f'"{location}"'))
    lines += [_foam("object", object_), "}", _SEP]
    return "\n".join(lines)


# Box hex vertex numbering (blockMesh convention) -> the 4 vertices of each face,
# wound so the face normal points out of the block. Verified against the cavity.
_BOX_FACE_VERTS: dict[str, tuple[int, int, int, int]] = {
    "xMin": (0, 4, 7, 3),
    "xMax": (2, 6, 5, 1),
    "yMin": (1, 5, 4, 0),
    "yMax": (3, 7, 6, 2),
    "zMin": (0, 3, 2, 1),
    "zMax": (4, 5, 6, 7),
}


def render_blockmeshdict(geom: BoxGeometry, patches: list[Patch]) -> str:
    (x0, x1), (y0, y1), (z0, z1) = geom.x, geom.y, geom.z
    # 8 box corners in blockMesh vertex order 0..7.
    corners = [
        (x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
        (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1),
    ]
    verts = "\n".join(f"    ({_num(a)} {_num(b)} {_num(c)})" for a, b, c in corners)
    nx, ny, nz = geom.cells
    gx, gy, gz = geom.grading

    blocks = (
        f"    hex (0 1 2 3 4 5 6 7) ({nx} {ny} {nz}) "
        f"simpleGrading ({_num(gx)} {_num(gy)} {_num(gz)})"
    )

    bnd_parts = []
    for p in patches:
        faces = "\n".join(
            "            (" + " ".join(str(v) for v in _BOX_FACE_VERTS[f]) + ")"
            for f in p.faces
        )
        bnd_parts.append(
            f"    {p.name}\n"
            f"    {{\n"
            f"        type {p.type};\n"
            f"        faces\n"
            f"        (\n"
            f"{faces}\n"
            f"        );\n"
            f"    }}"
        )
    boundary = "\n".join(bnd_parts)

    body = (
        f"\nconvertToMeters {_num(geom.convert_to_meters)};\n\n"
        f"vertices\n(\n{verts}\n);\n\n"
        f"blocks\n(\n{blocks}\n);\n\n"
        f"boundary\n(\n{boundary}\n);\n\n"
    )
    return f"{_header('dictionary', 'blockMeshDict')}\n{body}\n{FOOTER}\n"


def render_controldict(spec: CaseSpec) -> str:
    t = spec.time
    entries = [
        ("solver", spec.solver),
        ("startFrom", "startTime"),
        ("startTime", _num(t.start_time)),
        ("stopAt", "endTime"),
        ("endTime", _num(t.end_time)),
        ("deltaT", _num(t.delta_t)),
        ("writeControl", t.write_control),
        ("writeInterval", str(t.write_interval)),
        ("purgeWrite", "0"),
        ("writeFormat", "ascii"),
        ("writePrecision", "6"),
        ("writeCompression", "off"),
        ("timeFormat", "general"),
        ("timePrecision", "6"),
        ("runTimeModifiable", "true"),
    ]
    body = "\n\n".join(_kv(k, v) for k, v in entries)
    return f"{_header('dictionary', 'controlDict', 'system')}\n\n{body}\n\n{FOOTER}\n"


# fvSchemes / fvSolution for the incompressibleFluid solver. These are the v13
# tutorial defaults; kept as verified templates (they reference field names, not
# geometry, so they are stable across box cases).
_FVSCHEMES_BODY = """
ddtSchemes
{
    default         Euler;
}

gradSchemes
{
    default         Gauss linear;
}

divSchemes
{
    default         none;
    div(phi,U)      Gauss limitedLinearV 1;
    div(phi,k)      Gauss limitedLinear 1;
    div(phi,epsilon) Gauss limitedLinear 1;
    div(phi,omega)  Gauss limitedLinear 1;
    div(phi,R)      Gauss limitedLinear 1;
    div(R)          Gauss linear;
    div(phi,nuTilda) Gauss limitedLinear 1;
    div((nuEff*dev2(T(grad(U))))) Gauss linear;
}

laplacianSchemes
{
    default         Gauss linear corrected;
}

interpolationSchemes
{
    default         linear;
}

snGradSchemes
{
    default         corrected;
}

"""

_FVSOLUTION_BODY = """
solvers
{
    p
    {
        solver          GAMG;
        tolerance       1e-06;
        relTol          0.1;
        smoother        GaussSeidel;
    }

    pFinal
    {
        $p;
        tolerance       1e-06;
        relTol          0;
    }

    "(U|k|epsilon|omega|R|nuTilda).*"
    {
        solver          smoothSolver;
        smoother        GaussSeidel;
        tolerance       1e-05;
        relTol          0;
    }
}

PIMPLE
{
    nCorrectors     2;
    nNonOrthogonalCorrectors 0;
    pRefCell        0;
    pRefValue       0;
}

"""


def render_fvschemes(spec: CaseSpec) -> str:
    return f"{_header('dictionary', 'fvSchemes', 'system')}\n{_FVSCHEMES_BODY}\n{FOOTER}\n"


def render_fvsolution(spec: CaseSpec) -> str:
    return f"{_header('dictionary', 'fvSolution', 'system')}\n{_FVSOLUTION_BODY}\n{FOOTER}\n"


def render_physicalproperties(spec: CaseSpec) -> str:
    f = spec.fluid
    body = f"\n{_kv('viscosityModel', f.viscosity_model)}\n\n{_kv('nu', f.nu)}\n"
    return f"{_header('dictionary', 'physicalProperties', 'constant')}\n{body}\n{FOOTER}\n"


def render_momentumtransport(spec: CaseSpec) -> str:
    t = spec.turbulence
    body = (
        f"\n{_kv('simulationType', t.simulation_type)}\n\n"
        f"{t.simulation_type}\n{{\n"
        f"{_kv('model', t.model, indent=4)}\n\n"
        f"{_kv('turbulence', 'on' if t.turbulence else 'off', indent=4)}\n\n"
        f"}}\n"
    )
    return f"{_header('dictionary', 'momentumTransport', 'constant')}\n{body}\n{FOOTER}\n"


def render_field(field: Field, patches: list[Patch]) -> str:
    dims = "[" + " ".join(str(d) for d in field.dimensions) + "]"
    bf_parts = []
    for p in patches:
        bc = p.bc.get(field.name)
        if bc is None:
            continue
        inner = f"        type            {bc.type};"
        if bc.value is not None:
            inner += f"\n        value           {bc.value};"
        bf_parts.append(f"    {p.name}\n    {{\n{inner}\n    }}")
    boundary = "\n\n".join(bf_parts)

    body = (
        f"\n{_kv('dimensions', dims)}\n\n"
        f"{_kv('internalField', field.internal_field)}\n\n"
        f"boundaryField\n{{\n{boundary}\n}}\n"
    )
    return f"{_header(field.field_class, field.name)}\n{body}\n{FOOTER}\n"


def render(spec: CaseSpec) -> CaseFiles:
    """Render a validated CaseSpec into an in-memory CaseFiles (v13)."""
    case = CaseFiles()
    case.system["blockMeshDict"] = render_blockmeshdict(spec.geometry, spec.patches)
    case.system["controlDict"] = render_controldict(spec)
    case.system["fvSchemes"] = render_fvschemes(spec)
    case.system["fvSolution"] = render_fvsolution(spec)
    case.constant["physicalProperties"] = render_physicalproperties(spec)
    case.constant["momentumTransport"] = render_momentumtransport(spec)
    for field in spec.fields:
        case.zero[field.name] = render_field(field, spec.patches)
    return case
