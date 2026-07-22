# blockMesh — Annotated, from Real Cases

Source: the `blockMesh` utility's own header comment (`temp/openfoam-repo/applications/utilities/mesh/generation/blockMesh/blockMesh.C`), plus two real dictionaries from the repo: the minimal `cavity` case and the advanced `tutorials/mesh/blockMesh/pipe` case.

## What blockMesh actually is (from its own source)

```
Application
    blockMesh

Description
    A multi-block mesh generator.

    Uses the block mesh description found in
      - system/blockMeshDict
      - system/<region>/blockMeshDict
      - constant/polyMesh/blockMeshDict
      - constant/<region>/polyMesh/blockMeshDict

Usage
    blockMesh [OPTION]

    Options:
      -blockTopology       Write the topology as a set of edges in OBJ format.
      -region <name>       Specify an alternative mesh region.
      -dict <filename>      Specify alternative dictionary for the block mesh description.
      -noClean              Keep the existing files in the polyMesh (undocumented in header comment,
                             but present as a bool option in the source)
```

`-blockTopology` is worth knowing about specifically: it dumps the block edges as an OBJ file you can open in a viewer to sanity-check block connectivity *before* running the full mesh generation — useful for an agent's self-check step before attempting the expensive full mesh.

## Minimal example: the cavity's `blockMeshDict`

```
convertToMeters 0.1;

vertices
(
    (0 0 0)
    (1 0 0)
    (1 1 0)
    (0 1 0)
    (0 0 0.1)
    (1 0 0.1)
    (1 1 0.1)
    (0 1 0.1)
);

blocks
(
    hex (0 1 2 3 4 5 6 7) (20 20 1) simpleGrading (1 1 1)
);

boundary
(
    movingWall
    {
        type wall;
        faces ( (3 7 6 2) );
    }
    fixedWalls
    {
        type wall;
        faces ( (0 4 7 3) (2 6 5 1) (1 5 4 0) );
    }
    frontAndBack
    {
        type empty;
        faces ( (0 3 2 1) (4 5 6 7) );
    }
);
```

Line by line:
- `convertToMeters 0.1` — a global scale factor applied to every vertex coordinate below. Here, the 1x1x0.1 unit box actually becomes 0.1m x 0.1m x 0.01m. Lets you write vertices in convenient round numbers and scale once, instead of writing real-world decimals everywhere.
- `vertices` — 8 points, indexed **0 through 7** in the order they're listed. This index is how `blocks` and `boundary` refer back to them below — there's no coordinate lookup, just position in the list.
- `blocks` — `hex (0 1 2 3 4 5 6 7)` means: build one hexahedral block whose 8 corners are vertices 0,1,2,3 (bottom face) and 4,5,6,7 (top face, in the same rotational order as the bottom). **Vertex ordering matters** — get it backwards and you get a mesh with negative volume cells, which `blockMesh`/`checkMesh` will reject. `(20 20 1)` = divide this block into 20 cells in the first direction, 20 in the second, 1 in the third (i.e., no resolution in z — consistent with this being a 2D case). `simpleGrading (1 1 1)` = uniform cell size in each direction (no refinement clustering near any edge).
- `boundary` — three named patches, each listing which **quad faces** (by vertex index, 4 numbers per face) belong to it, and each face's `type`:
  - `wall` — a solid boundary (used for both `movingWall` and `fixedWalls` here; the actual velocity behavior — moving vs. fixed — is set separately in `0/U`, not here. `blockMeshDict` only defines geometry and patch *type*, never physics.)
  - `empty` — marks `frontAndBack` as not resolved (this is what makes the case 2D despite having a real 3D mesh with 1 cell of depth).

**The single most important cross-file rule**: the patch names here (`movingWall`, `fixedWalls`, `frontAndBack`) must appear as *exactly* the same strings in every field file's `boundaryField` block (`0/U`, `0/p`, etc. — see `docs/openfoam/case-structure.md`). A mismatch here is probably the single most common category of bug in an LLM-generated case, and it's silent until the solver refuses to start.

## Advanced example: named/projected vertices (`tutorials/mesh/blockMesh/pipe`)

For curved or complex geometry, `blockMeshDict` supports a more powerful syntax than the minimal example above — worth knowing exists even if you don't need it for the concept note's simple test prompts:

```
geometry
{
    cylinder
    {
        type cylinder;
        point1 (0 -4 0);
        point2 (0 4 0);
        radius 0.7;
    }
    ...
}

vertices
(
    name v0 project (-1 -0.1 -1) (cylinder cylinder2)
    name v1 project ( 1 -0.1 -1) (cylinder)
    ...
);

edges
(
    project v0 v1 (cylinder)
    ...
);

faces
(
    project (v0 v4 v7 v3) cylinder
    ...
);

defaultPatch
{
    name walls;
    type wall;
}
```

Differences from the minimal form:
- A top-level `geometry` block defines analytic shapes (cylinders, planes, etc.) by name.
- Vertices can be **named** (`name v0`) instead of only referenced by index, and **projected** onto a named geometry (`project (coords) (cylinder cylinder2)`) — the raw coordinate is a starting guess, and blockMesh snaps it onto the actual analytic surface. This is how you get curved edges/faces without hand-computing every point.
- `edges` and `faces` sections project straight block edges/faces onto the curved geometry too, so the resulting hex mesh actually curves to match the cylinder instead of staying flat-faced.
- `defaultPatch` catches any block face not explicitly assigned to a named patch — useful as a safety net, less useful as something to rely on deliberately (explicit patches are easier for your Syntax Validation agent to reason about).

This projected/named-vertex style is overkill for parametric test prompts like "flow over a cylinder at 2 m/s" if you can approximate the domain as straight hex blocks, but it's the right tool once geometry stops being expressible as flat-faced boxes.

## Workflow

```
blockMesh                 # reads system/blockMeshDict -> writes constant/polyMesh
blockMesh -blockTopology  # sanity-check block connectivity as OBJ, before full generation
checkMesh                 # validates the generated mesh (non-orthogonality, skewness, closed volume, etc.)
```

This `blockMesh` → `checkMesh` pair is exactly the "dry run" the concept note's Phase III describes. Practical implication for your correction loop: `blockMesh` failures are almost always about vertex ordering or block/geometry consistency (fix by re-deriving vertex order), while `checkMesh` failures are about mesh *quality* on an otherwise-valid mesh (fix by adjusting grading/cell counts) — these are different failure classes and probably deserve different repair strategies in your Syntax Validation agent rather than one generic "retry" path.
