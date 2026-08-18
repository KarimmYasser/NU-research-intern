# Testing & Conventions

How the test suite is organized, how to run it, and the conventions/gotchas that
keep the codebase coherent. Read the conventions section before you write code.

## 1. Running the tests

```bash
# From the repo root, with your Python environment active
# and `pip install -r requirements.txt` done:
pytest -q

# or against a specific interpreter regardless of what's active:
/path/to/python -m pytest -q
```

There are also two **non-pytest** suites for the harness (see
[`agent-harness-and-pi.md`](agent-harness-and-pi.md) §9), both offline:

```bash
node tests/pi_adapter/adapter_test.mjs   # pi adapter <-> real Python harness
bash tests/shell/installer_test.sh       # ./setup.sh / ./start.sh behaviour
./scripts/test_harness.sh                # all of the above + tool smoke tests
```

The suite is **267 tests, fully offline, ~3s** — no Docker or LLM at test time;
LLM and docker interactions are mocked at the client/ladder boundary. Run it on
any renderer change even if it "feels" obvious — the renderer is where silent
typo regressions hide.

There is no wired-up lint/typecheck gate in CI, but `pyproject.toml` ships
`[tool.pyright] extraPaths = ["src"]` — a pyright pass is a free sanity check.

## 2. Test coverage by file

| File | Covers |
|---|---|
| `test_intent.py` | `_assign_faces` tiling, `_turbulence_values`, field sets, `intent_to_spec` expansion, time control |
| `test_pipeline.py` | `extract_intent` stages, `_extract_json` / `_extract_fenced_json` repair, patch extraction |
| `test_harness.py` | MCP schemas, workspace confinement + `--workspace-dir`, single-source agent rules, guard against pre-v13 conventions in guidance |
| `tests/pi_adapter/adapter_test.mjs` | (node) adapter registers 19 tools, injects v13 prompt guidelines, round-trips a tool call, kills the child process on shutdown |
| `tests/shell/installer_test.sh` | (bash) pi-first default, idempotency, settings preservation/backup, error messages, skill-symlink repair |
| `test_intent_validator.py` | BC registry checks, relaxation factors, dict-syntax brace check, energy-solver enforcement, box-geometry faces |
| `test_envelopes.py` | in-range pass-through, clamps, direction-preserving velocity rescale, zero-velocity no-op, reject-band unit errors, species-derived nu (accept/reject/unknown fallback/T-dependence) |
| `test_rag_skills.py` | keyword-overlap RAG, species thermo lookup, the two skills |
| `test_renderer.py` | exact rendered dictionaries for the main families (box blockMeshDict, controlDict, fvSchemes/fvSolution, physicalProperties, momentumTransport, phaseProperties, fields) |
| `test_compressible_vortex_tube.py` | the end-to-end compressible flow case (RHVT-style) as a golden render |
| `test_mesh_io.py` | boundary-file parsing, copy, layout detection |
| `test_preflight.py` | Level A audit findings (incl. rho-relaxation, energy-solver, patch-coverage), checkMesh metrics parsing, residual predictor (NaN, rising/decaying/flat slopes), env-skip deferral |
| `test_validation.py` | ladder skip logic, failure classification, NaN-abort `_docker_run`, partial-output retention on timeout, cache |
| `test_graph.py` | LangGraph node wiring, routing tables (DICTIONARY_SYNTAX→patch, ENVIRONMENT→END, intent_error→END, preflight FAIL→build_intent), patch_stats |

To exercise the real parallel ladder locally, set `FOAM_VALIDATION_PROCS` (and
`FOAM_VALIDATION_TIMEOUT`) and run a case through `graph.run` — the ladder is the
only part tests deliberately mock.

## 3. Conventions

- **v13 first.** Files and field conventions must match OpenFOAM **Foundation
  v13**: solver *modules* named in `controlDict` (not old binaries like
  `icoFoam`), properties in `constant/physicalProperties` +
  `constant/momentumTransport`. See `docs/domain/openfoam/case-structure.md`.
