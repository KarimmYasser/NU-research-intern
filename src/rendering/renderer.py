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

from ir.spec import BoxGeometry, CaseSpec, Field, Patch
from .case import CaseFiles

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
        ("startFrom", t.start_from),
        ("startTime", _num(t.start_time)),
        ("stopAt", t.stop_at),
        ("endTime", _num(t.end_time)),
        ("deltaT", _num(t.delta_t)),
        ("writeControl", t.write_control),
        ("writeInterval", _num(t.write_interval)),
        ("purgeWrite", "0"),
        ("writeFormat", "ascii"),
        ("writePrecision", "6"),
        ("writeCompression", "off"),
        ("timeFormat", "general"),
        ("timePrecision", "6"),
        ("runTimeModifiable", t.run_time_modifiable),
    ]
    if t.adjust_time_step is not None:
        entries.append(("adjustTimeStep", t.adjust_time_step))
    if t.max_co is not None:
        entries.append(("maxCo", _num(t.max_co)))
    body = "\n\n".join(_kv(k, v) for k, v in entries)
    if spec.functions:
        funcs = "\n".join(f"    {fn}" for fn in spec.functions)
        body += f"\n\nfunctions\n{{\n{funcs}\n}}"
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

# Upwind preset (1st-order, robust) — used by the turbulent channel case.
_FVSCHEMES_UPWIND = """
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
    default             none;

    div(phi,U)          Gauss upwind;
    div(phi,k)          Gauss upwind;
    div(phi,epsilon)    Gauss upwind;
    div(phi,R)          Gauss upwind;
    div(R)              Gauss upwind;
    div(phi,nuTilda)    Gauss upwind;

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

_FVSOLUTION_UPWIND = """
solvers
{
    p
    {
        solver          GAMG;
        tolerance       0.01;
        relTol          0.01;
        smoother        DICGaussSeidel;
    }

    pFinal
    {
        $p;
        relTol          0;
    }

    "(U|k|epsilon)"
    {
        solver          smoothSolver;
        smoother        symGaussSeidel;
        tolerance       0.01;
        relTol          0.1;
    }

    "(U|k|epsilon)Final"
    {
        $U;
        relTol          0;
    }
}

PIMPLE
{
    nNonOrthogonalCorrectors 0;
    nCorrectors     3;
}

"""

# Compressible steady-state preset (standard OpenFOAM v13 general defaults)
_FVSCHEMES_COMPRESSIBLE_STEADY = """
ddtSchemes
{
    default         steadyState;
}

gradSchemes
{
    default         Gauss linear;
    grad(k)         cellMDLimited Gauss linear 1;
    grad(U)         cellMDLimited Gauss linear 1;
    grad(h)         cellMDLimited Gauss linear 1;
    grad(e)         cellMDLimited Gauss linear 1;
}

