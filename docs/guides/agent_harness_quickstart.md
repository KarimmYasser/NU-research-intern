# OpenFOAM AI Assistant — Quickstart

For CFD engineers, students, and researchers. No programming required.

---

## 1. What this does

You describe a simulation in plain English. The assistant builds the OpenFOAM case
for you — the folders, the boundary conditions, the numerical settings — checks it
for mistakes, and tells you whether it is ready to run.

It can:

- Build a complete, valid OpenFOAM v13 case from a description.
- Look up the right solver and proven settings from the official v13 tutorials.
- Check the case for errors instantly, before anything is run.
- Run the case in a container and explain any errors in plain language.
- Read results back and summarise them.

You do **not** need OpenFOAM installed on your computer. Everything runs in Docker.

> **Which route is this?** This one uses a large commercial AI assistant, so it needs
> an account and an internet connection. It is how this tool is built and tested, and
> it is well suited to exploring an unfamiliar setup interactively — you can ask
> questions and change your mind mid-way.
>
> For routine or repeated case generation, and for work that must stay on your own
> machine with no internet, use the automatic pipeline instead:
> [`engineer-user-guide.md`](engineer-user-guide.md). There the software constrains
> what the AI is allowed to decide; here the assistant decides everything itself, so
> **review what it produces.**

---

## 2. First-time setup

### Prerequisites (if starting on a fresh Ubuntu / Debian VM)

Ensure you have Python 3.11+ (with `python3-venv`) and Node.js 22+ installed:

```bash
# 1. System packages (Python 3.11+, venv, git, curl)
sudo apt update && sudo apt install -y python3 python3-venv git curl

# 2. Node.js 22 LTS (required for modern ESM and Pi)
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
sudo apt install -y nodejs

# 3. Pi coding agent
npm install -g @earendil-works/pi-coding-agent
```

### Setup the assistant

Open a terminal in the project folder and run:

```bash
./setup.sh
```

You should see:

```
Setting up your OpenFOAM AI assistant...

✓ Python is ready
✓ OpenFOAM tools are installed
✓ TypeScript runtime is ready
✓ Pi is connected to the harness
✓ Safety checks passed
✓ Your simulation workspace is ready

Setup complete.
```

Run it as often as you like — it never overwrites your simulations and never
changes settings that are already correct.

**If it says Pi is not installed**, install it and run `./setup.sh` again:

```bash
npm install -g @earendil-works/pi-coding-agent
```

**If it says the assistant still needs an AI model**, run `pi` once, follow the
sign-in instructions, then continue with `./start.sh`.

---

## 3. Starting the assistant

```bash
./start.sh
```

Then type your request. That is the whole workflow — `./setup.sh` once,
`./start.sh` every time after that.

---

## 4. What to ask

Talk to it the way you would brief a colleague. Give the geometry, the fluid, the
speeds, and what you want to know.

**A first simulation**

> Create a 2D lid-driven cavity simulation using water. Use a 0.1 m square domain
> and a top-wall speed of 1 m/s. Check the case and tell me whether it is ready to run.

**Turbulent flow through a channel**

> Create an incompressible turbulent channel flow with air at an inlet velocity of
> 10 m/s. Make sure the mesh quality is acceptable before you tell me it is done.

**Checking work you already have**

> Look at the case in workspace/my_case. Run the checks and tell me why it fails.

**Understanding results**

> The cavity case finished. Show me how the residuals behaved and tell me whether
> the solution converged.

If you leave something out — the fluid, the size, the speed — the assistant will
ask you instead of guessing.

---

## 5. Where files are saved

Everything the assistant creates goes in one place:

```
workspace/
```

Each simulation gets its own folder inside it, for example `workspace/cavity_re1000/`.
These are ordinary OpenFOAM text files: you can open them, edit them, and use them
with ParaView or any other OpenFOAM tooling. The assistant cannot write outside this
folder, so the rest of your computer is never touched.

---

## 6. If something goes wrong

Run the health check:

```bash
./check.sh
```

It reports what works and what does not, in plain language:

```
Checking your OpenFOAM assistant...

✓ Pi is installed
✓ OpenFOAM tools are connected
✓ Python components are working
! Docker is not running

The assistant can create cases now.
Start Docker before asking it to run a simulation.
```

Common situations:

