"""Deterministic renderer: CaseSpec (IR) -> OpenFOAM v13 files.

This is the load-bearing component of the Phase II architecture
(docs/phase-ii-architecture.md §2). The LLM never writes dictionary syntax; it
emits a validated CaseSpec (spec.py) and this module renders guaranteed-valid
v13 files. Because it is deterministic, its correctness is unit-testable against
golden tutorials rather than dependent on model behaviour.

Scope: box geometry (blockMeshDict) or external mesh (case setup only);
incompressible and compressible flow; transient and steady-state; laminar and
RAS turbulence. Presets are chosen from the CaseSpec's numerics field, so the
renderer itself is case-agnostic.
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
        $"(U|k|epsilon)";
        relTol          0;
    }
}

PIMPLE
{
    nNonOrthogonalCorrectors 0;
    nCorrectors     3;
    pRefCell        0;
    pRefValue       0;
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

# Incompressible VoF preset (grounded in v13 damBreakLaminar; turbulence div
# entries added so the turbulent damBreak variant stays expressible).
_FVSCHEMES_INCOMPRESSIBLE_VOF = """
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
    div(phi,alpha)  Gauss interfaceCompression vanLeer 1;

    div(rhoPhi,U)   Gauss linearUpwind grad(U);

    div(rhoPhi,k)   Gauss upwind;
    div(rhoPhi,epsilon) Gauss upwind;
    div(rhoPhi,omega) Gauss upwind;

    div(((rho*nuEff)*dev2(T(grad(U))))) Gauss linear;
}

laplacianSchemes
{
    default         Gauss linear uncorrected;
}

interpolationSchemes
{
    default         linear;
}

snGradSchemes
{
    default         uncorrected;
}

"""

_FVSOLUTION_INCOMPRESSIBLE_VOF = """
solvers
{
    "alpha.*"
    {
        nCorrectors     2;
        nSubCycles      1;

        MULESCorr       yes;

        solver          smoothSolver;
        smoother        symGaussSeidel;
        tolerance       1e-8;
        relTol          0;
    }

    "pcorr.*"
    {
        solver          PCG;
        preconditioner  DIC;
        tolerance       1e-5;
        relTol          0;
    }

    p_rgh
    {
        solver          PCG;
        preconditioner  DIC;
        tolerance       1e-7;
        relTol          0.05;
    }

    p_rghFinal
    {
        $p_rgh          ;
        relTol          0;
    }

    "(U|k|epsilon|omega).*"
    {
        solver          smoothSolver;
        smoother        symGaussSeidel;
        tolerance       1e-6;
        relTol          0;
        minIter         1;
    }
}

PIMPLE
{
    momentumPredictor no;
    nOuterCorrectors 1;
    nCorrectors     3;
    nNonOrthogonalCorrectors 0;
}

relaxationFactors
{
    equations
    {
        ".*"            1;
    }
}

