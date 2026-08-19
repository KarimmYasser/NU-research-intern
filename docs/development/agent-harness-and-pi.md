# The Agent Harness and the Pi Integration

Developer reference for the **second entry point** into this repo. Everything else in
`docs/development/` documents the `src/` pipeline, which you reach through a command-line
script (`python -m src run ...`) and which drives a local LLM through a LangGraph graph.

This document covers the other way in: a **tool server** that an existing coding agent
drives. We do not build a user interface here — pi supplies the chat window, and we supply
19 tools plus a skill that tells its model how to use them.

> **Status.** Spike maturity, like the rest of `src/`. It puts the domain tooling in a
> CFD engineer's hands today and shakes out failure modes — and it is the most legible
> demonstration of the project. What it is *not* is the automated deliverable: it
> depends on the host agent's model choosing the right steps, whereas the pipeline
> guarantees them structurally. Use it to explore and to demo; use the pipeline for
> repeatable generation.

## Table of contents

1. [Two entry points, one domain core](#1-two-entry-points-one-domain-core)
2. [Who defines the workflow](#2-who-defines-the-workflow)
3. [Component map](#3-component-map)
4. [The Pi adapter](#4-the-pi-adapter)
5. [Rules: one file, three consumers](#5-rules-one-file-three-consumers)
6. [The v13 guardrail and why it is duplicated](#6-the-v13-guardrail-and-why-it-is-duplicated)
7. [Workspace confinement](#7-workspace-confinement)
8. [Scripts](#8-scripts)
9. [Tests](#9-tests)
10. [Extending](#10-extending)
11. [Gotchas](#11-gotchas)

---

## 1. Two entry points, one domain core

```
   CLI script                ┌───────────────────────────────┐
   python -m src run  ──────►│  src/orchestration/graph.py   │  LangGraph: deterministic
   (+ local Ollama/vLLM)     │  (the deliverable's pipeline) │  node/edge control flow
                             └───────────────┬───────────────┘
                                             │
                    shared domain core (unchanged by either entry point)
                    ir/ · rendering/ · knowledge/ · validation/ · preflight/
                                             │
   pi's chat window          ┌───────────────┴───────────────┐
   (not ours) ──────────────►│  src/harness/mcp_server.py    │  MCP: 19 tools, no
                             │  (a tool server, not a UI)    │  control flow of its own
                             └───────────────────────────────┘
```

`src/harness/tools.py` imports the same `KnowledgeRetriever`, renderer, validation
ladder and preflight gate the graph uses. Fixing a renderer bug fixes both.

## 2. Who defines the workflow

This is the single most important difference, and it is easy to get wrong.

| | `src/orchestration/graph.py` | harness + host agent |
| :--- | :--- | :--- |
| Workflow lives in | code (`add_node` / `add_conditional_edges`) | prose (`src/harness/rules/openfoam.md`) |
| Sequence | `read_mesh → build_intent → expand_spec → render_case → preflight_case → validate_case` | whatever the model decides |
| Failure routing | by failure class: `ENVIRONMENT→END`, `DICTIONARY_SYNTAX→patch_files`, `MESH_*/SOLVER_*→build_intent` | the model may or may not call `diagnose_case_failure` |
| Enforced by | the LangGraph runtime | nothing |
| LLM's freedom | fills a typed `CaseIntent` at two nodes | chooses every step |

The harness enforces **safety invariants** in Python (workspace confinement,
`scaffold_case` refusing a non-empty directory without `overwrite`, `clean_case_directory`
supporting `dry_run`). It does **not** enforce **sequence**. Nothing stops a model from
calling `validate_case_docker` before `run_preflight_audit`.

That is acceptable only because this entry point targets a strong hosted model. The
deliverable targets a weak local model, which is exactly why the graph compiles the
sequence into code ("reason free, constrain late" —
[`../architecture/phase-ii-architecture.md`](../architecture/phase-ii-architecture.md)).
**Do not "simplify" the graph into prose because the harness works.**

## 3. Component map

```
src/harness/
├── mcp_server.py            JSON-RPC 2.0 stdio server; 19 tool schemas; prompts/get
├── tools.py                 OpenFOAMToolbox — the tool implementations
└── rules/
    ├── openfoam.md          CANONICAL agent rules (frontmatter + workflow)
    └── references/
        └── v13-vs-older.md  on-demand v13 vs v9/ESI reference

integrations/pi/             project-local pi package
├── package.json             pi manifest (extensions + skills)
├── extensions/
│   └── openfoam-tools.ts    the adapter (child process + JSON-RPC + tool bridge)
└── skills/openfoam/
    ├── SKILL.md    → symlink to src/harness/rules/openfoam.md
    └── references/ → symlink to src/harness/rules/references/

scripts/
├── setup_harness.sh         ./setup.sh
├── run_agent.sh             ./start.sh
├── check_harness.sh         ./check.sh
├── test_harness.sh          smoke diagnostics
└── agents/
    ├── common.sh            paths, logging, docker probe
    └── pi.sh                pi detection, adapter install, skill sync, launch
```

## 4. The Pi adapter

Pi does not consume MCP config files; it loads **extensions** and **skills**. The
adapter bridges the two worlds without reimplementing any tool in TypeScript.

Lifecycle:

1. **Locate the repo** — `OPENFOAM_HARNESS_REPO`, else walk up from the module looking
   for `src/harness/mcp_server.py`. Works regardless of pi's cwd.
2. **Discover tools** — spawn a *short-lived* harness process, `initialize`, `tools/list`,
   kill it. Pi's docs forbid starting long-lived resources in the extension factory
   (a factory may run in an invocation that never opens a session).
3. **Register** — every returned definition becomes a pi tool. The harness publishes JSON
   Schema, which is the same wire shape typebox emits, so `inputSchema` is passed through.
4. **Execute** — on the first tool call, start the long-lived harness
   (`python -m harness.mcp_server --workspace-dir <repo>/workspace`, `PYTHONPATH=<repo>/src`)
   and forward `tools/call`. Harness errors are re-thrown so pi marks the call failed.
5. **Shutdown** — `session_shutdown` closes stdin, `SIGTERM`, then `SIGKILL` after 2 s.

Pending requests are rejected if the child exits, with the last ~40 stderr chunks
attached, so a Python traceback reaches the user instead of a hang.

## 5. Rules: one file, three consumers

`src/harness/rules/openfoam.md` is the **only** copy of the agent rules.

| Consumer | How it gets them |
| :--- | :--- |
| Pi | loads `integrations/pi/skills/openfoam/SKILL.md`, a symlink to the canonical file |
| Cline / Claude (MCP) | `prompts/get` → `load_agent_rules()` reads the file and strips the YAML frontmatter |
| Humans | read the canonical file |

The frontmatter (`name`, `description`) lives in the canonical file because the skill
loader requires it; `_strip_frontmatter()` removes it for MCP clients.

Symlinks do not survive every transfer (Windows git without developer mode, zip
downloads, `rsync` without `-l`). `pi_sync_skill()` in `scripts/agents/pi.sh` repairs
this on every `./setup.sh`: an intact symlink is left alone, anything else is replaced
with a fresh copy, and a stale copy is re-synced. A pytest guard accepts either a
symlink or a byte-identical copy, and fails on a drifted copy.

**Never add a second rules file.** That was the state before this was collapsed, and the
two copies had already drifted.

## 5b. Preflight scepticism and hand-editing

Two rules were added after the RHVT re-verification (2026-08-18), where our own Level A
audit produced a false FAIL that hid the only working configuration:

- **Findings are evidence, not verdicts.** If a case runs and its residuals fall, the run
  wins over the checker. The agent is told to report the finding as a likely false
  positive rather than rewrite the case to satisfy it. A PASS is likewise not a promise
  of convergence.
- **Direct editing is expected work.** Rendering gets a case close; relaxation, solver
  settings and time-step are hand-tuning. The rules cover reading before writing, one
  change at a time, and explaining the change in CFD terms.

Archiving is **not** left to the model. `validate_case_docker` calls
`validation.snapshot.snapshot_run` whenever the ladder passes, copying `system/`, `0/`
and `constant/` (minus the mesh) plus the solver log into `<case>/.runs/<timestamp>/`,
and returns the path. Measured: **64 kB from the 374 MB RHVT case**, 176 kB for a
1000-step cavity (long logs keep head + tail only). Ten snapshots are retained per case.

The graph's `validate_case` node does the same thing, so the guarantee does not depend
on which entry point produced the run. A snapshot failure is reported but never fails a
validation that passed, and `clean_case_directory` leaves `.runs/` alone — all pinned by
tests. This is the split the harness follows throughout: sequence is prose, anything that
must not be forgotten is code.

Residual triage is given as a **symptom table** — NaN in the first solve, rising from
step 1, one field stalling while others decay, everything flat — mapping to where to
look. No numeric recipe: a copyable relaxation value would be pasted into unrelated
cases, the same failure mode the v13 guardrail avoids.

Both rules also appear as always-on `promptGuidelines` on `run_preflight_audit` and
`run_bounded_dry_run`, since skills are progressively disclosed and may never be read.
Adapter and Python tests assert their presence *and* the absence of hardcoded values.

## 6. The v13 guardrail and why it is duplicated

Models are trained overwhelmingly on OpenFOAM 9 and ESI/openfoam.com material. Verified
against `src/knowledge/v13_tutorials.json` (~200 real v13 cases):

| Pre-v12 | v13 | Corpus |
| :--- | :--- | :--- |
| `application icoFoam;` | top-level `solver <module>;`, run by `foamRun` | 0 vs 177 |
| `constant/transportProperties` | `constant/physicalProperties` | 0 vs 727 |
| `constant/turbulenceProperties` | `constant/momentumTransport` | 0 vs 671 |

The correction is deliberately in **two** places:

- **`src/harness/rules/`** — full detail, loaded on demand.
- **`promptGuidelines`** on `write_case_file` / `scaffold_case` in the adapter — always in
  the system prompt.

The duplication is intentional and was measured. Skills use *progressive disclosure*:
only the description sits in the prompt, and the model must choose to `read` SKILL.md.
When it does not, it reverts to v9 — observed output included `application foamRun;` and
an invented `constant/physicsProperties`. With the prompt guideline present, the same
prompt yields the correct `solver` entry without any skill read.

**Keep the guidance structural, not literal.** An earlier version said
``solver incompressibleFluid;`` as the example; a model asked for a dam-break then copies
that literally instead of `incompressibleVoF`. The guideline now names no module and
points at `list_case_types`. Tests assert the absence of a hardcoded module.

## 7. Workspace confinement

`OpenFOAMToolbox` resolves every path against `workspace_dir` and raises if a path
escapes it. The server accepts `--workspace-dir` (also `--workspace-dir=<path>`) and
creates the directory at startup; without it the toolbox falls back to `os.getcwd()`,
which is non-deterministic for a GUI-launched agent.

The adapter always passes `<repo>/workspace` (override with `OPENFOAM_WORKSPACE_DIR`).
So pi runs from the repo root — seeing `.pi/settings.json`, skills, `AGENTS.md` — while
harness tools can only touch `workspace/`.

## 8. Scripts

Public entry points are three one-line wrappers (`setup.sh`, `start.sh`, `check.sh`) over
`scripts/`. Rationale: a non-technical user should not need `--agent`, `PYTHONPATH`, or
knowledge of MCP.

`setup.sh` is Pi-first and idempotent: venv, dependencies, workspace, Python self-test,
skill sync, `pi install ./integrations/pi -l`, adapter self-test. It writes only
`.pi/settings.json` (backed up first) and never touches global pi settings or
credentials. It distinguishes *pi missing* / *pi unauthenticated* / *adapter missing* /
*python broken* / *docker unavailable* — Docker being down is a warning, not a failure.

Full output goes to `.harness/logs/`; the console stays plain-language unless `--verbose`.

## 9. Tests

| Suite | Command | Covers |
| :--- | :--- | :--- |
| Harness unit tests | `pytest tests/test_harness.py` | schemas, workspace confinement, `--workspace-dir`, single-source rules, no-v9-conventions guard |
| Pi adapter | `node tests/pi_adapter/adapter_test.mjs` | real Python harness: registers 19 tools, prompt guidelines, `list_case_types` round-trip, workspace path, error propagation, child termination |
| Installer/launcher | `bash tests/shell/installer_test.sh` | pi default, idempotency, settings preservation + backup, missing-pi message, docker warning, spaces in paths, out-of-repo invocation, skill repair |
| Everything | `./scripts/test_harness.sh` | the above plus tool smoke tests |

Setup verifies that the extension can be loaded before registering it with Pi. It prefers
Node's native type stripping and falls back to the `tsx` executable. That is why the
extension must avoid TypeScript syntax that strip-only mode rejects — **no parameter
properties, no enums, no namespaces**. Runtime imports are node built-ins only; pi types
are `import type` so they erase.

An end-to-end `pi --print` check is possible but needs a configured model, so it stays
manual. The adapter test is the deterministic gate.

## 10. Extending

**Add a tool.** Implement it on `OpenFOAMToolbox`, register schema + handler in
`_register_tools()`. Both entry points pick it up; the adapter registers it automatically.
Update the tool count in the rules and in `tests/test_harness.py`.

**Change agent behaviour.** Edit `src/harness/rules/openfoam.md` only.

**Add an always-on instruction.** Add to `PROMPT_GUIDELINES` in the adapter, keyed by tool
name. Use sparingly: each bullet is in the prompt for the whole session, and pi appends
them flat, so each must name its tool explicitly.

**Support another agent.** Add `scripts/agents/<agent>.sh` with detect/install/test/launch
functions and a case branch. Do not change the public commands.

## 11. Gotchas

- **The rules symlink is load-bearing.** If `SKILL.md` becomes a text file containing a
  path, pi skips the skill (no `description`) and the agent silently loses its workflow —
  it still looks healthy and all 19 tools still load. `./setup.sh` repairs it.
- **`load_agent_rules()` fails soft.** A missing rules file yields a one-line fallback, no
  error. If a deployed agent ignores the workflow, check the file shipped.
- **`deploy/Dockerfile` copies only `src/`.** The rules live under `src/harness/rules/` so
  they ship; do not move them into `integrations/`.
- **Tool count appears in three places** (rules prose, `list_case_types` description,
  adapter test constant). Adding a tool without updating them is a silent doc drift.
- **`.pyc` files were once committed** under `src/harness/`. They are removed; do not let
  them come back.

## Further reading

- Non-technical usage: [`../guides/agent_harness_quickstart.md`](../guides/agent_harness_quickstart.md)
- The other entry point: [`orchestration.md`](orchestration.md)
- Knowledge the tools expose: [`knowledge-layer.md`](knowledge-layer.md)
- Ladder and gate internals: [`validation-and-preflight.md`](validation-and-preflight.md)
