# Pipeline image for the VM deployment (docker-compose service `pipeline`).
#
# Design notes:
# - The validation ladder (src/validation/validation_agent.py) shells out to
#   `docker run` directly, so this image MUST contain a working `docker` CLI
#   and the container MUST mount the host docker socket + a *path-aligned*
#   workspace. See docs/infrastructure/vm-deploy-bundle.md.
# - The OpenFOAM image itself (microfluidica/openfoam:13) is NOT baked in;
#   it runs as a sibling container on the host daemon. `docker pull` it during
#   provisioning (docker-compose + offline-load.sh handle this).
#
# Build context is the REPO ROOT (docker-compose sets build.context=..),
# not this directory.

FROM python:3.11-slim

WORKDIR /app

# Docker CLI (static binary) for the validation ladder's `docker run` calls.
# Static tarball avoids apt repo setup and works offline once downloaded.
ARG DOCKER_CLI_VERSION=29.7.2
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/* \
    && curl -fsSL "https://download.docker.com/linux/static/stable/x86_64/docker-${DOCKER_CLI_VERSION}.tgz" -o /tmp/docker.tgz \
    && tar -xzf /tmp/docker.tgz -C /usr/local/bin --strip-components=1 docker/docker \
    && rm /tmp/docker.tgz

COPY requirements.txt pyproject.toml ./
COPY src ./src

RUN pip install --no-cache-dir -r requirements.txt

# python -m src resolves the package via PYTHONPATH (mirrors tests/ shim).
ENV PYTHONPATH=/app/src

ENTRYPOINT ["python", "-m", "src"]
