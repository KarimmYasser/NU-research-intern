# Validation & the Preflight Gate

This document covers the two gateways a rendered case must pass: the **Docker
validation ladder** (`src/validation/`) and the cheaper **preflight gate**
(`src/preflight/`) that runs before it. If you are debugging a failed case,
adding a validation check, or touching how failures are classified, this is the
doc.

## 1. The Docker validation ladder (`validation/validation_agent.py`)

Runs real OpenFOAM **v13** inside Docker — `microfluidica/openfoam:13` (the
official Foundation images stop at v11; this community image provides v13, which
shares v11's case conventions). No VM or local OpenFOAM install required.

Rungs:

```
blockMesh ──▶ checkMesh ──▶ foamRun      (box geometry: full ladder)
checkMesh ──▶ foamRun                    (external mesh: skip blockMesh)
```

`run_ladder(case_dir, skip_mesh_generation)` runs rungs in order and returns the
**first failure**, or the final success as a `ValidationResult`.

### `ValidationResult` / `ValidationError`

```python
ValidationResult(ok, stage, errors=[ValidationError], log="")
ValidationError(failure_class, file_hint, message, line=None)
```

`stage` is `"blockMesh"` / `"checkMesh"` / `"foamRun"` (or `"decomposePar"` /
`"foamRun(-parallel)"`). `log` keeps the full captured output for logging /
trajectory data. `file_hint` is a case-relative path like `"system/blockMeshDict"`
or `"0/p"` when OpenFOAM names it.

### `FailureClass` (enum)

| Class | Meaning | Routed to |
|---|---|---|
| `MESH_GENERATION` | blockMesh refused to build a mesh | `build_intent` |
| `MESH_QUALITY` | checkMesh flagged quality (non-orthogonality, skewness) | `build_intent` |
| `DICTIONARY_SYNTAX` | malformed/inconsistent dict file | `patch_files` |
| `SOLVER_RUNTIME` | parsed/meshed but solver diverged or died | `build_intent` |
| `ENVIRONMENT` | Docker/OpenFOAM unavailable | `END` |
| `UNKNOWN` | could not classify | `build_intent` |

### `parse_errors()` — localize + classify (`validation_agent.py:278`)

Order of detection:

1. `"docker not found"` → `ENVIRONMENT`.
2. **Signal crash** (`#0 Foam::error::printStack`) → the pre-crash lines (the
   "Courant Number ... Time = ..." divergence clue) + the top stack frame.
3. **`--> FOAM FATAL [IO ]ERROR` block** — extracts the block, names the
   offending file + line when OpenFOAM gives them (patterns like
   `file: /case/0/p at line 21`, `keyword ... in dictionary "/case/system/..."`
   → `file_hint = path.split("case/")[-1]`), truncates to the first 6 lines.
   `is_io` flips the class to `DICTIONARY_SYNTAX`.
4. **NaN divergence** — solver printed NaN residuals but didn't exit; classifies
   `SOLVER_RUNTIME` with the NaN lines as evidence.
5. **Timeout** → `ENVIRONMENT`.
6. Otherwise `default_class` with the truncated message.

### NaN / divergence handling

A diverged solver prints `nan` residuals forever instead of crashing, which used
to burn the full timeout and get misclassified `ENVIRONMENT` (dead-ending the
graph). Now:

1. `_docker_run(stop_on_nan=True)` runs the container with `--init` and uses a
   **threaded non-blocking queue reader** to enforce wall-clock timeouts even when
   the solver hangs silently without emitting output. It **kills the container at
   the first non-finite residual** (`_NAN_RE` matches `residual = nan`, `sum local = nan`, ...).
2. stdout+stderr is retained even on timeout/NaN (`_fake_cp` carries the partial
   output) — evidence is never thrown away.
3. The failure classifies `SOLVER_RUNTIME`, routing back to `build_intent`.

### Parallel solver run (`_run_solver_parallel`, `validation_agent.py:129`)

With `nprocs > 1`, the solver rung writes a scotch `system/decomposeParDict`
(always — a stale dict from a previous run would decompose into the wrong number
of subdomains), runs `decomposePar -force`, then
`mpirun --allow-run-as-root --oversubscribe -np N foamRun -parallel`. Decomposition goes to
`processorN/` dirs inside the case; no reconstruction afterward (validation only
needs proof the solver ran). `--oversubscribe` prevents slot allocation aborts when
running on hosts where `nprocs` exceeds available Docker core assignments.

### checkMesh pass caching

`ValidationAgent._CHECKED_MESHES` caches checkMesh pass keys (md5 of the
`boundary` file + size) so a static mesh isn't re-checked on every retry.
`clear_mesh_cache()` resets it.

## 2. The preflight gate (`preflight/`)

A **cheap fail-fast gate** run *before* the full ladder so a case with a config
defect or a diverging setup never pays the cost of a full (minutes-long)
`foamRun`. See [`../architecture/preflight-gate.md`](../architecture/preflight-gate.md)
for the design; the code:

- `__init__.py` — `PreflightResult` / `PreflightVerdict` (PASS/WARN/FAIL),
  `run_preflight()` (Level A only, pure).
- `audit.py` — Level A static audit (no Docker).
- `metrics.py` — Level B checkMesh metrics parser.
- `dry_run.py` — Level C bounded dry-run + residual-slope predictor.
- `ofdict.py` — tolerant OpenFOAM-dict parsing helpers shared by the audit.
- `runner.py` — `run_preflight_with_agent()` orchestrates A → B → C.

### Level A — static audit (`audit.py`)

Walks the rendered `0/`, `system/`, `constant/` files with no Docker, flagging
config defects chosen from the concrete failure classes the pipeline has hit:

- missing `rho` relaxation factor in compressible transient fvSolution (the RHVT
  first-solve NaN root cause);
- missing energy-field solver entry for compressible `fluid` ("keyword h is
  undefined");
- missing PIMPLE/SIMPLE/PISO loop block; missing `solvers` block;
- non-positive internalFields for solved scalars (`T`, `k`, `epsilon`, `omega`,
  `h`, `e`, `p` when compressible);
- `endTime ≤ startTime`, non-positive `deltaT`;
- `ddtSchemes.default = none` (no time-marching term);
- 0/ field files missing patches that exist in the mesh boundary
  (`patch_not_in_field`) — inspects `constant/polyMesh/boundary` when available,
  falling back to parsing `system/blockMeshDict` for box geometry cases prior to meshing;
- cross-field patch-set consistency (`patch_set_mismatch`);
- compressible `thermophysicalProperties` present and sane (positive molWeight/Cp).

Each finding is `AuditFinding(code, level, message)` with a `code` that maps to a
known root cause. `ofdict.py` provides tolerant `section()`, `entries()`, `keys()`
— deliberately *not* a full OpenFOAM parser.

### Level B — checkMesh metrics (`metrics.py`)

Runs blockMesh/checkMesh (cached for the ladder) and parses the stdout into
`MeshMetrics` (cells, non-orthogonality max/avg, skewness, aspect ratio, min/max
volume, face areas, `failed_checks`, `ok`). `evaluate_mesh_metrics()` compares
against OpenFOAM's own thresholds (non-ortho warn >70, skew >4, aspect >1000),
degrading to FAIL above 2×.

### Level C — bounded dry-run (`dry_run.py`)

`bounded_run_predict()` runs the solver for a small, bounded number of steps
(controlDict's `endTime` is patched to `startTime + steps·deltaT`,
`writeControl` to `none`, then restored in a `finally`), and the ValidationAgent
already aborts on the first NaN. `parse_residuals()` groups per-field *initial*
residuals by `Time = ...` step; `predict_from_residuals()` judges by **slope**:

- log10-residual slope ≤ −0.05/step → decaying, pass;
- slope ≥ +0.05/step → divergence predicted, FAIL;
- flat/stalled between → WARN (a flat residual is not a pass);
- residual already ≤ 1e-6 → converged, pass regardless;
- any non-finite residual anywhere → FAIL.

### Orchestration (`runner.py`)

`run_preflight_with_agent(case_dir, agent, dry_run_steps=5)`:

1. Level A; FAIL → return immediately with the audit as evidence.
2. Ensure a mesh exists (blockMesh box cases if needed), then Level B; an
   `ENVIRONMENT` failure (Docker unavailable) → **env-skip**: defer to the ladder
   (which will surface it) rather than fail the case.
3. Level C; FAIL → `verdict=FAIL`, WARN → `WARN`, else `PASS`.

`PreflightResult.as_evidence()` renders a compact multi-line evidence block used
as `error_feedback` in the graph. The graph routes `FAIL` back to
`build_intent`; env-skip / PASS / WARN proceeds to the ladder.

## 3. Env vars that tune both

| Env var | Default | Meaning |
|---|---|---|
| `FOAM_DOCKER_IMAGE` | `microfluidica/openfoam:13` | OpenFOAM v13 container image |
| `FOAM_VALIDATION_TIMEOUT` | `600` | wall-clock timeout per docker command (seconds) |
| `FOAM_VALIDATION_PROCS` | `1` | ranks for `foamRun -parallel` (1 = serial) |

## 4. Gotchas specific to this layer

- **`checkMesh` often exits 0 even on quality failures** — `run_check_mesh`
  inspects the output for `***` lines and `"Failed N mesh checks"` rather than
  trusting the exit code.
- **`_docker_run` passes host absolute paths** (`-v {host}:/case`) — in the
  Docker-deployed pipeline the workspace bind mount must be path-aligned
  (see `../infrastructure/vm-deploy-bundle.md`).
- **`_fake_cp`** turns timeout/NaN into a `CompletedProcess` with `returncode=1`
  carrying the partial output, so `parse_errors` can still see the evidence.
- `decomposeParDict` is **always rewritten** for the current `nprocs` — a stale
  dict decomposes into the wrong subdomain count and the run fails.
- Tests mock the ladder at the Docker boundary; the only way to exercise the real
  parallel ladder is a real Docker run with `FOAM_VALIDATION_PROCS` set.