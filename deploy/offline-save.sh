#!/usr/bin/env bash
# offline-save.sh — run this on an INTERNET-CONNECTED machine to build a
# transfer bundle for an air-gapped Ubuntu VM.
#
# Produces ./bundle/ with:
#   images/   - docker-saved tarballs (ollama server, OpenFOAM, pipeline)
#   models/   - the pre-pulled Ollama model blobs
#
# Usage:
#   ./deploy/offline-save.sh [MODEL] [FOAM_IMAGE] [SESC_WORKDIR]
# Defaults: MODEL=llama3.1:8b  FOAM_IMAGE=microfluidica/openfoam:13  SESC_WORKDIR=/srv/sesc
set -euo pipefail

MODEL="${1:-llama3.1:8b}"
FOAM_IMAGE="${2:-microfluidica/openfoam:13}"
SESC_WORKDIR="${3:-/srv/sesc}"

BUNDLE="$(pwd)/bundle"
IMAGES_DIR="$BUNDLE/images"
MODELS_DIR="$BUNDLE/models"

for c in docker ollama; do
  if ! command -v "$c" >/dev/null 2>&1; then
    echo "ERROR: '$c' not found — offline-save needs docker + ollama." >&2
    exit 1
  fi
done

echo "==> SESC_WORKDIR is '$SESC_WORKDIR' — the pipeline container bind-mounts"
echo "    this path; it MUST exist and be identical on the VM."
mkdir -p "$IMAGES_DIR" "$MODELS_DIR"

echo "==> Pulling OpenFOAM validation image: $FOAM_IMAGE"
docker pull "$FOAM_IMAGE"

echo "==> Pulling Ollama server image"
docker pull ollama/ollama:latest

echo "==> Building pipeline image (deploy/Dockerfile)"
docker build -t sesc-pipeline:local -f deploy/Dockerfile .

echo "==> Saving images (tar 'docker save' to avoid 'split to stdout' limits)"
docker save -o "$IMAGES_DIR/openfoam.tar" "$FOAM_IMAGE"
docker save -o "$IMAGES_DIR/ollama-server.tar" ollama/ollama:latest
docker save -o "$IMAGES_DIR/pipeline.tar" sesc-pipeline:local

echo "==> Pulling Ollama model: $MODEL"
ollama pull "$MODEL"

echo "==> Copying Ollama model blobs (from ~/.ollama/models)"
# Version-robust offline transfer: the whole models dir is copied, so the VM
# needs no `ollama pull` and no internet.
cp -R ~/.ollama/models "$MODELS_DIR/"

echo "==> Writing bundle manifest"
cat > "$BUNDLE/README.txt" <<EOF
Transfer bundle created $(date -u +%Y-%m-%dT%H:%M:%SZ)
  MODEL:       $MODEL
  FOAM_IMAGE:  $FOAM_IMAGE
  SESC_WORKDIR: $SESC_WORKDIR (must be created, identical, on the VM)

On the VM:
  1. mkdir -p $SESC_WORKDIR
  2. ./deploy/offline-load.sh   (docker load images + restore models)
  3. docker compose -f deploy/docker-compose.yml up -d
EOF

echo ""
echo "Done. Copy ./bundle/ to the VM, then run offline-load.sh there."
echo "Remember: create '$SESC_WORKDIR' on the VM and mount/copy this bundle"
echo "somewhere the script can read (e.g. ~/sesc-bundle)."