divSchemes
{
    default         none;

    div(phi,U)      Gauss limitedLinear 1;
    div(phi,h)      Gauss limitedLinear 1;
    div(phi,e)      Gauss limitedLinear 1;

    div(phi,k)       Gauss limitedLinear01 1;
    div(phi,epsilon) Gauss limitedLinear01 1;
    div(phi,omega)   Gauss limitedLinear01 1;

    div(phi,K)      Gauss linear;
    div(phi,Ekp)    Gauss linear;
    div(R)          Gauss linear;
    div(((rho*nuEff)*dev2(T(grad(U))))) Gauss linear;
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

_FVSOLUTION_COMPRESSIBLE_STEADY = """
solvers
{
    "rho.*"
    {
        solver          PCG;
        preconditioner  DIC;
        tolerance       0;
        relTol          0;
    }

    p
    {
        solver          GAMG;
        smoother        GaussSeidel;
        tolerance       1e-06;
        relTol          0.01;
        maxIter         50;
        cacheAgglomeration true;
        nCellsInCoarsestLevel 1000;
        agglomerator    faceAreaPair;
        mergeLevels     1;
    }

    pFinal
    {
        $p;
        relTol          0.01;
    }

    "(U|h|e|k|epsilon|R)"
    {
        solver          PBiCGStab;
        preconditioner  DILU;
        tolerance       1e-06;
        relTol          0.01;
    }

    "(U|h|e|k|epsilon|R)Final"
    {
        $U;
        relTol          0;
    }
}

SIMPLE
{
    momentumPredictor yes;
    nOuterCorrectors 1;
    nCorrectors     1;
    nNonOrthogonalCorrectors 1;
}

relaxationFactors
{
    fields
    {
        p          0.3;
    }
    equations
    {
        U               0.4;
        T               0.4;
        k               0.4;
        epsilon         0.4;
        h               0.4;
    }
}

"""

_FVSCHEMES = {
    "incompressibleFluid_limitedLinear": _FVSCHEMES_BODY,
    "incompressibleFluid_upwind": _FVSCHEMES_UPWIND,
    "compressibleFluid_steadyState": _FVSCHEMES_COMPRESSIBLE_STEADY,
}
_FVSOLUTION = {
    "incompressibleFluid_limitedLinear": _FVSOLUTION_BODY,
    "incompressibleFluid_upwind": _FVSOLUTION_UPWIND,
    "compressibleFluid_steadyState": _FVSOLUTION_COMPRESSIBLE_STEADY,
}


def render_fvschemes(spec: CaseSpec) -> str:
    if spec.custom_fv_schemes:
        body = spec.custom_fv_schemes
    else:
        body = _FVSCHEMES.get(spec.numerics, _FVSCHEMES["incompressibleFluid_limitedLinear"])
    return f"{_header('dictionary', 'fvSchemes', 'system')}\n{body}\n{FOOTER}\n"


def render_fvsolution(spec: CaseSpec) -> str:
    if spec.custom_fv_solution:
        body = spec.custom_fv_solution
    else:
        body = _FVSOLUTION.get(spec.numerics, _FVSOLUTION["incompressibleFluid_limitedLinear"])
    return f"{_header('dictionary', 'fvSolution', 'system')}\n{body}\n{FOOTER}\n"


def render_physicalproperties(spec: CaseSpec) -> str:
    f = spec.fluid
    if f.flow_type == "compressible":
        from ir.spec import ThermoProperties
        t = f.thermo or ThermoProperties()
        thermo_entries = "\n".join(_kv(k, v, indent=4) for k, v in t.thermo_type.items())
        body = (
            f"\nthermoType\n{{\n{thermo_entries}\n}}\n\n"
            f"{_kv('pRef', _num(t.p_ref))}\n\n"
            f"mixture\n{{\n"
            f"    specie\n    {{\n{_kv('molWeight', _num(t.mol_weight), indent=8)}\n    }}\n"
            f"    thermodynamics\n    {{\n{_kv('Cp', _num(t.cp), indent=8)}\n{_kv('hf', _num(t.hf), indent=8)}\n    }}\n"
            f"    transport\n    {{\n{_kv('mu', _num(t.mu), indent=8)}\n{_kv('Pr', _num(t.pr), indent=8)}\n    }}\n"
            f"}}\n"
        )
        return f"{_header('dictionary', 'physicalProperties', 'constant')}\n{body}\n{FOOTER}\n"
    comment = f"// {f.comment}\n" if f.comment else ""
    body = f"\n{_kv('viscosityModel', f.viscosity_model)}\n\n{comment}{_kv('nu', f.nu)}\n"
    return f"{_header('dictionary', 'physicalProperties', 'constant')}\n{body}\n{FOOTER}\n"


def render_momentumtransport(spec: CaseSpec) -> str:
    t = spec.turbulence
    if t.simulation_type == "laminar":
        body = f"\n{_kv('simulationType', 'laminar')}\n"
        return f"{_header('dictionary', 'momentumTransport', 'constant')}\n{body}\n{FOOTER}\n"
    inner = (
        f"{_kv('model', t.model, indent=4)}\n\n"
        f"{_kv('turbulence', 'on' if t.turbulence else 'off', indent=4)}\n\n"
    )
    if t.print_coeffs:
        inner += f"{_kv('printCoeffs', 'on', indent=4)}\n\n"
    body = (
        f"\n{_kv('simulationType', t.simulation_type)}\n\n"
        f"{t.simulation_type}\n{{\n{inner}}}\n"
    )
    return f"{_header('dictionary', 'momentumTransport', 'constant')}\n{body}\n{FOOTER}\n"


def render_field(field: Field, patches: list[Patch]) -> str:
    dims = "[" + " ".join(str(d) for d in field.dimensions) + "]"
    bf_parts = []
    for p in patches:
        bc = p.bc.get(field.name)
        if bc is None:
            continue
        inner_lines = [f"        type            {bc.type};"]
        if bc.value is not None:
            inner_lines.append(f"        value           {bc.value};")
        if hasattr(bc, "extra") and bc.extra:
            for k, v in bc.extra.items():
                if k not in ("type", "value"):
                    inner_lines.append(f"        {k:<15} {v};")
        inner = "\n".join(inner_lines)
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
    # Box geometry -> blockMeshDict; external mesh -> only the case setup is rendered.
    if isinstance(spec.geometry, BoxGeometry):
        case.system["blockMeshDict"] = render_blockmeshdict(spec.geometry, spec.patches)
    case.system["controlDict"] = render_controldict(spec)
    case.system["fvSchemes"] = render_fvschemes(spec)
    case.system["fvSolution"] = render_fvsolution(spec)
    case.constant["physicalProperties"] = render_physicalproperties(spec)
    case.constant["momentumTransport"] = render_momentumtransport(spec)
    for field in spec.fields:
        case.zero[field.name] = render_field(field, spec.patches)
    return case
