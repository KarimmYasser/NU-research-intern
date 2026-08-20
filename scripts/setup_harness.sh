#!/usr/bin/env bash
# ==============================================================================
# OpenFOAM AI assistant — first-time setup.
# Public entry point: ./setup.sh
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/agents/common.sh
source "$SCRIPT_DIR/agents/common.sh"

AGENT_TARGET="pi"
VERBOSE=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --agent)
            [[ $# -ge 2 ]] || { echo "--agent requires a value (pi, cline, claude)" >&2; exit 2; }
            AGENT_TARGET="$2"; shift 2 ;;
        --agent=*) AGENT_TARGET="${1#*=}"; shift ;;
        --verbose) VERBOSE=1; shift ;;
        -h|--help)
            cat <<'USAGE'
Usage: ./setup.sh [--agent pi|cline|claude] [--verbose]

Sets up the OpenFOAM AI assistant. With no arguments it sets up Pi,
which is the supported default.
USAGE
            exit 0 ;;
        *) echo "Unknown option: $1" >&2; exit 2 ;;
    esac
done

cd "$REPO_ROOT"
log_init "setup --agent $AGENT_TARGET"

say ""
say "Setting up your OpenFOAM AI assistant..."
say ""

# --- 1. Required software ----------------------------------------------------
have python3 || die "Python 3 is not installed." \
    "Please install Python 3.11 or newer and run ./setup.sh again."
if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1; then
    die "Python 3.11 or newer is required." \
        "Found $(python3 --version 2>&1). Please install Python 3.11 or newer and run ./setup.sh again."
fi
ok "Python is ready ($(python3 --version 2>&1))"

# --- 2 & 3. Private Python environment + dependencies -----------------------
if [[ ! -x "$VENV_PYTHON" ]]; then
    if ! run_logged python3 -m venv "$VENV_DIR"; then
        VENV_HINT="See the log for details, then run ./setup.sh again."
        if [[ -f /etc/debian_version || -f /etc/os-release ]] && grep -qiE "debian|ubuntu" /etc/*release* 2>/dev/null; then
            VENV_HINT="On Debian/Ubuntu systems, install the python3-venv package:
    sudo apt install python3-venv (or python3.12-venv / python3.11-venv)"
        fi
        die "The private Python environment could not be created." "$VENV_HINT"
    fi
fi
run_logged "$VENV_PYTHON" -m pip install --upgrade pip \
    || warn "pip could not be upgraded; continuing with the existing version."
run_logged "$VENV_PYTHON" -m pip install -r "$REPO_ROOT/requirements.txt" \
    || die "The OpenFOAM assistant's components could not be installed." \
           "Check your internet connection and run ./setup.sh again."
ok "OpenFOAM tools are installed"

# --- 5. Simulation workspace -------------------------------------------------
mkdir -p "$WORKSPACE_DIR"
[[ -f "$WORKSPACE_DIR/.gitkeep" ]] || : >"$WORKSPACE_DIR/.gitkeep"

# --- 6. Python self-test -----------------------------------------------------
if ! PYTHONPATH="$REPO_ROOT/src" run_logged "$VENV_PYTHON" -m harness.mcp_server \
        --self-test --workspace-dir "$WORKSPACE_DIR"; then
    die "The OpenFOAM components are not working correctly." \
        "This is usually fixed by deleting the .venv folder and running ./setup.sh again."
fi

# --- 4. Connect the assistant to the chosen agent ---------------------------
case "$AGENT_TARGET" in
    pi)
        # shellcheck source=scripts/agents/pi.sh
        source "$SCRIPT_DIR/agents/pi.sh"
        pi_require
        pi_supports_packages || die "Your version of Pi is too old for the OpenFOAM tools." \
            "Update Pi and run ./setup.sh again:
    npm install -g @earendil-works/pi-coding-agent@latest"
        log "pi version: $(pi_version)"
        pi_require_typescript_runtime
        ok "TypeScript runtime is ready"
        pi_sync_skill
        pi_install_adapter
        pi_adapter_test || die "The OpenFOAM tools did not load correctly inside Pi." \
            "Run ./setup.sh --verbose to see what went wrong."
        ;;
    cline)
        have cline || die "The Cline CLI is not installed." "Install Cline and run ./setup.sh --agent cline again."
        warn "Cline support is experimental; Pi is the supported default."
        run_logged cline mcp add openfoam "$VENV_PYTHON" -m harness.mcp_server \
            --workspace-dir "$WORKSPACE_DIR" \
            || die "Cline could not register the OpenFOAM tools."
        ok "Cline is connected to the harness"
        ;;
    claude)
        warn "Claude support is experimental; Pi is the supported default."
        CONFIG="$REPO_ROOT/.mcp.json"
        [[ -f "$CONFIG" ]] && cp "$CONFIG" "$CONFIG.bak.$(date +%Y%m%d%H%M%S)"
        python3 - "$CONFIG" "$VENV_PYTHON" "$REPO_ROOT/src" "$WORKSPACE_DIR" <<'PY'
import json, os, sys, tempfile
path, python, pythonpath, workspace = sys.argv[1:5]
try:
    with open(path, encoding="utf-8") as stream:
        config = json.load(stream)
except FileNotFoundError:
    config = {}
config.setdefault("mcpServers", {})["openfoam"] = {
    "command": python,
    "args": ["-m", "harness.mcp_server", "--workspace-dir", workspace],
    "env": {"PYTHONPATH": pythonpath},
}
fd, temporary = tempfile.mkstemp(dir=os.path.dirname(path) or ".", prefix=".mcp.", text=True)
with os.fdopen(fd, "w", encoding="utf-8") as stream:
    json.dump(config, stream, indent=2)
    stream.write("\n")
os.replace(temporary, path)
PY
        ok "Claude is connected to the harness"
        ;;
    *) die "Unsupported assistant: $AGENT_TARGET" "Supported values are: pi (default), cline, claude." ;;
esac

say "✓ Your simulation workspace is ready"
report_docker

if [[ "$AGENT_TARGET" == "pi" ]]; then
    pi_auth_hint
fi

say ""
say "Setup complete."
say ""
say "Start your assistant with:"
say ""
say "    ./start.sh"
say ""
