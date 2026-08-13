#!/usr/bin/env bash
# offline-load.sh — run this ON THE AIR-GAPPED Ubuntu VM to load a transfer
# bundle produced by offline-save.sh.
#
# Usage:
#   ./deploy/offline-load.sh [BUNDLE_DIR] [SESC_WORKDIR]
# Defaults: BUNDLE_DIR=./bundle  SESC_WORKDIR=/srv/sesc
set -euo pipefail

BUNDLE_DIR="${1:-$(pwd)/bundle}"
SESC_WORKDIR="${2:-/srv/sesc}"

for c in docker; do
  if ! command -v "$c" >/dev/null 2>&1; then
    echo "ERROR: '$c' not found — load docker images on this host first." >&2
    exit 1
  fi
done

echo "==> Loading docker images"
for tarball in "$BUNDLE_DIR"/images/*.tar; do
  echo "    - $tarball"
  docker load -i "$tarball"
done

echo "==> Restoring Ollama model blobs into $SESC_WORKDIR/ollama-models"
mkdir -p "$SESC_WORKDIR/ollama-models"
if [ -d "$BUNDLE_DIR/models/models" ]; then
  cp -R "$BUNDLE_DIR/models/models/." "$SESC_WORKDIR/ollama-models/"
else
  echo "WARN: no model blobs found under $BUNDLE_DIR/models — the pipeline"
  echo "      will fail at LLM calls until a model is present." >&2
fi

echo "==> Verifying the Ollama server image is loadable"
docker image inspect ollama/ollama:latest >/dev/null \
  && echo "    ollama/ollama:latest OK" \
  || { echo "ERROR: ollama/ollama:latest missing"; exit 1; }

echo ""
echo "Done. Bring up the stack:"
echo "  docker compose -f deploy/docker-compose.yml up -d"
echo "Verify the model is visible inside the container:"
echo "  docker compose -f deploy/docker-compose.yml exec ollama ollama list"
