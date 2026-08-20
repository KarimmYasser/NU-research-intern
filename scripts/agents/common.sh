#!/usr/bin/env bash
# ==============================================================================
# Shared helpers for the OpenFOAM assistant scripts.
# Sourced by setup_harness.sh, run_agent.sh, check_harness.sh and agent adapters.
# ==============================================================================

# Resolve the repository root from this file's location (works from anywhere).
COMMON_SH_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$COMMON_SH_DIR/../.." && pwd)"
VENV_DIR="$REPO_ROOT/.venv"
VENV_PYTHON="$VENV_DIR/bin/python3"
WORKSPACE_DIR="${OPENFOAM_WORKSPACE_DIR:-$REPO_ROOT/workspace}"
LOG_DIR="$REPO_ROOT/.harness/logs"
LOG_FILE="${HARNESS_LOG_FILE:-$LOG_DIR/setup.log}"

VERBOSE="${VERBOSE:-0}"

if [[ -t 1 ]]; then
    C_GREEN='\033[0;32m'; C_BLUE='\033[0;34m'; C_YELLOW='\033[1;33m'
    C_RED='\033[0;31m'; C_DIM='\033[2m'; C_OFF='\033[0m'
else
    C_GREEN=''; C_BLUE=''; C_YELLOW=''; C_RED=''; C_DIM=''; C_OFF=''
fi

log_init() {
    mkdir -p "$LOG_DIR"
    : >"$LOG_FILE"
    printf '[%s] %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$*" >>"$LOG_FILE"
}

log() {
    mkdir -p "$LOG_DIR" 2>/dev/null || true
    printf '[%s] %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$*" >>"$LOG_FILE" 2>/dev/null || true
}

say()  { printf '%b\n' "$*"; }
ok()   { printf '%b\n' "${C_GREEN}✓${C_OFF} $*"; log "OK: $*"; }
warn() { printf '%b\n' "${C_YELLOW}!${C_OFF} $*"; log "WARN: $*"; }
info() { printf '%b\n' "${C_BLUE}·${C_OFF} $*"; log "INFO: $*"; }

# Print a user-facing failure, point at the log, and exit.
die() {
    printf '\n%b\n' "${C_RED}$1${C_OFF}"
    if [[ -n "${2:-}" ]]; then printf '\n%b\n' "$2"; fi
    printf '\n%b\n' "${C_DIM}Details were written to ${LOG_FILE#"$REPO_ROOT"/}${C_OFF}"
    log "FAIL: $1"
    exit 1
}

have() { command -v "$1" >/dev/null 2>&1; }

# Run a command quietly, tee-ing everything into the log. Show output with --verbose.
run_logged() {
    log "RUN: $*"
    if [[ "$VERBOSE" == "1" ]]; then
        "$@" 2>&1 | tee -a "$LOG_FILE"
        return "${PIPESTATUS[0]}"
    fi
    "$@" >>"$LOG_FILE" 2>&1
}

harness_python() {
    if [[ -x "$VENV_PYTHON" ]]; then printf '%s' "$VENV_PYTHON"; else printf '%s' "python3"; fi
}

ensure_docker_path() {
    if have docker; then return 0; fi
    local candidates=(
        "/usr/local/bin"
        "/opt/homebrew/bin"
        "$HOME/.docker/bin"
        "/Applications/Docker.app/Contents/Resources/bin"
        "/usr/bin"
    )
    for dir in "${candidates[@]}"; do
        if [[ -x "$dir/docker" ]]; then
            export PATH="$dir:$PATH"
            return 0
        fi
    done
    return 1
}

docker_state() {
    ensure_docker_path
    if ! have docker; then printf 'missing'; return; fi
    if docker info >/dev/null 2>&1; then printf 'running'; return; fi

    # Probe standard socket locations if DOCKER_HOST was unset
    if [[ -z "${DOCKER_HOST:-}" ]]; then
        local sockets=(
            "unix://$HOME/.docker/run/docker.sock"
            "unix://$HOME/.docker/desktop/docker.sock"
            "unix:///var/run/docker.sock"
        )
        for sock in "${sockets[@]}"; do
            local sock_path="${sock#unix://}"
            if [[ -S "$sock_path" ]]; then
                if DOCKER_HOST="$sock" docker info >/dev/null 2>&1; then
                    export DOCKER_HOST="$sock"
                    printf 'running'
                    return
                fi
            fi
        done
    fi

    local err
    err="$(docker info 2>&1 || true)"
    if grep -qi "permission denied" <<<"$err"; then
        printf 'permission_denied'
    else
        printf 'stopped'
    fi
}

report_docker() {
    case "$(docker_state)" in
        running) ok "Docker is running (OpenFOAM validation available)" ;;
        permission_denied)
            warn "Docker is running, but your user does not have permission to access the socket."
            say "  On Linux, add yourself to the docker group and reload permissions:"
            say "    sudo usermod -aG docker \$USER && newgrp docker"
            ;;
        stopped)
            warn "Docker is not currently running."
            say "  You can still create and inspect simulations, but the assistant"
            say "  cannot run OpenFOAM validation until Docker is started."
            ;;
        *)
            warn "Docker is not installed."
            say "  You can still create and inspect simulations, but the assistant"
            say "  cannot run OpenFOAM validation without Docker."
            ;;
    esac
}
