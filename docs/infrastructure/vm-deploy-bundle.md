# VM Deployment Bundle (`deploy/`)

Turns the spike's local Docker setup into a one-command bring-up on the
concept note's isolated Ubuntu VM. The pipeline is already Docker-shaped (it
validates cases by running OpenFOAM in a container), so deploying to the VM is
mostly **re-using the same containers** on a Linux host rather than installing
OpenFOAM natively. This bundle is the concrete answer to the open
VM/hardware question in `docs/supervision/first-meeting-prep.md` — the
OpenFOAM side is solved; the remaining decision is the LLM serving tier (GPU
or CPU), which changes only the model size/backend, not this bundle.

## What's in the bundle

```
deploy/
  Dockerfile          pipeline image (python + deps + docker CLI)
  docker-compose.yml  ollama + pipeline services, path-aligned mounts
  offline-save.sh     build a transfer bundle on a CONNECTED machine
  offline-load.sh     load that bundle on the air-gapped VM
```

Two services:

- **`ollama`** — the local LLM. No external API calls; the pipeline talks to
  it over the compose network (`http://ollama:11434/v1`), not localhost.
- **`pipeline`** — `python -m src`, the LangGraph pipeline. It validates
  cases by running OpenFOAM in a **sibling container on the host daemon**
  (`docker-outside-of-docker`), because `src/validation/validation_agent.py`
  shells out to `docker run` rather than using a client SDK.

## The one non-obvious constraint: path alignment

`validation_agent.py` runs

```
docker run -v {case_dir}:/case ...
```

where `{case_dir}` is the path **as seen inside the pipeline container**. The
host docker daemon resolves that path on the **host** filesystem. So the
workspace bind mount must use the **same absolute path on both sides**.

The compose file defaults this to `/srv/sesc` (host and container). If you
change it, set `SESC_WORKDIR` in the `.env` used by `docker compose` — and
create that directory on the VM — but never change only one side.

```
host:  /srv/sesc/out/rhvt   (real files)
pipeline container:  /srv/sesc/out/rhvt   (same bind mount)
    └─ docker run -v /srv/sesc/out/rhvt:/case ...   ← daemon resolves it on host
```

## Normal deployment (VM has internet, at least during setup)

```bash
git clone <repo> /srv/sesc/workspace   # or copy the repo tree
sudo mkdir -p /srv/sesc                # host+container path-aligned
cd /srv/sesc/workspace
docker compose -f deploy/docker-compose.yml up -d --build
```

Then verify the LLM responds (the Week 2 KPI, now inside a container):

```bash
docker compose -f deploy/docker-compose.yml exec ollama ollama pull llama3.1:8b
docker compose -f deploy/docker-compose.yml exec ollama \
    ollama run llama3.1:8b "Reply with exactly: OK"
```

And run the pipeline (mesh lives under the same `/srv/sesc` tree):

```bash
docker compose -f deploy/docker-compose.yml run --rm pipeline \
    run --mesh /srv/sesc/mesh \
        --case-dir /srv/sesc/out/rhvt \
        --prompt "Describe the flow here..."
```

Environment overrides (all optional, via `.env` next to the compose file):

| Variable             | Default                      | Meaning                                   |
|----------------------|------------------------------|-------------------------------------------|
| `LOCAL_LLM_MODEL`    | `llama3.1:8b`                | Ollama model name                         |
| `FOAM_DOCKER_IMAGE`  | `microfluidica/openfoam:13`  | OpenFOAM validation image                 |
| `FOAM_VALIDATION_TIMEOUT` | `600`                   | seconds per ladder stage                  |
| `FOAM_VALIDATION_PROCS` | `1`                       | `mpirun -parallel` worker count           |
| `SESC_WORKDIR`       | `/srv/sesc`                  | path-aligned workspace (host **and** container) |

## Air-gapped VM (per concept note's "isolated")

On a **connected** machine, build the bundle:

```bash
./deploy/offline-save.sh llama3.1:8b          # pulls images + model, writes ./bundle/
scp -r bundle user@vm:~/sesc-bundle
```

On the **VM**:

```bash
sudo mkdir -p /srv/sesc
./deploy/offline-load.sh ~/sesc-bundle        # docker load + restore model blobs
docker compose -f deploy/docker-compose.yml up -d
```

Notes:

- The bundle `docker save`s the images (not `docker export`) and copies the
  **Ollama model blobs** from `~/.ollama/models`, so the VM never needs
  `ollama pull`. No internet required after load.
- The pipeline image is built **on the connected machine** (`deploy/Dockerfile`)
  and shipped as a tarball — the VM doesn't need to build it or reach PyPI.
- If the VM has a GPU, add `gpus: all` to the `ollama` service
  (compose supports it with the NVIDIA container toolkit); Ollama auto-detects
  CUDA. CPU-only works — just pick a smaller model (see
  `docs/infrastructure/local-llm-backend-comparison.md`).

## What this bundle deliberately does NOT do

- **Native OpenFOAM install on the VM.** The pipeline runs OpenFOAM in
  containers (`microfluidica/openfoam:13`); a native
  `apt install openfoam13` (`docs/infrastructure/vm-ollama-setup.md`) is only
  needed if you want the OpenFOAM CLI *on the host* for manual case inspection.
- **Decide GPU vs CPU.** That's still an open question for Dr. Shaaban — it
  determines the practical model size, not the deployment mechanics.
- **Provision the VM.** This bundle assumes docker + compose are already on
  the host (or that you can install them before isolation is enforced).
