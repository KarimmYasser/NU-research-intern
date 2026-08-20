#!/usr/bin/env bash
# ==============================================================================
# Regression tests for the user-facing installer/launcher scripts.
#
# Uses a temporary HOME and fake `pi` / `docker` binaries so nothing on the real
# machine is touched. Run: bash tests/shell/installer_test.sh
# ==============================================================================
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PASS=0
FAIL=0

pass() { printf '  ok   %s\n' "$1"; PASS=$((PASS + 1)); }
fail() { printf '  FAIL %s\n     %s\n' "$1" "${2:-}"; FAIL=$((FAIL + 1)); }

# Create an isolated copy of the repo scripts + a fake environment.
make_sandbox() {
    local sandbox="$1"
    mkdir -p "$sandbox/home" "$sandbox/bin" "$sandbox/repo"
    # Copy just what the scripts need (keeps tests fast, avoids the real .venv).
    cp -R "$REPO_ROOT/scripts" "$sandbox/repo/"
    cp -R "$REPO_ROOT/integrations" "$sandbox/repo/"
    cp "$REPO_ROOT/setup.sh" "$REPO_ROOT/start.sh" "$REPO_ROOT/check.sh" "$sandbox/repo/"
    mkdir -p "$sandbox/repo/src/harness"
    : >"$sandbox/repo/src/harness/mcp_server.py"
    printf 'pytest\n' >"$sandbox/repo/requirements.txt"
    # Canonical agent rules (the real repo ships these under src/harness/rules/).
    mkdir -p "$sandbox/repo/src/harness/rules"
    printf -- '---\nname: openfoam\ndescription: test rules\n---\n\n# Rules\n' \
        >"$sandbox/repo/src/harness/rules/openfoam.md"
    # Pre-seed a stub virtualenv so the tests exercise script logic, not pip.
    mkdir -p "$sandbox/repo/.venv/bin"
    cat >"$sandbox/repo/.venv/bin/python3" <<'STUB'
#!/usr/bin/env bash
# Stub interpreter: pip installs and harness self-tests always succeed.
exit 0
STUB
    chmod +x "$sandbox/repo/.venv/bin/python3"

    # Stub system python3 in sandbox/bin that satisfies >= 3.11 check
    cat >"$sandbox/bin/python3" <<'PYSTUB'
#!/usr/bin/env bash
if [[ "$*" == *"--version"* ]]; then
    echo "Python 3.12.0"
    exit 0
fi
if [[ "$*" == *"sys.version_info"* ]]; then
    exit 0
fi
# Delegate to system python for inline scripts if available
for candidate in /usr/local/bin/python3 /opt/homebrew/bin/python3 /usr/bin/python3; do
    if [[ -x "$candidate" && "$candidate" != "$0" ]]; then
        exec "$candidate" "$@"
    fi
done
exit 0
PYSTUB
    chmod +x "$sandbox/bin/python3"
}

# A fake `pi` that records its arguments and emulates `pi install <path> -l`.
make_fake_pi() {
    local bin="$1"
    cat >"$bin/pi" <<'FAKE'
#!/usr/bin/env bash
echo "$@" >>"${PI_CALL_LOG:-/dev/null}"
case "${1:-}" in
    --version) echo "0.84.2"; exit 0 ;;
    install)
        if [[ "${2:-}" == "--help" ]]; then echo "install help"; exit 0; fi
        package="$2"
        mkdir -p "$PWD/.pi"
        python3 - "$PWD/.pi/settings.json" "$package" <<'PY'
import json, os, sys
path, package = sys.argv[1:3]
try:
    with open(path, encoding="utf-8") as fh:
        settings = json.load(fh)
except (OSError, ValueError):
    settings = {}
rel = os.path.relpath(package, os.path.dirname(path))
settings.setdefault("packages", [])
if rel not in settings["packages"]:
    settings["packages"].append(rel)
with open(path, "w", encoding="utf-8") as fh:
    json.dump(settings, fh, indent=2)
PY
        exit 0 ;;
esac
exit 0
FAKE
    chmod +x "$bin/pi"
}