| What you see | What to do |
| :--- | :--- |
| "Pi is not installed" | `npm install -g @earendil-works/pi-coding-agent`, then `./setup.sh` |
| "not connected" / "not set up yet" | Run `./setup.sh` |
| "Docker is not running" | Start Docker Desktop. Cases can still be created without it. |
| "Pi still needs an AI model" | Run `pi`, sign in, then `./start.sh` |
| Anything else | Run `./setup.sh --verbose`, or send `.harness/logs/setup.log` to the maintainer |

**Do I need OpenFOAM installed?** No. Meshing and solver runs happen inside the
official Docker image (`microfluidica/openfoam:13`).

**It suggested settings that look like an older OpenFOAM.** Tell it so, and ask it
to check. This project targets OpenFOAM **v13** (openfoam.org), but most OpenFOAM
material in the world — including what AI models learned from — is from version 9
or from the separate openfoam.com branch. The assistant is instructed about the
differences and has a reference page for them, but it is the most likely place for
it to slip. Two signs something is wrong:

- it writes `application icoFoam;` (or another solver name) in `system/controlDict`
  — v13 uses a `solver` line instead;
- it mentions `transportProperties` or `turbulenceProperties` — v13 calls these
  `physicalProperties` and `momentumTransport`.

If you see either, ask it to re-check against v13 conventions. Cases it builds with
its own tools are correct by construction; the risk is only when it hand-writes a
file.

**Can I edit the generated files myself?** Yes. They are standard OpenFOAM v13
dictionaries. Edit them and ask the assistant to re-check the case.

---

## 7. Advanced reference

<details>
<summary>Choosing a different assistant</summary>

Pi is the supported default. Other targets are experimental:

```bash
./setup.sh --agent pi        # default
./setup.sh --agent cline
./setup.sh --agent claude
./start.sh  --agent pi
```

Arguments you pass to `./start.sh` are forwarded to the underlying assistant.

</details>

<details>
<summary>How the integration works</summary>

`integrations/pi/` is a project-local pi package containing:

- `extensions/openfoam-tools.ts` — starts the Python harness as a child process,
  speaks JSON-RPC over stdio, and registers all 19 tools with pi. The Python code
  remains the single source of truth; no tool logic is duplicated in TypeScript.
- `skills/openfoam/SKILL.md` — a symlink to `src/harness/rules/openfoam.md`, the one
  copy of the behavioural rules (when to use which tool, how to confirm before
  destructive actions, how to report results). `./setup.sh` replaces the symlink with
  a real copy on systems that cannot create symlinks.

Developers extending this should read
[`../development/agent-harness-and-pi.md`](../development/agent-harness-and-pi.md).

`./setup.sh` installs it with `pi install ./integrations/pi -l`, which writes only
`.pi/settings.json`. Global pi configuration and credentials are never modified.
The harness is always given an explicit workspace:
`python -m harness.mcp_server --workspace-dir <repo>/workspace`.

Internal scripts: `scripts/setup_harness.sh`, `scripts/run_agent.sh`,
`scripts/check_harness.sh`, `scripts/agents/{common,pi}.sh`.
Logs: `.harness/logs/`.

</details>

<details>
<summary>Diagnostics and tests</summary>

```bash
./scripts/test_harness.sh              # full smoke diagnostics
node tests/pi_adapter/adapter_test.mjs # pi adapter against the real Python harness
bash tests/shell/installer_test.sh     # installer/launcher regression tests
```

</details>

<details>
<summary>All 19 tools</summary>

**Case generation & mesh:** `scaffold_case`, `import_external_mesh`, `inspect_mesh`,
`list_case_types`

**Numerics & controls:** `calculate_cfl_timestep`, `run_openfoam_command`,
`get_tutorial_numerics`

**Preflight audits:** `run_preflight_audit` (Level A static),
`get_mesh_quality_metrics` (Level B checkMesh), `run_bounded_dry_run` (Level C
divergence prediction)

**Live validation & diagnosis:** `validate_case_docker`, `diagnose_case_failure`,
`get_case_status`

**Files & results:** `read_case_file`, `write_case_file`, `clean_case_directory`,
`read_postprocessing_data`

**Knowledge & physical bounds:** `search_openfoam_knowledge`, `check_physical_envelope`

</details>
