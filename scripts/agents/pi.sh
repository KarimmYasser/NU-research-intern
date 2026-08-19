#!/usr/bin/env bash
# ==============================================================================
# Pi adapter for the OpenFOAM assistant.
# Installs the project-local Pi package (integrations/pi) and launches Pi.
# ==============================================================================

PI_PACKAGE_DIR="$REPO_ROOT/integrations/pi"
PI_PROJECT_SETTINGS="$REPO_ROOT/.pi/settings.json"

PI_INSTALL_HINT="Installation command:
    npm install -g @earendil-works/pi-coding-agent"

# Populated by pi_require_typescript_runtime. Kept as an array so optional Node
# flags and the tsx fallback are passed without shell re-parsing.
PI_TYPESCRIPT_RUNNER=()

pi_present() { have pi; }

# The pi skill is a symlink to src/harness/rules/openfoam.md so there is only one
# copy of the agent rules. Some checkouts cannot materialise symlinks (Windows
# without developer mode, zip downloads, rsync without -l): git then writes a
# plain text file containing the link target, and pi silently skips the skill.
# Replace it with a real copy in that case, and keep the copy in sync every run.
pi_sync_skill() {
    local rules_dir="$REPO_ROOT/src/harness/rules"
    local skill_dir="$REPO_ROOT/integrations/pi/skills/openfoam"

    [[ -f "$rules_dir/openfoam.md" ]] || die "The assistant's instruction file is missing." \
        "Expected it at: src/harness/rules/openfoam.md"

    mkdir -p "$skill_dir"
    # SKILL.md is a file link; references/ is a directory link. Same repair rule.
    _pi_link_or_copy "$rules_dir/openfoam.md" "$skill_dir/SKILL.md"
    _pi_link_or_copy "$rules_dir/references"  "$skill_dir/references"
}

# Leave an intact symlink alone; otherwise replace the target with a fresh copy.
_pi_link_or_copy() {
    local source="$1" target="$2"
    [[ -e "$source" ]] || return 0

    # Working symlink: nothing to do.
    if [[ -L "$target" && -e "$target" ]]; then return 0; fi
    # Plain copy that is already identical: nothing to do.
    if [[ ! -L "$target" && -e "$target" ]]; then
        if diff -r -q "$source" "$target" >/dev/null 2>&1; then return 0; fi
    fi

    rm -rf "$target"
    cp -R "$source" "$target"
    log "Materialised $(basename "$target") as a copy (symlinks unavailable or stale)."
}

# Hard requirement: Pi itself.
pi_require() {
    if pi_present; then return 0; fi
    die "Pi is not installed." "Please install Pi and then run ./setup.sh again.

$PI_INSTALL_HINT"
}

# Pi must be new enough to support project-local packages (pi install -l).
pi_supports_packages() {
    pi install --help >/dev/null 2>&1
}

pi_version() { pi --version 2>/dev/null | head -1; }

# The adapter is a .ts file. Pi has its own TypeScript loader, but setup also
# imports the extension directly for its deterministic self-test. Accept either
# Node's built-in type stripping or the standalone tsx runtime.
pi_require_typescript_runtime() {
    PI_TYPESCRIPT_RUNNER=()
    local extension="$PI_PACKAGE_DIR/extensions/openfoam-tools.ts"

    if have node && node --check "$extension" >/dev/null 2>&1; then
        PI_TYPESCRIPT_RUNNER=(node)
        return 0
    fi

    if have node && node --experimental-strip-types --check "$extension" >/dev/null 2>&1; then
        PI_TYPESCRIPT_RUNNER=(node --experimental-strip-types)
        return 0
    fi

    if have tsx && tsx --version >/dev/null 2>&1; then
        PI_TYPESCRIPT_RUNNER=(tsx)
        return 0
    fi

    die "Node.js cannot load the TypeScript OpenFOAM extension." \
        "Install Node.js 22.6 or newer, or install the tsx runtime, then run ./setup.sh again:

    npm install -g tsx"
}

# Is the adapter already registered in project settings?
pi_adapter_installed() {
    [[ -f "$PI_PROJECT_SETTINGS" ]] || return 1
    python3 - "$PI_PROJECT_SETTINGS" "$PI_PACKAGE_DIR" <<'PY'
import json, os, sys
settings_path, package_dir = sys.argv[1:3]
try:
    with open(settings_path, encoding="utf-8") as stream:
        settings = json.load(stream)
except (OSError, ValueError):
    sys.exit(1)
target = os.path.realpath(package_dir)
base = os.path.dirname(os.path.realpath(settings_path))
for entry in settings.get("packages", []):
    source = entry.get("source") if isinstance(entry, dict) else entry
    if not isinstance(source, str):
        continue
    candidate = source if os.path.isabs(source) else os.path.join(base, source)
    if os.path.realpath(candidate) == target:
        sys.exit(0)
sys.exit(1)
PY
}

pi_backup_settings() {
    [[ -f "$PI_PROJECT_SETTINGS" ]] || return 0
    local backup="$PI_PROJECT_SETTINGS.bak.$(date +%Y%m%d%H%M%S)"
    cp "$PI_PROJECT_SETTINGS" "$backup"
    log "Backed up $PI_PROJECT_SETTINGS -> $backup"
}

# Install the adapter into .pi/settings.json only. Never touches global settings.
pi_install_adapter() {
    if pi_adapter_installed; then
        ok "OpenFOAM tools are already connected to Pi"
        return 0
    fi
    pi_backup_settings
    if ! run_logged pi install "$PI_PACKAGE_DIR" -l; then
        die "The OpenFOAM tools could not be connected to Pi." \
            "Pi reported an error while installing the local adapter package."
    fi
    if ! pi_adapter_installed; then
        die "The OpenFOAM tools could not be connected to Pi." \
            "Pi finished without registering the adapter in .pi/settings.json."
    fi
    ok "Pi is connected to the harness"
}

# Deterministic adapter test: drives the TypeScript extension with a stub Pi API.
pi_adapter_test() {
    local test_script="$REPO_ROOT/tests/pi_adapter/adapter_test.mjs"
    [[ -f "$test_script" ]] || { warn "Adapter self-test is missing; skipped."; return 0; }
    [[ ${#PI_TYPESCRIPT_RUNNER[@]} -gt 0 ]] || pi_require_typescript_runtime
    if run_logged "${PI_TYPESCRIPT_RUNNER[@]}" "$test_script"; then
        ok "Safety checks passed"
        return 0
    fi
    return 1
}

# Soft check: Pi needs a configured model before it can answer anything.
pi_auth_hint() {
    local auth_file="$HOME/.pi/agent/auth.json"
    if [[ -s "$auth_file" ]] || [[ -n "${ANTHROPIC_API_KEY:-}${OPENAI_API_KEY:-}${GEMINI_API_KEY:-}${GOOGLE_API_KEY:-}" ]]; then
        return 0
    fi
    say ""
    warn "The OpenFOAM tools are installed, but Pi still needs an AI model."
    say ""
    say "Run:"
    say "    pi"
    say ""
    say "Then follow Pi's sign-in instructions. After that, run:"
    say "    ./start.sh"
}

# Launch Pi from the repository so project-local settings and skills load.
pi_launch() {
    cd "$REPO_ROOT"
    exec pi --approve "$@"
}