run_setup() {
    local sandbox="$1"; shift
    ( cd "$sandbox/repo" && HOME="$sandbox/home" PATH="$sandbox/bin:$PATH" \
        PI_CALL_LOG="$sandbox/pi-calls.log" ./setup.sh "$@" 2>&1 )
}

# ---------------------------------------------------------------------------
# Test: Pi is the default agent and setup needs no arguments
# ---------------------------------------------------------------------------
test_pi_is_default() {
    local sandbox; sandbox="$(mktemp -d)"
    make_sandbox "$sandbox"; make_fake_pi "$sandbox/bin"
    run_setup "$sandbox" >/dev/null 2>&1
    if grep -q "integrations/pi" "$sandbox/repo/.pi/settings.json" 2>/dev/null; then
        pass "setup.sh defaults to pi with no arguments"
    else
        fail "setup.sh defaults to pi with no arguments" "no adapter entry in .pi/settings.json"
    fi
    rm -rf "$sandbox"
}

# ---------------------------------------------------------------------------
# Test: existing Pi settings are preserved and setup is idempotent
# ---------------------------------------------------------------------------
test_preserves_and_idempotent() {
    local sandbox; sandbox="$(mktemp -d)"
    make_sandbox "$sandbox"; make_fake_pi "$sandbox/bin"
    mkdir -p "$sandbox/repo/.pi"
    cat >"$sandbox/repo/.pi/settings.json" <<'JSON'
{"theme": "dark", "packages": ["npm:some-other-package"]}
JSON
    run_setup "$sandbox" >/dev/null 2>&1
    run_setup "$sandbox" >/dev/null 2>&1

    local report
    report="$(python3 - "$sandbox/repo/.pi/settings.json" <<'PY'
import json, sys
settings = json.load(open(sys.argv[1], encoding="utf-8"))
packages = settings.get("packages", [])
ours = [p for p in packages if isinstance(p, str) and "integrations/pi" in p]
print("theme=%s other=%s ours=%d" % (
    settings.get("theme"),
    "npm:some-other-package" in packages,
    len(ours),
))
PY
)"
    [[ "$report" == "theme=dark other=True ours=1" ]] \
        && pass "existing settings preserved and install is idempotent" \
        || fail "existing settings preserved and install is idempotent" "$report"

    # A backup must exist from the run that modified settings.
    if compgen -G "$sandbox/repo/.pi/settings.json.bak.*" >/dev/null; then
        pass "settings are backed up before modification"
    else
        fail "settings are backed up before modification" "no .bak file found"
    fi
    rm -rf "$sandbox"
}

# ---------------------------------------------------------------------------
# Test: missing Pi produces a clear, non-technical message
# ---------------------------------------------------------------------------
test_missing_pi_message() {
    local sandbox; sandbox="$(mktemp -d)"
    make_sandbox "$sandbox"
    # Ensure no pi binary is on PATH even if pi is globally installed on host
    local isolated_bin="$sandbox/clean_bin"
    mkdir -p "$isolated_bin"
    for tool in bash sh cat rm cp mv mkdir date dirname basename diff grep sed chmod mktemp touch uname which env; do
        local tool_path; tool_path="$(command -v "$tool" || true)"
        [[ -n "$tool_path" ]] && ln -s "$tool_path" "$isolated_bin/$tool" 2>/dev/null || true
    done
    cp "$sandbox/bin/python3" "$isolated_bin/python3"

    local output
    output="$( cd "$sandbox/repo" && HOME="$sandbox/home" \
        PATH="$isolated_bin" ./setup.sh 2>&1 )"
    if grep -q "Pi is not installed" <<<"$output" \
        && grep -q "npm install -g @earendil-works/pi-coding-agent" <<<"$output"; then
        pass "missing Pi produces an actionable message"
    else
        fail "missing Pi produces an actionable message" "$(tail -3 <<<"$output")"
    fi
    # Nothing should have been written to settings on failure.
    if [[ ! -f "$sandbox/repo/.pi/settings.json" ]]; then
        pass "failed setup leaves no partial Pi settings"
    else
        fail "failed setup leaves no partial Pi settings" "settings.json was created"
    fi
    rm -rf "$sandbox"
}

