# Config, LLM Client & Mesh I/O

The small supporting layers: how the LLM backend is configured, how the CLI
reads input, and how external meshes enter the pipeline. Read this when you are
changing backend integration, environment wiring, or mesh handling.

## 1. The LLM client (`llm/llm_client.py`)

OpenAI-compatible, so the backend (Ollama, vLLM, SGLang, an OpenAI-compatible
gateway) can swap without touching agent code — only the `base_url`/`model`
config changes.

`LLMConfig` is a dataclass read from env:

| Env var | Default | Meaning |
|---|---|---|
| `LOCAL_LLM_BASE_URL` | `http://localhost:11434/v1` | OpenAI-compatible endpoint |
| `LOCAL_LLM_MODEL` | `TODO-pick-a-model` | model name to call |
| `LLM_GATEWAY_API_KEY` | *(absent)* | optional Siemens SDC LLM Gateway key; when set, sent as an `x-api-key` header |

`load_dotenv()` at import reads a repo-root `.env` if present (gitignored).
`.env.example` documents the three vars; `.env` must never be committed.

`make_client()` builds the `OpenAI` client. The gateway authenticates via an
`x-api-key` header (not the SDK's default `Authorization: Bearer`), so the key is
passed as a default header when present; Ollama/vLLM ignore it. The `api_key`
param is filled with `"not-needed"` because the OpenAI SDK requires a non-empty
string even though local backends don't check it.

The actual chat call lives in `orchestration/pipeline.py:_chat_create` (see
`orchestration.md` §3), which degrades across `temperature=0` /
`response_format=json_schema` combinations for backends that reject them.

## 2. Mesh I/O (`mesh/mesh_io.py`)

Repository rule R1: **this repo never generates or repairs meshes.** A mesh is
externally supplied as a `constant/polyMesh/` directory (or a folder containing
the raw polyMesh files directly, as in `temp/provided-mesh/`).

Three functions:

- `parse_boundary_file(path)` — regex-parse the lightweight `boundary` file into
  `list[MeshPatch(name, type, n_faces, start_face)]`. Strips comments and the
  `FoamFile{}` header; a `boundary` file is always small ASCII, so no full
  OpenFOAM parser is needed.
- `load_external_mesh(mesh_dir)` — locate + parse the boundary in either layout
  (`<dir>/boundary` or `<dir>/constant/polyMesh/boundary`); raises
  `FileNotFoundError` if absent.
- `copy_polymesh(mesh_dir, case_dir)` — byte-copy the heavy files
  (`points`, `faces`, `owner`, `neighbour`, `boundary`, plus a `sets/` dir if
  present) into `constant/polyMesh/` of the case. **Never parsed or
  reinterpreted.**

Patch names from the boundary file are authoritative: the LLM is told to reuse
them verbatim (`_known_patches_hint`), the IntentValidator rejects unknown names,
and the skills map them to roles by type.

## 3. CLI (`src/__main__.py`)

```bash
python -m src run \
  --mesh "temp/provided-mesh/3D RHVT (OpenFOAM Files)" \
  --case-dir temp/out/my_case \
  --prompt-file temp/case-files/requirements.txt
```

- `--mesh` optional; omit → box geometry (blockMesh).
- `--prompt` / `--prompt-file` / stdin (Ctrl-Z/D + Enter).
- `--max-attempts N` (default 3) and `--model NAME` (overrides
  `LOCAL_LLM_MODEL`).
- Exit code 0 iff validation passed. Prints stage banners, validation
  stage/errors, attempts used, and the case path.

The `sys.path` shim at the top (`__main__.py:30`) inserts `src/` so the module
runs from the repo root without an editable install.

## 4. Deployment env (Dockerized)

When deployed on the VM, the pipeline runs in a container with its own Linux
docker CLI and the host socket mounted (docker-outside-of-docker); the OpenFOAM
image runs as a sibling container. Paths must be aligned. See
[`../infrastructure/vm-deploy-bundle.md`](../infrastructure/vm-deploy-bundle.md).

## 5. Gotchas

- **Don't call the LLM client directly in new code** — go through
  `pipeline.extract_intent` / `extract_file_patches` (they own prompt building,
  JSON repair, retry, and graceful degradation).
- Editing `.env` while a process is running won't be picked up (`load_dotenv`
  runs once at import).
- `mesh_io` is intentionally regex-based; if you need to *understand* mesh data
  beyond the boundary file, that is out of scope per R1.
- When running tests/scripts by hand, remember `sys.path` shims: tests set
  `sys.path.insert(0, "src")` themselves (`python -m pytest` from the repo root
  uses `pyproject.toml`'s `pythonpath = ["src"]`).