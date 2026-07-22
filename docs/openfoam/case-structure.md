# OpenFOAM Case Structure — Annotated, from a Real Case

Every file below is reproduced verbatim from `temp/openfoam-repo/tutorials/incompressibleFluid/cavity/` — the official "lid-driven cavity" tutorial from the OpenFOAM Foundation's own v13 repo (cloned locally, see `docs/openfoam/README.md`). This is the canonical first case every OpenFOAM user learns on. Nothing here is paraphrased from a website; it's the actual dictionary files, annotated.

The case models a square 2D box of fluid where the top wall drags the fluid along (`movingWall`), and the other three walls are stationary (`fixedWalls`).

```
cavity/
├── 0/
│   ├── U            <- velocity field
│   └── p            <- pressure field
│       (also k, epsilon, omega, nut, nuTilda — turbulence fields, unused
│        here since this case runs laminar, but present because the
│        generic v13 solver expects them to exist)
├── constant/
│   ├── momentumTransport
│   └── physicalProperties
└── system/
    ├── blockMeshDict
    ├── controlDict
    ├── fvSchemes
    └── fvSolution
```

## `0/U` — the velocity field

```
FoamFile
{
    format      ascii;
    class       volVectorField;
    object      U;
}

dimensions      [0 1 -1 0 0 0 0];

internalField   uniform (0 0 0);

boundaryField
{
    movingWall
    {
        type            fixedValue;
        value           uniform (1 0 0);
    }

    fixedWalls
    {
        type            noSlip;
    }

    frontAndBack
    {
        type            empty;
    }
}
```

Reading this line by line:
- `class volVectorField` — U is a vector field (3 components per cell); pressure below is `volScalarField` instead.
- `dimensions [0 1 -1 0 0 0 0]` — OpenFOAM's SI dimension-checking array: `[kg m s K mol A cd]` exponents. `[0 1 -1 0 0 0 0]` = m¹s⁻¹ = velocity. If your agent gets this wrong, OpenFOAM refuses to run — this is free, automatic sanity-checking your syntax-validation agent can lean on.
- `internalField uniform (0 0 0)` — the whole domain starts at rest.
- `boundaryField` — **one entry per patch name**, and those names (`movingWall`, `fixedWalls`, `frontAndBack`) must exactly match the patch names defined in `system/blockMeshDict`'s `boundary` block. This cross-file name matching is the single most common place an LLM-generated case breaks.
- `movingWall` gets `fixedValue (1 0 0)` — constant velocity of 1 m/s in x, driving the flow.
- `fixedWalls` gets `noSlip` — zero velocity, the standard no-slip wall condition.
- `frontAndBack` gets `empty` — this marks the case as effectively 2D (no resolution in the z-direction); required on any patch normal to a direction you're not simulating.

## `0/p` — the pressure field

```
class       volScalarField;
object      p;

dimensions      [0 2 -2 0 0 0 0];

internalField   uniform 0;

boundaryField
{
    movingWall  { type zeroGradient; }
    fixedWalls  { type zeroGradient; }
    frontAndBack { type empty; }
}
```

- `dimensions [0 2 -2 0 0 0 0]` = m²s⁻² — this is *kinematic* pressure (p/ρ), the OpenFOAM incompressible-solver convention, not Pascals. A common source of unit confusion if you're translating a physics prompt that specifies pressure in Pa.
- `zeroGradient` on all walls — the standard incompressible-flow pressure boundary condition at solid walls (no flux through the wall).

## `constant/physicalProperties` — fluid properties

```
class       dictionary;
object      physicalProperties;

viscosityModel  constant;

nu              1e-05 [m^2/s];
```

Just the kinematic viscosity of the fluid (here, roughly air/water-like at 1e-5 m²/s). **Naming trap:** older OpenFOAM versions (and most tutorials/papers still online) call this file `transportProperties`. v13 renamed it. If your agent pulls few-shot examples from older sources, it may generate the wrong filename.

## `constant/momentumTransport` — turbulence model selection

```
class       dictionary;
object      momentumTransport;

simulationType  RAS;

RAS
{
    model           kEpsilon;
    turbulence      on;
}
```

Same v13 rename applies here — older versions call this `turbulenceProperties`. Note this cavity case sets up a turbulence model even though the case is small/laminar-Reynolds-number in practice; the generic v13 solver framework expects this file to exist regardless.

## `system/controlDict` — run control

```
solver          incompressibleFluid;

startFrom       startTime;
startTime       0;
stopAt          endTime;
endTime         10;
deltaT          0.005;

writeControl    timeStep;
writeInterval   100;
purgeWrite      0;

writeFormat     ascii;
writePrecision  6;
writeCompression off;

timeFormat      general;
timePrecision   6;

runTimeModifiable true;
```

**Important architecture note for v13**: `solver incompressibleFluid;` — this is a real, current difference from almost every OpenFOAM tutorial/paper published before the v12/v13 restructuring. Older versions pick a solver by running a specific binary (`icoFoam`, `simpleFoam`, `pimpleFoam`, etc.) and `controlDict` has an `application` entry naming it. **v13 unifies this**: `controlDict` names the (modular) solver via the `solver` keyword, and the case is executed with a generic runtime-selectable runner rather than calling a solver-specific binary directly. If your literature-review papers or few-shot examples predate this change (most do — OpenFOAMGPT, Foam-Agent etc. were all built against older/ESI-branch conventions), expect your agent to default to the old `application` + solver-binary pattern unless you explicitly correct for it. This is worth resolving early and deliberately, not discovering by accident mid-Phase-III.

## `system/fvSchemes` — discretization

```
ddtSchemes    { default Euler; }
gradSchemes   { default Gauss linear; }
divSchemes
{
    default         none;
    div(phi,U)      Gauss limitedLinearV 1;
    ...
}
laplacianSchemes { default Gauss linear corrected; }
interpolationSchemes { default linear; }
snGradSchemes { default corrected; }
```

Picks the numerical scheme for each mathematical operator (time derivative, gradient, divergence, Laplacian) per field. `default none;` under `divSchemes` is deliberate — OpenFOAM forces you to be explicit about divergence schemes per-field rather than silently applying a default, since divergence scheme choice has a big stability impact.

## `system/fvSolution` — linear solver configuration

```
solvers
{
    p { solver GAMG; tolerance 1e-06; relTol 0.1; smoother GaussSeidel; }
    pFinal { $p; tolerance 1e-06; relTol 0; }
    "(U|k|epsilon|omega|R|nuTilda).*"
    {
        solver smoothSolver; smoother GaussSeidel;
        tolerance 1e-05; relTol 0;
    }
}

PIMPLE
{
    nCorrectors 2;
    nNonOrthogonalCorrectors 0;
    pRefCell 0;
    pRefValue 0;
}
```

- Per-field linear solver choice (`GAMG` for pressure — a multigrid solver well-suited to elliptic pressure equations; `smoothSolver` for everything else).
- The regex key `"(U|k|epsilon|omega|R|nuTilda).*"` applies one solver config to a whole group of fields at once — useful pattern to know when generating this file, since it means the agent doesn't need to enumerate every field individually.
- `PIMPLE` block controls the pressure-velocity coupling algorithm's iteration counts — this is where instability shows up if your generated case doesn't converge.
