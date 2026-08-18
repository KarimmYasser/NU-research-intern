#!/usr/bin/env bash
# ==============================================================================
# OpenFOAM AI assistant — daily launcher.
# Public entry point: ./start.sh
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/agents/common.sh
source "$SCRIPT_DIR/agents/common.sh"
HARNESS_LOG_FILE="$LOG_DIR/start.log"
LOG_FILE="$HARNESS_LOG_FILE"

AGENT_TARGET="pi"
PASSTHROUGH=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --agent)
            [[ $# -ge 2 ]] || { echo "--agent requires a value" >&2; exit 2; }
            AGENT_TARGET="$2"; shift 2 ;;
        --agent=*) AGENT_TARGET="${1#*=}"; shift ;;
        -h|--help)
            cat <<'USAGE'
Usage: ./start.sh [--agent pi|claude] [extra arguments passed to the assistant]

Starts the OpenFOAM AI assistant. With no arguments it starts Pi.
USAGE
            exit 0 ;;
        *) PASSTHROUGH+=("$1"); shift ;;
    esac
done

cd "$REPO_ROOT"
log "start --agent $AGENT_TARGET"

# --- 1. Confirm setup was completed -----------------------------------------
if [[ ! -x "$VENV_PYTHON" ]]; then
    die "Your OpenFOAM assistant is not set up yet." "Run this first:

    ./setup.sh"
fi

mkdir -p "$WORKSPACE_DIR"

case "$AGENT_TARGET" in
    pi)
        # shellcheck source=scripts/agents/pi.sh
        source "$SCRIPT_DIR/agents/pi.sh"
        pi_require
        if ! pi_adapter_installed; then
            die "The OpenFOAM tools are not connected to Pi yet." "Run this first:

    ./setup.sh"
        fi
        # --- 2. Fast readiness check ---------------------------------------
        if ! PYTHONPATH="$REPO_ROOT/src" run_logged "$VENV_PYTHON" -m harness.mcp_server \
                --self-test --workspace-dir "$WORKSPACE_DIR"; then
            die "The OpenFOAM components are not responding." "Run ./setup.sh again to repair the installation."
        fi

        # --- 4. Welcome ----------------------------------------------------
        say ""
        say "Starting your OpenFOAM assistant..."
        say ""
        say "Your simulation files will be saved in:"
        say "    ${WORKSPACE_DIR#"$REPO_ROOT"/}/"
        say ""
        if [[ "$(docker_state)" != "running" ]]; then
            say "Docker is not running, so simulations cannot be validated yet."
            say "You can still create and inspect cases."
            say ""
        fi
        say "Try asking:"
        say ""
        say "  \"Create a 2D lid-driven cavity simulation with a top-wall"
        say "   velocity of 1 m/s, then check that the case is ready to run.\""
        say ""

        # --- 3. Launch Pi (project-local settings + OpenFOAM skill) --------
        export OPENFOAM_WORKSPACE_DIR="$WORKSPACE_DIR"
        export OPENFOAM_HARNESS_REPO="$REPO_ROOT"
        if [[ ${#PASSTHROUGH[@]} -gt 0 ]]; then
            pi_launch "${PASSTHROUGH[@]}"
        else
            pi_launch
        fi
        ;;
    claude)
        have claude || die "Claude Code is not installed." "Use the supported assistant instead:

    ./start.sh"
        exec claude "${PASSTHROUGH[@]}"
        ;;
    *) die "Unsupported assistant: $AGENT_TARGET" "Supported values are: pi (default), claude." ;;
esac