# ---------------------------------------------------------------------------
# Test: Docker absence is a warning, not a fatal error
# ---------------------------------------------------------------------------
test_docker_absence_is_warning() {
    local sandbox; sandbox="$(mktemp -d)"
    make_sandbox "$sandbox"; make_fake_pi "$sandbox/bin"
    # Fake docker that is installed but not running.
    printf '#!/usr/bin/env bash\nexit 1\n' >"$sandbox/bin/docker"
    chmod +x "$sandbox/bin/docker"
    local output rc
    output="$(run_setup "$sandbox")"; rc=$?
    if [[ $rc -eq 0 ]] && grep -qi "Docker is not currently running" <<<"$output"; then
        pass "docker being down is a warning, not a failure"
    else
        fail "docker being down is a warning, not a failure" "rc=$rc"
    fi
    rm -rf "$sandbox"
}

# ---------------------------------------------------------------------------
# Test: paths containing spaces work
# ---------------------------------------------------------------------------
test_paths_with_spaces() {
    local base; base="$(mktemp -d)"
    local sandbox="$base/dir with spaces"
    mkdir -p "$sandbox"
    make_sandbox "$sandbox"; make_fake_pi "$sandbox/bin"
    run_setup "$sandbox" >/dev/null 2>&1
    [[ -f "$sandbox/repo/.pi/settings.json" ]] \
        && pass "works from a path containing spaces" \
        || fail "works from a path containing spaces" "setup did not complete"
    rm -rf "$base"
}

# ---------------------------------------------------------------------------
# Test: scripts work when launched from outside the repository
# ---------------------------------------------------------------------------
test_launch_from_elsewhere() {
    local sandbox; sandbox="$(mktemp -d)"
    make_sandbox "$sandbox"; make_fake_pi "$sandbox/bin"
    local other; other="$(mktemp -d)"
    ( cd "$other" && HOME="$sandbox/home" PATH="$sandbox/bin:$PATH" \
        PI_CALL_LOG="$sandbox/pi-calls.log" "$sandbox/repo/setup.sh" >/dev/null 2>&1 )
    [[ -f "$sandbox/repo/.pi/settings.json" ]] \
        && pass "setup works when invoked from another directory" \
        || fail "setup works when invoked from another directory" "settings not written to the repo"
    rm -rf "$sandbox" "$other"
}

# ---------------------------------------------------------------------------
# Test: launcher refuses to start before setup and points at ./setup.sh
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Test: a checkout without symlink support still gets a usable skill file
# (Windows/git without core.symlinks, zip downloads, rsync without -l)
# ---------------------------------------------------------------------------
test_skill_materialised_without_symlinks() {
    local sandbox; sandbox="$(mktemp -d)"
    make_sandbox "$sandbox"; make_fake_pi "$sandbox/bin"
    local skill="$sandbox/repo/integrations/pi/skills/openfoam/SKILL.md"

    # Reproduce what git writes when it cannot create a symlink: a text file
    # whose contents are the link target.
    rm -f "$skill"
    printf -- '../../../../src/harness/rules/openfoam.md' >"$skill"

    run_setup "$sandbox" >/dev/null 2>&1

    if head -1 "$skill" | grep -q '^---$' && grep -q 'name: openfoam' "$skill"; then
        pass "skill is repaired when symlinks are unavailable"
    else
        fail "skill is repaired when symlinks are unavailable" "SKILL.md is still: $(head -1 "$skill")"
    fi

    # And a stale copy is refreshed rather than left to drift.
    printf 'stale content\n' >"$skill"
    run_setup "$sandbox" >/dev/null 2>&1
    if cmp -s "$skill" "$sandbox/repo/src/harness/rules/openfoam.md"; then
        pass "a drifted skill copy is re-synced on setup"
    else
        fail "a drifted skill copy is re-synced on setup" "copy was not refreshed"
    fi
    rm -rf "$sandbox"
}

# ---------------------------------------------------------------------------
# Test: an intact symlink is left alone (not replaced by a copy)
# ---------------------------------------------------------------------------
test_intact_symlink_preserved() {
    local sandbox; sandbox="$(mktemp -d)"
    make_sandbox "$sandbox"; make_fake_pi "$sandbox/bin"
    local skill="$sandbox/repo/integrations/pi/skills/openfoam/SKILL.md"
    rm -f "$skill"
    ln -s ../../../../src/harness/rules/openfoam.md "$skill"
    run_setup "$sandbox" >/dev/null 2>&1
    [[ -L "$skill" ]] \
        && pass "an intact symlink is left as a symlink" \
        || fail "an intact symlink is left as a symlink" "it was replaced by a copy"
    rm -rf "$sandbox"
}

