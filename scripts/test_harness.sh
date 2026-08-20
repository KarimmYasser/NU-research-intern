#!/usr/bin/env bash
# ==============================================================================
# OpenFOAM Agent Harness — Comprehensive Diagnostics & Tool Tests
# ==============================================================================

set -e

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

GREEN='\033[0;32m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${BLUE}==================================================================${NC}"
echo -e "${BLUE}           🧪 OpenFOAM Agent Harness Diagnostics & Tests          ${NC}"
echo -e "${BLUE}==================================================================${NC}"
echo ""

# Activate virtualenv if available
if [ -f "$REPO_ROOT/.venv/bin/activate" ]; then
    source "$REPO_ROOT/.venv/bin/activate"
fi

export PYTHONPATH="$REPO_ROOT/src"

echo "1. Checking MCP Server Protocol & Tool Registry..."
python3 -m harness.mcp_server --self-test

echo ""
echo "2. Testing Case Scaffolding, File I/O & Case Cleaning..."
python3 -c '
from harness.tools import OpenFOAMToolbox
import tempfile, os

with tempfile.TemporaryDirectory() as tmpdir:
    tb = OpenFOAMToolbox(workspace_dir=tmpdir)
    # 1. Scaffold
    res = tb.scaffold_case(case_name="test_cavity", target_dir=tmpdir)
    assert res["success"], f"Scaffold failed: {res}"
    n_files = len(res["rendered_files"])
    print(f"   ✓ Scaffolding passed! Rendered {n_files} files.")
    # 2. Read File
    read_res = tb.read_case_file(res["case_dir"], "system/controlDict")
    assert read_res["success"], f"Read file failed: {read_res}"
    n_lines = read_res["lines"]
    print(f"   ✓ Case file read passed ({n_lines} lines in controlDict)!")
    # 3. Write File
    write_res = tb.write_case_file(res["case_dir"], "system/customDict", "myEntry 123;")
    assert write_res["success"], f"Write file failed: {write_res}"
    print("   ✓ Case file write passed!")
    # 4. Clean Case
    clean_res = tb.clean_case_directory(res["case_dir"])
    assert clean_res["success"], f"Clean case failed: {clean_res}"
    print("   ✓ Clean case directory passed!")
'

echo ""
echo "3. Testing Level A Static Preflight Audit..."
python3 -c '
from harness.tools import OpenFOAMToolbox
import tempfile

with tempfile.TemporaryDirectory() as tmpdir:
    tb = OpenFOAMToolbox(workspace_dir=tmpdir)
    res = tb.scaffold_case(case_name="test_cavity", target_dir=tmpdir)
    audit_res = tb.run_preflight_audit(res["case_dir"])
    assert audit_res["success"], f"Audit failed: {audit_res}"
    v = audit_res["verdict"]
    print(f"   ✓ Level A Static Audit passed (verdict: {v})!")
'

echo ""
echo "4. Testing Chunkless Multi-Step Graph RAG & Numerics..."
python3 -c '
from harness.tools import OpenFOAMToolbox
tb = OpenFOAMToolbox()

# Multi-step chunkless graph walk
res = tb.search_openfoam_knowledge("transient buoyant cavity")
assert res["recommended_solver"] is not None
n_cases = len(res["reference_cases"])
n_fams = len(res["matched_families"])
s_name = res["recommended_solver"]["name"]
print(f"   ✓ Chunkless RAG graph walk passed (recommended: {s_name}, {n_fams} families, {n_cases} reference cases)!")

# Numerics snippet lookup
num_res = tb.get_tutorial_numerics(solver_name="foamRun")
assert num_res["solver"] == "foamRun"
print("   ✓ Tutorial numerics lookup passed!")

env_res = tb.check_physical_envelope("air", "nu", 1.5e-5)
assert env_res["in_bounds"]
print("   ✓ Physical envelope verification passed.")
'

echo ""
echo "5. Testing CFL Timestep, Case Status & Error Diagnosis..."
python3 -c '
from harness.tools import OpenFOAMToolbox
import tempfile

tb = OpenFOAMToolbox()

# 1. CFL calculation
cfl_res = tb.calculate_cfl_timestep(box_size=[0.1, 0.1, 0.01], cells=[20, 20, 1], max_velocity=10.0, target_cfl=0.5)
assert cfl_res["success"] and cfl_res["recommended_deltaT"] > 0
dt = cfl_res["recommended_deltaT"]
print(f"   ✓ CFL calculation passed (recommended deltaT={dt}s for U=10m/s)!")

# 2. Case status
with tempfile.TemporaryDirectory() as tmpdir:
    tb = OpenFOAMToolbox(workspace_dir=tmpdir)
    res = tb.scaffold_case(case_name="status_test", target_dir=tmpdir)
    status_res = tb.get_case_status(res["case_dir"])
    assert status_res["success"] and status_res["has_mesh"]
    print("   ✓ Case status inspector passed!")

# 3. Error diagnosis
diag_res = tb.diagnose_case_failure("FATAL IO ERROR: keyword rho is undefined in fvSolution")
assert diag_res["failure_class"] == "DICTIONARY_SYNTAX"
print("   ✓ Expert error diagnosis engine passed!")
'

echo ""
echo "6. Testing the Pi adapter against the real Python harness..."
if command -v node >/dev/null 2>&1; then
    if node "$REPO_ROOT/tests/pi_adapter/adapter_test.mjs" 2>/dev/null; then
        :
    elif node --experimental-strip-types "$REPO_ROOT/tests/pi_adapter/adapter_test.mjs" 2>/dev/null; then
        :
    elif command -v tsx >/dev/null 2>&1 && tsx "$REPO_ROOT/tests/pi_adapter/adapter_test.mjs" 2>/dev/null; then
        :
    else
        node "$REPO_ROOT/tests/pi_adapter/adapter_test.mjs"
    fi
else
    echo "   (skipped: Node.js is not installed)"
fi

echo ""
echo "7. Testing the installer and launcher scripts..."
bash "$REPO_ROOT/tests/shell/installer_test.sh"

echo ""
echo -e "${GREEN}==================================================================${NC}"
echo -e "${GREEN}       Harness smoke diagnostics completed successfully.          ${NC}"
echo -e "${GREEN}==================================================================${NC}"
