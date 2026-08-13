# Engineer's Guide — Using the CFD Case-Generation Agent (No Code Needed)

You are a mechanical/aerospace engineer, not a programmer. This guide is for you.
It explains, in plain language, how to use this tool to get a working
**OpenFOAM CFD case** from a description you write in English. You do **not**
need to write code, understand dictionaries, or know how OpenFOAM stores its
setup. The agent software does the engineering setup for you — you describe the
problem, it builds the case.

> What this tool is **for**: turning a written description of a fluid-flow (or
> solid-mechanics) problem into a complete, ready-to-run OpenFOAM case folder.
>
> What it is **not**: a CAD tool, a mesh generator, or a replacement for a
> supercomputer. It prepares the simulation; a CFD engineer (or you, later)
> still interprets the results.

---

## 1. What happens when you use it

Under the hood the tool is a small pipeline of software pieces working together:

1. **An AI model** reads your English description and turns it into a compact
   summary of the physics ("turbulent, incompressible air flow", "inlet at
   5 m/s on this face", ...).
2. **A generator** then writes out the full OpenFOAM case — the `0/`,
   `constant/`, `system/` folders that define initial values, fluid
   properties, and solver settings. This part is deterministic: good, proven
   defaults are filled in automatically.
3. **A watchdog checks the case before doing the expensive work** — it audits
   the settings, checks the mesh, and does a *short* test run to predict whether
   the full solver run would blow up. If that predicts trouble, it fixes the cause
   early instead of wasting minutes on a doomed run.
4. **A checker** actually opens the case up inside a small Linux container and
   runs the real checks on it: builds the mesh (if no mesh was provided), checks
   the mesh quality, and starts the solver for the first time steps.
5. If a check finds a problem, the tool **fixes the case by itself and tries
   again** — up to three attempts — before giving up and telling you what went
   wrong.

So your job is only: **describe the problem, run one command, read the answer.**

---

## 2. Before you start (one-time setup)

This section is done once by whoever first sets up the machine. If a colleague
hands you a machine that is already set up, jump straight to **Section 3**.

You need four things installed:

| # | What | Why | How to know you have it |
|---|------|-----|-------------------------|
| 1 | **Python 3.11+** | the language the tool is written in | `python --version` prints something like `Python 3.11.x` |
| 2 | **Docker Desktop** | runs OpenFOAM v13 in an isolated container so you don't install CFD software | `docker version` prints version info (and Docker Desktop is open) |
| 3 | **The OpenFOAM v13 image** | the actual CFD engine used for checking | `docker images` lists `microfluidica/openfoam` |
| 4 | **An AI model** | the "brain" that understands your description | set in a small file called `.env` (see below) |

**One-time commands** (copy-paste each into the terminal — macOS/Linux shown):

```bash
# 1. Create a working Python environment (once) — here with plain Python's `venv`,
#    but any virtual environment works just as well (venv, conda, ...):
python -m venv .venv
source .venv/bin/activate            # re-activate it in every new terminal

# 2. Pull the OpenFOAM v13 container we check against (once)
docker pull microfluidica/openfoam:13
```

Then, from the project folder, install the Python dependencies:

```bash
pip install -r requirements.txt
```

> [!NOTE]
> Any Python 3.11+ environment works — venv, conda, or a per-project manager of
> your choice. Just keep it active when you run the commands below, and use the
> same `python` for both setup and running.

Finally, configure which AI model to talk to. Copy the example file to a file
named `.env` (exactly that name, starting with a dot):

```bash
cp .env.example .env
```

Open `.env` with any text editor and set the model. The two lines that matter:

```
LOCAL_LLM_BASE_URL=
LOCAL_LLM_MODEL=
```

- If your team runs a **local model** (e.g. via Ollama), set the base URL to
  `http://localhost:11434/v1` and the model to the model name you pulled.
- If your team provides a **cloud/test model**, paste the URL and model name
  they give you.
- Leave any line blank only if your team says to.

*Check your setup:*

```bash
python -c "import docker, openai; print('Python + deps OK')"
docker version                      # Docker is running
docker images | grep openfoam       # CFD image present
```

> [!TIP]
> If `python` doesn't work but `python3` prints the Xcode 3.9 message, you are
> using the wrong interpreter. Activate your virtual environment first (e.g.
> `source .venv/bin/activate`) and use **python**, not python3.

---

## 3. The three steps for every case you generate

### Step 1 — Write your problem as a short English description

Describe the problem the way you'd brief a colleague. Include what matters:

- **What flows** (air, water, a gas...) and roughly **how** (fast/slow, steady
  or changing with time)
- **The space** — if you have no mesh: the box dimensions (e.g.
  *"a 0.1 × 0.1 × 0.01 m channel"*). If you have a mesh: just say where it is.
- **What happens at the edges** — inlets, outlets, fixed walls, a moving wall
  (e.g. *"the top wall slides at 1 m/s"*)
- Anything unusual (pressure, temperature, surface tension)

**Examples that work well:**

> "Lid-driven cavity: a 0.1 m square box filled with air at room temperature,
> the top lid moves to the right at 1 m/s, all other walls are fixed, 2D,
> laminar."

> "Steady turbulent flow of air through a 6 cm wide square duct, 1 m long,
> inlet velocity 5 m/s, outlet at atmospheric pressure."

> "Water at 300 K flows over a flat heating wall; simulate temperature as well
> and include buoyancy."

### Step 2 — Run one command

The command has four parts:

- `--prompt "..."` — quote your description, OR `--prompt-file my_case.txt` to
  read it from a text file (recommended if it's long).
- `--case-dir temp/out/<name>` — where the generated case folder is written.
  Use a new name each time.
- `--mesh path/to/folder` — **optional**. Add this only if you already have a
  mesh. The tool reads a small file called `boundary` inside that folder to
  learn the names of your surfaces, then builds the case around your mesh.
- Omit `--mesh` to start with a simple box that the tool meshes for you.

**Simple box example (no mesh):**

```bash
export PYTHONPATH="src"
python -m src run \
    --case-dir temp/out/my_first_case \
    --prompt "Air in a 0.1 by 0.1 by 0.01 m box, lid moving right at 1 m/s, walls fixed, 2D laminar..."
```

**With your own mesh:**

```bash
export PYTHONPATH="src"
python -m src run \
    --mesh "temp/provided-mesh/My Geometry" \
    --case-dir temp/out/my_case \
    --prompt-file temp/my_problem.txt
```

Or paste your description directly at the prompt by omitting both `--prompt`
and `--prompt-file`; type your text and finish with **Ctrl+D** (macOS/Linux)
or **Ctrl+Z + Enter** (Windows).

Watch the screen. It prints what it's doing stage by stage:

```
[2/5] Querying LLM to build CaseIntent (Attempt 1/3)...
[3/5] Expanding CaseIntent into CaseSpec...
[4/5] Rendering OpenFOAM dictionary files to temp/out/my_first_case...
[4.5/5] Preflight gate on temp/out/my_first_case (audit + bounded dry run)...
      -> Preflight: PASS
[5/5] Running Docker OpenFOAM validation ladder...
      -> Validation PASSED at stage [foamRun]!
```

A full run usually takes **~10–15 seconds**. If a check flags a problem, the
tool fixes it and re-runs automatically (you'll see `[Retry 2/3] ...`).

### Step 3 — Read the answer

- **Success** ends with `ok: True` and prints:

  ```
  validation stage: foamRun
  ok: True
  case written to: temp/out/my_first_case
  ```

  Your case lives in the folder it names. Inside you'll see the three standard
  OpenFOAM folders — `0/` (initial values at the boundaries), `constant/`
  (fluid properties and the mesh), `system/` (solver and output settings).
  This case has **already passed** a real mesh-quality check and **actually
  ran** its first time steps — not just "looks about right".

- **Failure** ends with `ok: False`. The tool tried up to 3 times by itself. If
  it still failed, it prints the reason with the file it blames, e.g.
  `[DICTIONARY_SYNTAX] system/fvSolution: ...`. Don't try to fix it — **save
  the last block of output** and send it to the team that maintains the tool.
  It is exactly the information they need.

---

## 4. If something goes wrong — quick fixes

| You see | Most likely cause | Fix |
|---------|-------------------|-----|
| `docker: command not found` | Docker not installed / not open | Install Docker Desktop and open it, then re-run |
| `Cannot connect to the Docker daemon` | Docker is running? | Start Docker Desktop and wait for it to be ready |
| `docker not found on PATH` in the output | Docker unavailable | Start Docker; otherwise the tool exits early (that's an *environment* error, not a case error) |
| `TODO-pick-a-model` or empty-model errors | `.env` not configured | Open `.env`, fill in `LOCAL_LLM_MODEL`; ask the team for the value if unsure |
| `python` doesn't run / Python 3.9 message | Xcode's system Python instead of your environment | Activate your virtual environment (e.g. `source .venv/bin/activate`) and try again |
| `no boundary file found` | `--mesh` points at the wrong folder | Point `--mesh` at the folder that contains a file named `boundary` |
| `ValueError: ... if it is not possible` / long traceback | Inputs don't combine | Re-read your description, simplify it, and retry |
| It says `ok: False` after retries | A genuine setup problem | Copy the last error block and send it to the team |

---

## 5. Word of caution

This software is a **research prototype** built by an internship project. It is
not certified for engineering decisions. Treat it as a quick way to get a
well-formed, *checkable* OpenFOAM case started — not as validation that your
physics are right. A human CFD engineer (you!) still owns the final setup,
mesh quality, and interpretation of results.

---

## Where to find more

- A technical quick-start for people comfortable with code:
  [`agent-usage-guide.md`](agent-usage-guide.md).
- Plain-language overview of how OpenFOAM cases are structured (for curious
  engineers): [`../domain/openfoam-onboarding.md`](../domain/openfoam-onboarding.md).
- What problem this whole project solves and the plan behind it:
  [`../README.md`](../../README.md).