test_launcher_requires_setup() {
    local sandbox; sandbox="$(mktemp -d)"
    make_sandbox "$sandbox"; make_fake_pi "$sandbox/bin"
    local output rc
    output="$( cd "$sandbox/repo" && HOME="$sandbox/home" PATH="$sandbox/bin:$PATH" ./start.sh 2>&1 )"
    rc=$?
    if [[ $rc -ne 0 ]] && grep -q "./setup.sh" <<<"$output"; then
        pass "launcher tells the user to run ./setup.sh first"
    else
        fail "launcher tells the user to run ./setup.sh first" "rc=$rc: $(tail -2 <<<"$output")"
    fi
    rm -rf "$sandbox"
}

# ---------------------------------------------------------------------------
# Test: all public scripts are executable and syntactically valid
# ---------------------------------------------------------------------------
test_scripts_valid() {
    local bad=""
    for script in "$REPO_ROOT"/setup.sh "$REPO_ROOT"/start.sh "$REPO_ROOT"/check.sh \
                  "$REPO_ROOT"/scripts/*.sh "$REPO_ROOT"/scripts/agents/*.sh; do
        [[ -x "$script" ]] || bad+=" not-executable:$(basename "$script")"
        bash -n "$script" 2>/dev/null || bad+=" syntax:$(basename "$script")"
    done
    [[ -z "$bad" ]] && pass "all scripts are executable and valid" \
        || fail "all scripts are executable and valid" "$bad"
}

test_python_too_old_rejected() {
    local sandbox; sandbox="$(mktemp -d)"
    make_sandbox "$sandbox"; make_fake_pi "$sandbox/bin"
    # Stub python 3.10
    cat >"$sandbox/bin/python3" <<'PYSTUB'
#!/usr/bin/env bash
if [[ "$*" == *"--version"* ]]; then echo "Python 3.10.12"; exit 0; fi
if [[ "$*" == *"sys.version_info"* ]]; then exit 1; fi
exit 0
PYSTUB
    chmod +x "$sandbox/bin/python3"
    local output rc
    output="$(run_setup "$sandbox")"; rc=$?
    if [[ $rc -ne 0 ]] && grep -q "Python 3.11 or newer is required" <<<"$output"; then
        pass "python < 3.11 is rejected with actionable message"
    else
        fail "python < 3.11 is rejected with actionable message" "rc=$rc: $(tail -2 <<<"$output")"
    fi
    rm -rf "$sandbox"
}

test_pi_runtime_failure_message() {
    local sandbox; sandbox="$(mktemp -d)"
    make_sandbox "$sandbox"
    # pi exists but crashes on execution
    printf '#!/usr/bin/env bash\nexit 1\n' >"$sandbox/bin/pi"
    chmod +x "$sandbox/bin/pi"
    printf '#!/usr/bin/env bash\necho "v18.19.1"\n' >"$sandbox/bin/node"
    chmod +x "$sandbox/bin/node"
    local output rc
    output="$(run_setup "$sandbox")"; rc=$?
    if [[ $rc -ne 0 ]] && grep -q "Node.js 22.19 or newer" <<<"$output"; then
        pass "pi runtime failure highlights Node version requirement"
    else
        fail "pi runtime failure highlights Node version requirement" "rc=$rc: $(tail -3 <<<"$output")"
    fi
    rm -rf "$sandbox"
}

printf '\nInstaller regression tests\n\n'
test_scripts_valid
test_python_too_old_rejected
test_pi_runtime_failure_message
test_pi_is_default
test_preserves_and_idempotent
test_missing_pi_message
test_docker_absence_is_warning
test_paths_with_spaces
test_launch_from_elsewhere
test_skill_materialised_without_symlinks
test_intact_symlink_preserved
test_launcher_requires_setup

printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[[ $FAIL -eq 0 ]]