"""

# Compressible transient preset (grounded in v13 fluid/shockTube: Euler ddt,
# PIMPLE loop). Used whenever a compressible case is not steady — the fluid
# module's PIMPLE (PISO-SIMPLE) control reads this dict by name.
_FVSCHEMES_COMPRESSIBLE_TRANSIENT = """
ddtSchemes
{
    default         Euler;
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

# Compressible VoF preset (grounded in v13 compressibleVoF/damBreak).
_FVSCHEMES_COMPRESSIBLE_VOF = """
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
    div(phi,alpha)  Gauss interfaceCompression vanLeer 1;

    div(rhoPhi,U)   Gauss linearUpwind grad(U);

    div(alphaRhoPhi,e) Gauss upwind;
    div(alphaRhoPhi,T) Gauss upwind;

    div(rhoPhi,K)   Gauss upwind;
    div(phi,p)      Gauss upwind;

    div(rhoPhi,k)   Gauss upwind;
    div(rhoPhi,epsilon) Gauss upwind;
    div(rhoPhi,omega) Gauss upwind;

    div(((rho*nuEff)*dev2(T(grad(U))))) Gauss linear;
}

laplacianSchemes
{
    default         Gauss linear uncorrected;
}

interpolationSchemes
{
    default         linear;
}

snGradSchemes
{
    default         uncorrected;
}

"""

_FVSOLUTION_COMPRESSIBLE_VOF = """
solvers
{
    "alpha.*"
    {
        nCorrectors     2;
        nSubCycles      1;

        MULESCorr       yes;

        solver          smoothSolver;
        smoother        symGaussSeidel;
        tolerance       1e-8;
        relTol          0;
    }

    "pcorr.*"
    {
        solver          PCG;
        preconditioner  DIC;
        tolerance       1e-5;
        relTol          0;
    }

    p_rgh
    {
        solver          PCG;
        preconditioner  DIC;
        tolerance       1e-07;
        relTol          0.05;
    }

    p_rghFinal
    {
        $p_rgh;
        relTol          0;
    }

    "(U|T|k|epsilon|omega).*"
    {
        solver          smoothSolver;
        smoother        symGaussSeidel;
        tolerance       1e-06;
        relTol          0;
        minIter         1;
    }
}

PIMPLE
{
    momentumPredictor   no;
    nOuterCorrectors    1;
    nCorrectors         3;
    nNonOrthogonalCorrectors 0;
    pRefPoint           (0 0 0);
    pRefValue           0;
}

relaxationFactors
{
    equations
    {
        ".*"                1;
    }
}

"""

_FVSOLUTION_COMPRESSIBLE_STEADY = """
solvers
{
    "rho.*"
    {
        solver          smoothSolver;
        smoother        GaussSeidel;
        tolerance       1e-06;
        relTol          0;
    }

    "p.*"
    {
        solver          GAMG;
        smoother        DIC;
        tolerance       1e-08;
        relTol          0.00;
        maxIter         25;
        cacheAgglomeration true;
        nCellsInCoarsestLevel 1000;
        agglomerator    faceAreaPair;
        mergeLevels     1;
    }

    "(U|h|e|k|epsilon|omega|R).*"
    {
        solver          PBiCGStab;
        preconditioner  DILU;
        tolerance       1e-06;
        relTol          0.01;
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
        p          0.2;
        rho        0.05;
    }
    equations
    {
        U               0.5;
        T               0.5;
        k               0.5;
        epsilon         0.5;
        h               0.5;
    }
}

"""

# Compressible transient preset: same solvers, PIMPLE loop with outer
# corrections + relaxation for stability on a transient compressible run.
_FVSOLUTION_COMPRESSIBLE_TRANSIENT = """
solvers
{
    "rho.*"
    {
        solver          smoothSolver;
        smoother        GaussSeidel;
        tolerance       1e-06;
        relTol          0;
    }

    "p.*"
    {
        solver          GAMG;
        smoother        DIC;
        tolerance       1e-08;
        relTol          0.00;
        maxIter         25;
        cacheAgglomeration true;
        nCellsInCoarsestLevel 1000;
        agglomerator    faceAreaPair;
        mergeLevels     1;
    }

    "(U|h|e|k|epsilon|omega|R).*"
    {
        solver          PBiCGStab;
        preconditioner  DILU;
        tolerance       1e-06;
        relTol          0.01;
    }
}

PIMPLE
{
    momentumPredictor yes;
    nOuterCorrectors 2;
    nCorrectors     1;
    nNonOrthogonalCorrectors 0;
}

relaxationFactors
{
    fields
    {
        p          0.2;
        rho        0.05;
    }
    equations
    {
        U               0.5;
        T               0.5;
        k               0.5;
        epsilon         0.5;
        h               0.5;
    }
}

"""

_FVSCHEMES = {
    "incompressibleFluid_limitedLinear": _FVSCHEMES_BODY,
    "incompressibleFluid_upwind": _FVSCHEMES_UPWIND,
    "compressibleFluid_steadyState": _FVSCHEMES_COMPRESSIBLE_STEADY,
    "compressibleFluid_transient": _FVSCHEMES_COMPRESSIBLE_TRANSIENT,
    "incompressibleVoF": _FVSCHEMES_INCOMPRESSIBLE_VOF,
    "compressibleVoF": _FVSCHEMES_COMPRESSIBLE_VOF,
}
_FVSOLUTION = {
    "incompressibleFluid_limitedLinear": _FVSOLUTION_BODY,
    "incompressibleFluid_upwind": _FVSOLUTION_UPWIND,
    "compressibleFluid_steadyState": _FVSOLUTION_COMPRESSIBLE_STEADY,
    "compressibleFluid_transient": _FVSOLUTION_COMPRESSIBLE_TRANSIENT,
    "incompressibleVoF": _FVSOLUTION_INCOMPRESSIBLE_VOF,
    "compressibleVoF": _FVSOLUTION_COMPRESSIBLE_VOF,
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
    if spec.solid:
        return _render_solid_physical(spec)
    if spec.is_film:
        return _render_film_physical(spec)
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


def _render_solid_physical(spec: CaseSpec) -> str:
    """SolidDisplacement property dict, grounded in solidDisplacement/plateHole. A
    steel default (E=2e11, nu=0.3, rho=7854, Cv=434, kappa=60.5)."""
    body = "\n".join(
        f"\n{key}\n{{\n    type        uniform;\n    value       {value};\n}}"
        for key, value in (
            ("rho", "7854"),
            ("nu", "0.3"),
            ("E", "2e+11"),
            ("Cv", "434"),
            ("kappa", "60.5"),
        )
    )
    return f"{_header('dictionary', 'physicalProperties', 'constant')}\n{body}\n\n{FOOTER}\n"


def _render_film_physical(spec: CaseSpec) -> str:
    """Thin-film property dict, grounded in isothermalFilm/rivuletPanel: thermo
    (water) + surface tension + deltaWet."""
    body = (
        "\nthermoType\n{\n"
        "    type            heRhoThermo;\n"
        "    mixture         pureMixture;\n"
        "    properties      liquid;\n"
        "    energy          sensibleInternalEnergy;\n"
        "}\n\n"
        "mixture\n{\n    H2O;\n}\n\n"
        "sigma\n{\n"
        "    type    constant;\n"
        f"    sigma   {_num(spec.surface_tension if spec.surface_tension is not None else 0.07)};\n"
        "}\n\n"
        f"{_kv('deltaWet', '1e-8')}\n"
    )
    return f"{_header('dictionary', 'physicalProperties', 'constant')}\n{body}\n\n{FOOTER}\n"


def render_g(spec: CaseSpec) -> str:
    """constant/g -> uniformDimensionedVectorField (grounded in damBreak/g)."""
    gx, gy, gz = spec.gravity or (0, 0, 0)
    body = (
        f"\n{_kv('dimensions', '[0 1 -2 0 0 0 0]')}\n\n"
        f"{_kv('value', f'({_num(gx)} {_num(gy)} {_num(gz)})')}\n"
    )
    return f"{_header('uniformDimensionedVectorField', 'g', 'constant')}\n{body}\n\n{FOOTER}\n"


# Per-phase incompressible transport defaults, from the v13 tutorials / SI values.
_PHASE_NU_RHO: dict[str, tuple[float, float]] = {
    "water": (1e-06, 1000.0),
    "air": (1.48e-05, 1.0),
    "oil": (1e-04, 900.0),
    "mercury": (1.14e-07, 13546.0),
    "sludge": (1e-06, 1996.0),
    "carrier": (1.48e-05, 1.0),
    "cloud": (1e-06, 1000.0),
}


def _per_phase_nu_rho(phase: str, default_nu: float) -> tuple[float, float]:
    return _PHASE_NU_RHO.get(phase, (default_nu, 1000.0))


def render_per_phase_physical(spec: CaseSpec, phase: str) -> str:
    """constant/physicalProperties.<phase>, grounded in:
      - incompressible: incompressibleVoF/damBreakLaminar + incompressibleDriftFlux/dahl
      - compressible:  compressibleVoF/damBreak (water: liquidProperties, air: perfectGas)"""
    if not spec.compressible_phases:
        default_nu = float(spec.fluid.nu.split(" ")[0]) if " " in spec.fluid.nu else float(spec.fluid.nu)
        nu, rho = _per_phase_nu_rho(phase, default_nu)
        body = (
            f"\n{_kv('viscosityModel', 'constant')}\n\n"
            f"{_kv('nu', _num(nu))}\n\n"
            f"{_kv('rho', _num(rho))}\n"
        )
        return f"{_header('dictionary', f'physicalProperties.{phase}', 'constant')}\n{body}\n\n{FOOTER}\n"
    if phase == "water":
        body = (
            "\nthermoType\n{\n"
            "    type            heRhoThermo;\n"
            "    mixture         pureMixture;\n"
            "    properties      liquid;\n"
            "    energy          sensibleInternalEnergy;\n"
            "}\n\n"
            "mixture\n{\n    H2O;\n}\n"
        )
    else:
        t = spec.fluid.thermo
        body = (
            "\nthermoType\n{\n"
            "    type            heRhoThermo;\n"
            "    mixture         pureMixture;\n"
            "    transport       const;\n"
            "    thermo          hConst;\n"
            "    equationOfState perfectGas;\n"
            "    specie          specie;\n"
            "    energy          sensibleInternalEnergy;\n"
            "}\n\n"
            "mixture\n{\n"
            "    specie\n    {\n"
            f"{_kv('molWeight', _num(t.mol_weight if t else 28.9), indent=8)}\n"
            "    }\n"
            "    thermodynamics\n    {\n"
            f"{_kv('Cp', _num(t.cp if t else 1007), indent=8)}\n"
            f"{_kv('hf', _num(t.hf if t else 0), indent=8)}\n"
            "    }\n"
            "    transport\n    {\n"
            f"{_kv('mu', _num(t.mu if t else 1.84e-05), indent=8)}\n"
            f"{_kv('Pr', _num(t.pr if t else 0.7), indent=8)}\n"
            "    }\n"
            "}\n"
        )
    return f"{_header('dictionary', f'physicalProperties.{phase}', 'constant')}\n{body}\n\n{FOOTER}\n"


def render_phaseproperties(spec: CaseSpec) -> str:
    """constant/phaseProperties per solver family, grounded in the v13 tutorials:
      - 2-phase VoF:  incompressibleVoF/damBreakLaminar (scalar sigma)
      - n-phase VoF:  incompressibleMultiphaseVoF/damBreak4phase (pairwise sigmas)
      - VoF 2-phase compressible: compressibleVoF/damBreak (liquidProperties sigma)
      - drift-flux:   incompressibleDriftFlux/dahl
      - Euler-Euler:  multiphaseEuler/bubbleColumnLaminar
    """
    phases = " ".join(spec.phases)
    if spec.relative_velocity_model:  # drift-flux
        body = (
            f"\n{_kv('phases', f'({phases})')}\n\n"
            f"{_kv('relativeVelocityModel', spec.relative_velocity_model)}\n\n"
            "\"(simple|general)Coeffs\"\n{\n"
            f"{_kv('Vc', '2.241e-4', indent=4)}\n"
            f"{_kv('a', '285.84', indent=4)}\n"
            f"{_kv('a1', '0.1', indent=4)}\n"
            f"{_kv('residualAlpha', '0', indent=4)}\n"
            "}\n"
        )
    elif spec.case_type == "multiphaseEuler":
        primary = spec.phases[-1]
        phase_blocks = []
        for i, ph in enumerate(spec.phases):
            d_model = "isothermal" if ph == "air" else "constant"
            d_coeffs = f"d0              3e-3;\n        p0              1e5;" if d_model == "isothermal" else f"d               {_num(1e-4)};"
            phase_blocks.append(
                f"{ph}\n{{\n"
                "    type            purePhaseModel;\n\n"
                f"    diameterModel   {d_model};\n"
                f"    {d_model}Coeffs\n    {{\n        {d_coeffs}\n    }}\n\n"
                "    residualAlpha   1e-6;\n}"
            )
        blending_lines = []
        for key in ("default", "segregated"):
            off = "0.5" if key == "segregated" else "0.3"
            blending_lines.append(
                f"    {key}\n    {{\n"
                "        type            linear;\n"
                f"        minFullyContinuousAlpha.{primary} 0.7;\n"
                f"        minPartlyContinuousAlpha.{primary} {off};\n"
                + "".join(
                    f"        minFullyContinuousAlpha.{ph} 0.7;\n"
                    f"        minPartlyContinuousAlpha.{ph} {off};\n"
                    for ph in spec.phases if ph != primary
                )
                + "    }"
            )
        pairs = " ".join(f"{spec.phases[0]}_{ph}" for ph in spec.phases[1:])
        sigma_blocks = "\n".join(
            f"    {spec.phases[0]}_{ph}\n    {{\n        type            constant;\n"
            f"        sigma           {_num(spec.surface_tension if spec.surface_tension is not None else 0.07)};\n    }}"
            for ph in spec.phases[1:]
        )
        body = (
            f"\n{_kv('phases', f'({phases})')}\n\n"
            f"{_kv('referencePhase', primary)}\n\n"
            + "\n\n".join(phase_blocks)
            + "\n\nblending\n{\n" + "\n".join(blending_lines) + "\n}\n\n"
            + f"surfaceTension\n{{\n{sigma_blocks}\n}}\n"
        )
    else:  # VoF families
        sigma = spec.surface_tension if spec.surface_tension is not None else 0.07
        header = f"\n{_kv('phases', f'({phases})')}\n\n"
        if len(spec.phases) == 2 and not spec.compressible_phases:
            # grounded: incompressibleVoF/damBreakLaminar  (scalar sigma)
            body = header + f"{_kv('sigma', _num(sigma))}\n"
        elif len(spec.phases) == 2:  # compressibleVoF/damBreak
            body = (
                header
                + "sigma\n{\n"
                + f"    type    liquidProperties;\n    phase   {spec.phases[0]};\n"
                + "}\n"
            )
        else:  # n-phase: pairwise sigmas  (damBreak4phase sorts pairs alphabetically)
            pairs = sorted(
                tuple(sorted((a, b)))
                for i, a in enumerate(spec.phases) for b in spec.phases[i + 1:]
            )
            body = (
                header
                + "sigmas\n(\n"
                + "".join(f"    ({a} {b}) {_num(sigma)}\n" for a, b in pairs)
                + ");\n"
            )
    return f"{_header('dictionary', 'phaseProperties', 'constant')}\n{body}\n\n{FOOTER}\n"


def render_dynamicmeshdict(spec: CaseSpec) -> str:
    """constant/dynamicMeshDict for movingMesh, grounded in movingMesh/SnakeRiverCanyon."""
    body = (
        "\nmover\n{\n"
        "    type            motionSolver;\n\n"
        '    libs            ("libfvMotionSolvers.so");\n\n'
        "    motionSolver    displacementSBRStress;\n\n"
        "    diffusivity     quadratic inverseDistance 1(minZ);\n"
        "}\n"
    )
    return f"{_header('dictionary', 'dynamicMeshDict', 'constant')}\n{body}\n\n{FOOTER}\n"


def render_field(field: Field, patches: list[Patch]) -> str:
    dims = "[" + " ".join(str(d) for d in field.dimensions) + "]"
    # Wall-function BCs (kqRWallFunction, epsilonWallFunction, nutkWallFunction,
    # alphatJayatillekeWallFunction, ...) require a `value` entry, and OpenFOAM
    # v13 tutorials supply one. Rather than forcing a weak local LLM to guess
    # that number ("reason free, constrain late"), the renderer defaults it to
    # the field's own internal value — a safe, valid starting guess that the
    # wall function corrects during the solve.
    _WALL_FUNCTIONS = {
        "kqRWallFunction", "epsilonWallFunction", "omegaWallFunction",
        "nutkWallFunction", "nutUSpaldingWallFunction", "nutUWallFunction",
        "alphatJayatillekeWallFunction", "alphatWallFunction",
        "compressible::alphatJayatillekeWallFunction", "compressible::alphatWallFunction",
        "v2WallFunction", "kLowReWallFunction", "calculated",
    }
    bf_parts = []
    for p in patches:
        bc = p.bc.get(field.name)
        if bc is None:
            continue
        bc_type_name = "compressible::alphatWallFunction" if bc.type == "alphatWallFunction" else ("compressible::alphatJayatillekeWallFunction" if bc.type == "alphatJayatillekeWallFunction" else bc.type)
        inner_lines = [f"        type            {bc_type_name};"]
        if hasattr(bc, "extra") and bc.extra:
            for k, v in bc.extra.items():
                if k not in ("type", "value"):
                    inner_lines.append(f"        {k:<15} {v};")
        if bc.value is not None:
            inner_lines.append(f"        value           {bc.value};")
        elif bc.type in _WALL_FUNCTIONS:
            inner_lines.append(f"        value           {field.internal_field};")
        elif bc.type in ("cyclic", "cyclicAMI"):
            inner_lines.append("        value           $internalField;")
        inner = "\n".join(inner_lines)
        bf_parts.append(f"    {p.name}\n    {{\n{inner}\n    }}")
    boundary = "\n\n".join(bf_parts)

    body = (
        f"\n{_kv('dimensions', dims)}\n\n"
        f"{_kv('internalField', field.internal_field)}\n\n"
        f"boundaryField\n{{\n{boundary}\n}}\n"
    )
    return f"{_header(field.field_class, field.name)}\n{body}\n{FOOTER}\n"


def render_setfieldsdict(spec: CaseSpec) -> str:
    """Render a default setFieldsDict for VoF phase fraction initialization."""
    phase = spec.phases[0] if spec.phases else "water"
    if isinstance(spec.geometry, BoxGeometry):
        x_min, x_max = spec.geometry.x
        y_min, y_max = spec.geometry.y
        z_min, z_max = spec.geometry.z
        # Half-fill the domain along y-axis (typical for dam break / sloshing initializations)
        y_mid = y_min + 0.5 * (y_max - y_min)
        box_min = f"({_num(x_min)} {_num(y_min)} {_num(z_min)})"
        box_max = f"({_num(x_max)} {_num(y_mid)} {_num(z_max)})"
    else:
        box_min = "(0 0 0)"
        box_max = "(1 1 1)"

    body = (
        "defaultFieldValues\n"
        "(\n"
        f"    volScalarFieldValue alpha.{phase} 0\n"
        ");\n\n"
        "regions\n"
        "(\n"
        "    boxToCell\n"
        "    {\n"
        f"        box {box_min} {box_max};\n"
        "        fieldValues\n"
        "        (\n"
        f"            volScalarFieldValue alpha.{phase} 1\n"
        "        );\n"
        "    }\n"
        ");"
    )
    return f"{_header('dictionary', 'setFieldsDict', 'system')}\n\n{body}\n\n{FOOTER}\n"


def render(spec: CaseSpec) -> CaseFiles:
    """Render a validated CaseSpec into an in-memory CaseFiles (v13)."""
    case = CaseFiles()
    # Box geometry -> blockMeshDict; external mesh -> only the case setup is rendered.
    if isinstance(spec.geometry, BoxGeometry):
        case.system["blockMeshDict"] = render_blockmeshdict(spec.geometry, spec.patches)
    case.system["controlDict"] = render_controldict(spec)
    case.system["fvSchemes"] = render_fvschemes(spec)
    case.system["fvSolution"] = render_fvsolution(spec)
    if spec.case_type in ("incompressibleVoF", "compressibleVoF", "incompressibleMultiphaseVoF", "compressibleMultiphaseVoF"):
        if "system/setFieldsDict" not in spec.extra_files:
            case.system["setFieldsDict"] = render_setfieldsdict(spec)
    # Physics property layer: base physicalProperties is replaced by per-phase files
    # for VoF/Euler families; solids and films get their own property dictionaries.
    if spec.per_phase_properties:
        for phase in spec.phases:
            case.constant[f"physicalProperties.{phase}"] = render_per_phase_physical(spec, phase)
    else:
        case.constant["physicalProperties"] = render_physicalproperties(spec)
    if spec.phases and spec.case_type != "incompressibleDenseParticleFluid":
        case.constant["phaseProperties"] = render_phaseproperties(spec)
    if spec.solid:
        pass  # solidDisplacement needs no momentumTransport
    else:
        case.constant["momentumTransport"] = render_momentumtransport(spec)
    if spec.gravity is not None:
        case.constant["g"] = render_g(spec)
    if spec.case_type == "movingMesh":
        case.constant["dynamicMeshDict"] = render_dynamicmeshdict(spec)
    for field in spec.fields:
        case.zero[field.name] = render_field(field, spec.patches)
    case.extra.update(spec.extra_files)
    return case