- **Never let the LLM write dictionary text** unless it is the *targeted file
  patch* path (`patch_files`), which is bounded and route-gated for a reason.
- **Mesh boundary names are authoritative.** With an external mesh, patch names
  come from the real `boundary` file; the prompt/model must reuse them verbatim.
- **Mutate `CaseIntent` only in deterministic code.** Skills and the expander may
  change it; the LLM JSON path is validated before it counts.
- **State resets on retry:** `file_overrides` are cleared in `build_intent` so a
  full re-render happens clean; the patch loop caps at 3 before falling back.
- **Presets are paired:** every `_FVSCHEMES` key must have a matching
  `_FVSOLUTION` key, or the renderer silently falls back.
- **Envelope everything:** any numeric field the LLM can emit must have an entry
  in `physical_envelopes.json`.
- **Prefer JSON knowledge over code.** Most "the agent knows X" work is a
  registry entry (`knowledge/*.json`), not a code change.
- **Docs live in classified topic folders** (`docs/domain`, `docs/development`,
  `docs/architecture`, `docs/guides`, ...); `docs/README.md` is the index. New
  knowledge or renderer behaviour should be reflected there, not just in code
  comments.
- **This is a spike.** Prefer a slightly rough but *demonstrably running*
  improvement over a clean one you can't validate — the pipeline's whole claim
  to correctness rests on the Docker ladder.

## 4. Gotchas

- **`.env` is gitignored**; only `.env.example` is committed. Never commit real
  keys.
- The `src/` sys.path shim (`__main__.py`, and each test file) means modules are
  importable from the repo root without an install; `pyproject.toml`
  (`pythonpath = ["src"]`) makes `pytest` behave the same.
- `KnowledgeRetriever` / `IntentValidator` / `EnvelopeConstraint` load JSON at
  **construction** — after editing a registry wit a long-running process, rebuild
  the object (or re-import) before asserting new behavior.
- **`graph.route_after_preflight` keys on the env-skip message string** from
  `preflight/runner.py` — changing that message breaks the routing test.

## 5. Further reading

- [`docs/development/overview.md`](overview.md) — architecture in one pass.
- [`docs/development/extending.md`](extending.md) — concrete extension recipes.
- [`docs/guides/agent-usage-guide.md`](../guides/agent-usage-guide.md) — CLI/API
  reference (the practical run documentation).
- [`docs/architecture/phase-ii-architecture.md`](../architecture/phase-ii-architecture.md)
  — the design rationale.

## Coverage as a first-class signal

Level C (`run_bounded_dry_run`) shipped completely broken for weeks: it passed the
`ValidationAgent` object where a callable was expected, so every call raised. No test
caught it because **no test called it at all**. Coverage is therefore treated as a
defect-prediction signal here, not a vanity metric.

```bash
./scripts/coverage_report.sh        # report
./scripts/coverage_report.sh 75     # fail under a floor
```

Current: **79%** overall. The audit that followed the incident found the risk was
concentrated exactly where Docker or an LLM is normally required — the code paths that
are awkward to test are the ones that break:

| Module | Was | Now | Why it mattered |
|---|---|---|---|
| `preflight/runner.py` | 14% | 74% | `_bounded_foamrun` **rewrites the user's controlDict** and must restore it byte-for-byte even when the solver crashes or times out |
| `validation/validation_agent.py` | 67% | 80% | checkMesh reports failure in its *text* while exiting 0; the pass cache must not hand a stale PASS to an edited mesh |
| `harness/tools.py` | 62% | 62%¹ | 7 of 15 tools had no test mention at all |

¹ smoke sentinels added for the 7 uncovered tools; the percentage barely moves because
they are shallow by design — they assert the contract, not the behaviour.

Still thin and known: `orchestration/pipeline.py` (54%, CLI wiring),
`harness/mcp_server.py` (61%, JSON-RPC transport), `knowledge/rebuild_tutorials.py` (0%,
an offline data-generation script).

The lesson worth carrying into the rebuild: **test the awkward paths first**. Stub the
container call rather than skipping the test — every gap listed above was closable
without Docker.
