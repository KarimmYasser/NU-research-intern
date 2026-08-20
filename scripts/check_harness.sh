#!/usr/bin/env bash
# ==============================================================================
# OpenFOAM AI assistant — plain-language health check.
# Public entry point: ./check.sh
# ==============================================================================
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/agents/common.sh
source "$SCRIPT_DIR/agents/common.sh"
LOG_FILE="$LOG_DIR/check.log"

VERBOSE=0
AGENT_TARGET="pi"
while [[ $# -gt 0 ]]; do
    case "$1" in
        --verbose) VERBOSE=1; shift ;;
        --agent) AGENT_TARGET="${2:-pi}"; shift 2 ;;
        --agent=*) AGENT_TARGET="${1#*=}"; shift ;;
        -h|--help) echo "Usage: ./check.sh [--verbose]"; exit 0 ;;
        *) echo "Unknown option: $1" >&2; exit 2 ;;
    esac
done

cd "$REPO_ROOT"
log_init "check --agent $AGENT_TARGET"

say ""
say "Checking your OpenFOAM assistant..."
say ""

PROBLEMS=0
CAN_CREATE=1

# shellcheck source=scripts/agents/pi.sh
source "$SCRIPT_DIR/agents/pi.sh"

if pi_present; then
    ok "Pi is installed"
else
    printf '%b\n' "${C_RED}✗${C_OFF} Pi is not installed."
    say "  Install it with: npm install -g @earendil-works/pi-coding-agent"
    PROBLEMS=1; CAN_CREATE=0
fi

if pi_adapter_installed; then
    ok "OpenFOAM tools are connected"
else
    printf '%b\n' "${C_RED}✗${C_OFF} OpenFOAM tools are not connected to Pi."
    say "  Run: ./setup.sh"
    PROBLEMS=1; CAN_CREATE=0
fi

if [[ -x "$VENV_PYTHON" ]] && PYTHONPATH="$REPO_ROOT/src" run_logged "$VENV_PYTHON" \
        -m harness.mcp_server --self-test --workspace-dir "$WORKSPACE_DIR"; then
    ok "Python components are working"
else
    printf '%b\n' "${C_RED}✗${C_OFF} The OpenFOAM components are not working."
    say "  Run: ./setup.sh"
    PROBLEMS=1; CAN_CREATE=0
fi

DOCKER="$(docker_state)"
if [[ "$DOCKER" == "running" ]]; then
    ok "Docker is running"
elif [[ "$DOCKER" == "permission_denied" ]]; then
    warn "Docker is running, but permission was denied for the socket."
    say "  Fix with: sudo usermod -aG docker \$USER && newgrp docker"
    PROBLEMS=$((PROBLEMS + 1))
elif [[ "$DOCKER" == "stopped" ]]; then
    warn "Docker is not running"
else
    warn "Docker is not installed"
fi

say ""
if [[ "$CAN_CREATE" == "1" && "$DOCKER" == "running" ]]; then
    say "Everything is ready. Start with: ./start.sh"
elif [[ "$CAN_CREATE" == "1" ]]; then
    say "The assistant can create cases now."
    if [[ "$DOCKER" == "permission_denied" ]]; then
        say "Add your user to the docker group before running simulations."
    else
        say "Start Docker before asking it to run a simulation."
    fi
else
    say "Run ./setup.sh to fix the problems above."
fi
say ""
say "${C_DIM}Full details: ${LOG_FILE#"$REPO_ROOT"/}${C_OFF}"

exit "$PROBLEMS"
