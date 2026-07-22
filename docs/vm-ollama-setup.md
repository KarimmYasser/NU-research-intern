# Ubuntu VM + Local LLM Setup (Phase I)

Step-by-step for the concept note's Phase I infrastructure tasks: provision the isolated Ubuntu VM, install OpenFOAM v13, deploy a local LLM, and confirm the API responds — the literal Week 2 KPI. Commands verified against the official OpenFOAM Foundation download page and Ollama's docs (2026); cited inline.

## 1. VM baseline

- **OS:** Ubuntu 22.04 LTS, 24.04 LTS, or a recent interim release (25.04/25.10) — OpenFOAM v13 is officially packaged for these. For a long-lived research VM, prefer an LTS (22.04 or 24.04). ([openfoam.org/download/13-ubuntu](https://openfoam.org/download/13-ubuntu/))
- **CPU-only is fine to start.** Per `docs/local-llm-backend-comparison.md`, the recommendation is to start with Ollama specifically because it doesn't require a GPU — a 7B GGUF model at Q4 quantization runs at roughly 10–30 tok/s on a modern CPU. Minimum practical spec: 4+ cores, 16GB RAM (8GB is tight once OpenFOAM meshing and a 7B model are both resident).
- **If a GPU is available:** Ollama's install script auto-detects NVIDIA (CUDA) or AMD (ROCm) GPUs and configures itself accordingly — no separate GPU-specific install path needed for Ollama. A GPU changes the calculus on when to move to vLLM/SGLang (see the backend comparison doc) but isn't required for the Week 2 milestone.
- **Isolation:** per the concept note this VM should be isolated (no external API calls) — confirm with Dr. Shaaban what "isolated" means concretely (no internet at all vs. no calls out to LLM provider APIs specifically), since that affects whether you can `apt update`/`curl` install scripts directly on it or need to stage packages another way.

## 2. Install OpenFOAM v13

Official apt-based install for Ubuntu ([openfoam.org/download/13-ubuntu](https://openfoam.org/download/13-ubuntu/)):

```bash
# 1. Add the repository key
sudo sh -c "wget -O - https://dl.openfoam.org/gpg.key > /etc/apt/trusted.gpg.d/openfoam.asc"

# 2. Remove any previous OpenFOAM repo entries (clean slate)
sudo rm -f /etc/apt/sources.list.d/*dl_openfoam_org*list

# 3. Add the repository
sudo add-apt-repository "http://dl.openfoam.org/ubuntu main dev"

# 4. Update and install
sudo apt update
sudo apt -y install openfoam13
```

**Environment sourcing (v13-specific path — this is the gotcha):**

```bash
echo ". /opt/openfoam13/etc/bashrc" >> ~/.bashrc
```

Open a **new terminal** after this (sourcing in the current shell won't pick up a `.bashrc` append reliably). Verify with:

```bash
foamRun -help
```

A usage message confirms the install and environment sourcing both worked. `foamRun` is the correct verification command for v13 specifically — it's the generic runtime-selectable solver runner that replaced calling solver binaries like `icoFoam` directly (matches what `docs/openfoam/case-structure.md` already documents about `controlDict`'s `solver` field).

## 3. Verify OpenFOAM with the cavity tutorial

Real case files are already in this repo at `temp/openfoam-repo/tutorials/incompressibleFluid/cavity/` (sparse clone of OpenFOAM-13) — copy that directory to the VM, or re-clone there, then:

```bash
cd cavity
blockMesh      # writes constant/polyMesh
checkMesh      # validates mesh quality
foamRun        # runs the solver named in system/controlDict ("incompressibleFluid")
```

If all three complete without fatal errors, OpenFOAM is correctly installed and configured. This is your local "known good" reference case per `docs/openfoam-onboarding.md`.

## 4. Install Ollama

Official installer ([ollama.com/install.sh](https://ollama.com/install.sh), referenced across current 2026 Ubuntu install guides):

```bash
curl -fsSL https://ollama.com/install.sh | sh
```

This detects CPU architecture (x86_64/ARM64) and GPU (CUDA/ROCm) automatically, installs the `ollama` binary, and sets up a systemd service (`ollama.service`) that starts automatically. Takes 30–60 seconds. Re-running the same command later updates Ollama in place without deleting pulled models.

## 5. Pull a model and verify the API — this is the Week 2 KPI

```bash
ollama pull llama3.1:8b   # or whichever model you and Dr. Shaaban settle on
```

Ollama serves on **`http://localhost:11434`** by default. Two ways to confirm "responsive local LLM API," from weakest to strongest evidence:

```bash
# 1. Service is up at all
curl http://localhost:11434/

# 2. Model actually responds (this is the real KPI evidence — a completion, not just a 200)
curl http://localhost:11434/v1/chat/completions \
    -H "Content-Type: application/json" \
    -d '{
        "model": "llama3.1:8b",
        "messages": [{"role": "user", "content": "Reply with exactly: OK"}]
    }'
```

Ollama exposes an OpenAI-compatible endpoint at `/v1/chat/completions`, which matters directly for Phase II — most agent frameworks (LangChain, AutoGen, CrewAI) have first-class OpenAI-client support, so pointing them at `http://localhost:11434/v1` with any placeholder API key is usually the path of least resistance for wiring an agent framework to a local model.

## 6. Handoff to Phase II

Once step 5's curl returns a real completion, Phase I's local-LLM infrastructure goal is done. Phase II (agent framework wiring — LangChain/AutoGen/CrewAI per the concept note, agent topology design) is out of scope for this doc; start there once this is verified.

## Troubleshooting

- **Agent framework can't reach Ollama, but curl on the VM itself works:** Ollama binds to `localhost` by default. If your agent code runs in a container or a different process context that isn't truly `localhost` to the Ollama service, set `OLLAMA_HOST=0.0.0.0` (in the systemd service environment or via `launchctl`/env var before starting `ollama serve`) and restart the service — otherwise external processes get connection-refused even though `curl localhost:11434` works fine from the VM's own shell.
- **Model runs but is extremely slow / VM becomes unresponsive:** Likely insufficient RAM for the model size — Ollama will still attempt to load it and swap heavily. Check `free -h` before and during a pull; if swap usage spikes hard, drop to a smaller model or a more aggressive quantization (e.g. `:8b-instruct-q4_0` variants) before assuming something's broken.
- **`foamRun -help` not found after install:** almost always the `.bashrc` sourcing step — confirm the line `. /opt/openfoam13/etc/bashrc` is actually in `~/.bashrc` (not `~/.bash_profile` or a different shell's rc file) and that you opened a genuinely new terminal/session rather than re-sourcing in a shell that already ran before the edit.
- **`blockMesh` succeeds but `foamRun` fails to find the solver:** confirm `system/controlDict`'s `solver` field matches an actual installed solver module name — a copy-pasted case from an older-version tutorial may still have an `application` field instead of `solver`, which v13's `foamRun` won't recognize (see the version-mismatch note in `docs/openfoam-onboarding.md`).
- **apt install fails / repo unreachable:** if the VM is genuinely network-isolated per the concept note's "isolated" requirement, these apt/curl steps need to happen either before isolation is enforced, or via a staged/offline package mirror — worth clarifying with Dr. Shaaban exactly what "isolated" is meant to block before you hit this wall mid-setup.

## Sources

- [OpenFOAM v13 download/install — Ubuntu](https://openfoam.org/download/13-ubuntu/)
- [Ollama official install script](https://ollama.com/install.sh)
- [Ollama OpenAI compatibility docs](https://ollama.com/blog/openai-compatibility)
