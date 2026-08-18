"""Model Context Protocol (MCP) Server for OpenFOAM AI Agents.

Implements standard JSON-RPC 2.0 stdio MCP transport (compatible with Pi Agent,
Cline, Claude Code, Cursor, Windsurf, Zed, and any standard MCP client).

Exposes 19 comprehensive OpenFOAM tools covering knowledge search, case scaffolding,
external mesh importing, Level A static preflight, Level B checkMesh metrics,
Level C bounded dry-run with NaN/divergence prediction, Docker validation ladder,
CFL time-step calculation, general OpenFOAM utility execution, case status inspection,
post-processing data parsing, expert error diagnosis, file I/O, and case cleaning.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .tools import OpenFOAMToolbox

SERVER_NAME = "openfoam-agent-harness"
SERVER_VERSION = "0.2.0"
PROTOCOL_VERSION = "2024-11-05"

# Single source of truth for agent behaviour. MCP clients (Cline/Claude) get this
# text through prompts/get; the pi skill at integrations/pi/skills/openfoam/SKILL.md
# is a symlink to it, so there is exactly one copy to maintain.
AGENT_RULES_PATH = Path(__file__).resolve().parent / "rules" / "openfoam.md"
AGENT_RULES_FALLBACK = "You are an expert OpenFOAM v13 CFD assistant."


def _strip_frontmatter(text: str) -> str:
    """Remove the YAML frontmatter block a skill file needs but a prompt does not."""
    if not text.startswith("---"):
        return text
    parts = text.split("---", 2)
    return parts[2].lstrip("\n") if len(parts) == 3 else text


def load_agent_rules() -> str:
    """Load the shared OpenFOAM behaviour rules (skill body, frontmatter removed)."""
    try:
        return _strip_frontmatter(AGENT_RULES_PATH.read_text(encoding="utf-8"))
    except OSError:
        return AGENT_RULES_FALLBACK


class MCPOpenFOAMServer:
    """Zero-dependency stdio JSON-RPC 2.0 MCP Server for OpenFOAM."""

    def __init__(self, workspace_dir: str | None = None) -> None:
        self.toolbox = OpenFOAMToolbox(workspace_dir=workspace_dir)
        self.tools: dict[str, dict[str, Any]] = {}
        self.handlers: dict[str, Callable[[dict[str, Any]], Any]] = {}
        self._register_tools()

    def _register_tools(self) -> None:
        """Register the 19 domain tools with JSONSchema definitions."""

        # 1. scaffold_case
        self.tools["scaffold_case"] = {
            "name": "scaffold_case",
            "description": "Scaffold a baseline OpenFOAM v13 case directory from high-level physics parameters.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "case_name": {"type": "string", "description": "Directory name for the new case (e.g. 'cavity_test')"},
                    "case_type": {"type": "string", "default": "incompressibleFluid", "description": "OpenFOAM solver family (e.g. 'incompressibleFluid', 'fluid', 'incompressibleVoF')"},
                    "regime": {"type": "string", "enum": ["laminar", "turbulent"], "default": "turbulent"},
                    "flow_type": {"type": "string", "enum": ["incompressible", "compressible"], "default": "incompressible"},
                    "solver_mode": {"type": "string", "enum": ["transient", "steadyState"], "default": "transient"},
                    "box_size": {"type": "array", "items": {"type": "number"}, "description": "3D dimensions [Lx, Ly, Lz] in meters, e.g. [0.1, 0.1, 0.01]"},
                    "cells": {"type": "array", "items": {"type": "integer"}, "description": "Mesh resolution [Nx, Ny, Nz], e.g. [20, 20, 1]"},
                    "nu": {"type": "number", "default": 1e-5, "description": "Kinematic viscosity [m^2/s]"},
                    "p_ambient": {"type": "number", "default": 100000.0, "description": "Operating/ambient pressure [Pa]"},
                    "temperature": {"type": "number", "default": 300.0, "description": "Reference temperature [K]"},
                    "patches": {"type": "array", "description": "List of boundary patches with role ('inlet', 'outlet', 'wall', 'movingWall', 'empty', 'symmetry'), velocity, etc.", "items": {"type": "object"}},
                    "target_dir": {"type": "string", "description": "Optional parent directory for the case."},
                    "overwrite": {"type": "boolean", "default": False, "description": "Replace an existing non-empty case directory."},
                },
                "required": ["case_name"],
            },
        }
        self.handlers["scaffold_case"] = lambda args: self.toolbox.scaffold_case(**args)

        # 2. validate_case_docker
        self.tools["validate_case_docker"] = {
            "name": "validate_case_docker",
            "description": "Run the full OpenFOAM v13 Docker validation ladder (blockMesh -> checkMesh -> foamRun) on a case directory.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "case_dir": {"type": "string", "description": "Path to the OpenFOAM case directory."},
                    "stage": {"type": "string", "enum": ["blockMesh", "checkMesh", "all"], "default": "all", "description": "Validation stage to run up to."},
                    "timeout_seconds": {"type": "integer", "default": 600, "description": "Timeout in seconds."},
                    "nprocs": {"type": "integer", "default": 1, "description": "Number of MPI parallel ranks (default 1 for serial)."},
                },
                "required": ["case_dir"],
            },
        }
        self.handlers["validate_case_docker"] = lambda args: self.toolbox.validate_case_docker(**args)

        # 3. run_preflight_audit
        self.tools["run_preflight_audit"] = {
            "name": "run_preflight_audit",
            "description": "Run fast Level A static checks to inspect case files, syntax, and boundary conditions without running Docker.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "case_dir": {"type": "string", "description": "Path to the OpenFOAM case directory."}
                },
                "required": ["case_dir"],
            },
        }
        self.handlers["run_preflight_audit"] = lambda args: self.toolbox.run_preflight_audit(**args)

        # 4. get_mesh_quality_metrics
        self.tools["get_mesh_quality_metrics"] = {
            "name": "get_mesh_quality_metrics",
            "description": "Run checkMesh in Docker (Level B) and return quantitative mesh quality metrics (non-orthogonality max/avg, max skewness, aspect ratios, cell count).",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "case_dir": {"type": "string", "description": "Path to the OpenFOAM case directory."}
                },
                "required": ["case_dir"],
            },
        }
        self.handlers["get_mesh_quality_metrics"] = lambda args: self.toolbox.get_mesh_quality_metrics(**args)

        # 5. run_bounded_dry_run
        self.tools["run_bounded_dry_run"] = {
            "name": "run_bounded_dry_run",
            "description": "Execute Level C bounded solver dry-run in Docker (default 5 steps) to analyze initial residual slopes and predict numerical divergence or NaN explosions before committing to a full run.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "case_dir": {"type": "string", "description": "Path to the OpenFOAM case directory."},
                    "dry_run_steps": {"type": "integer", "default": 5, "description": "Number of initial solver time steps to test."},
                },
                "required": ["case_dir"],
            },
        }
        self.handlers["run_bounded_dry_run"] = lambda args: self.toolbox.run_bounded_dry_run(**args)

        # 6. import_external_mesh
        self.tools["import_external_mesh"] = {
            "name": "import_external_mesh",
            "description": "Import an externally provided polyMesh directory into constant/polyMesh and parse its boundary patch names/types.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "case_dir": {"type": "string", "description": "Path to the target case directory."},
                    "source_mesh_dir": {"type": "string", "description": "Path to directory containing polyMesh files."},
                },
                "required": ["case_dir", "source_mesh_dir"],
            },
        }
        self.handlers["import_external_mesh"] = lambda args: self.toolbox.import_external_mesh(**args)

        # 7. search_openfoam_knowledge
        self.tools["search_openfoam_knowledge"] = {
            "name": "search_openfoam_knowledge",
            "description": "Perform multi-step chunkless graph RAG over OpenFOAM v13 knowledge: infers semantic case family, resolves solver, walks tutorial case graph, and returns grounded structural references.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Physics query, e.g. 'supersonic vortex tube' or 'transient buoyant convection'."},
                    "top_k": {"type": "integer", "default": 3, "description": "Number of tutorial cases to retrieve."},
                    "solver": {"type": "string", "description": "Optional solver override to focus the case walk."},
                    "flow_type": {"type": "string", "description": "Optional flow type filter ('incompressible' or 'compressible')."},
                },
                "required": ["query"],
            },
        }
        self.handlers["search_openfoam_knowledge"] = lambda args: self.toolbox.search_openfoam_knowledge(**args)

        # 8. get_tutorial_numerics
        self.tools["get_tutorial_numerics"] = {
            "name": "get_tutorial_numerics",
            "description": "Fetch real v13 tutorial numerical discretization presets (fvSchemes and fvSolution) for a solver.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "solver_name": {"type": "string", "description": "OpenFOAM solver name (e.g. 'foamRun', 'incompressibleFluid', 'fluid')."},
                    "flow_type": {"type": "string", "default": "incompressible", "description": "Flow type ('incompressible' or 'compressible')."},
                },
                "required": ["solver_name"],
            },
        }
        self.handlers["get_tutorial_numerics"] = lambda args: self.toolbox.get_tutorial_numerics(**args)

        # 8. check_physical_envelope
        self.tools["check_physical_envelope"] = {
            "name": "check_physical_envelope",
            "description": "Verify nu, ambient pressure, temperature, or surface tension against realistic fluid bounds.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "species": {"type": "string", "description": "Fluid name, e.g. 'air', 'water', 'oil'."},
                    "property_name": {"type": "string", "enum": ["nu", "p_ambient", "temperature", "surface_tension"], "description": "Implemented property to verify."},
                    "value": {"type": "number", "description": "Value to check."},
                },
                "required": ["species", "property_name", "value"],
            },
        }
        self.handlers["check_physical_envelope"] = lambda args: self.toolbox.check_physical_envelope(**args)

        # 9. inspect_mesh
        self.tools["inspect_mesh"] = {
            "name": "inspect_mesh",
            "description": "Inspect mesh status, patch names, and boundary definitions in an OpenFOAM case directory.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "case_dir": {"type": "string", "description": "Path to the OpenFOAM case directory."}
                },
                "required": ["case_dir"],
            },
        }
        self.handlers["inspect_mesh"] = lambda args: self.toolbox.inspect_mesh(**args)

        # 10. read_case_file
        self.tools["read_case_file"] = {
            "name": "read_case_file",
            "description": "Read the exact text contents of an OpenFOAM dictionary or field file (e.g. 'system/controlDict', '0/U').",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "case_dir": {"type": "string", "description": "Path to the OpenFOAM case directory."},
                    "file_path": {"type": "string", "description": "Case-relative file path (e.g. 'system/controlDict', '0/p', 'constant/momentumTransport')."},
                },
                "required": ["case_dir", "file_path"],
            },
        }
        self.handlers["read_case_file"] = lambda args: self.toolbox.read_case_file(**args)

        # 11. write_case_file
        self.tools["write_case_file"] = {
            "name": "write_case_file",
            "description": "Write or overwrite an OpenFOAM dictionary or field file with new text content.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "case_dir": {"type": "string", "description": "Path to the OpenFOAM case directory."},
                    "file_path": {"type": "string", "description": "Case-relative file path (e.g. 'system/fvSchemes', '0/U')."},
                    "content": {"type": "string", "description": "Complete text content to write."},
                },
                "required": ["case_dir", "file_path", "content"],
            },
        }
        self.handlers["write_case_file"] = lambda args: self.toolbox.write_case_file(**args)

        # 12. clean_case_directory
        self.tools["clean_case_directory"] = {
            "name": "clean_case_directory",
            "description": "Clean an OpenFOAM case directory by safely removing previous time directories, processor folders, and logs before a new run.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "case_dir": {"type": "string", "description": "Path to the OpenFOAM case directory."},
                    "dry_run": {"type": "boolean", "default": False, "description": "List items without deleting them."}
                },
                "required": ["case_dir"],
            },
        }
        self.handlers["clean_case_directory"] = lambda args: self.toolbox.clean_case_directory(**args)

        # 13. calculate_cfl_timestep
        self.tools["calculate_cfl_timestep"] = {
            "name": "calculate_cfl_timestep",
            "description": "Calculate mathematically stable time-step (deltaT) and controlDict settings based on Courant number (CFL) and cell resolution.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "box_size": {"type": "array", "items": {"type": "number"}, "description": "3D box size [Lx, Ly, Lz] in meters."},
                    "cells": {"type": "array", "items": {"type": "integer"}, "description": "Mesh resolution [Nx, Ny, Nz]."},
                    "max_velocity": {"type": "number", "description": "Peak expected fluid velocity [m/s]."},
                    "target_cfl": {"type": "number", "default": 0.5, "description": "Target Courant number (default 0.5)."},
                },
                "required": ["box_size", "cells", "max_velocity"],
            },
        }
        self.handlers["calculate_cfl_timestep"] = lambda args: self.toolbox.calculate_cfl_timestep(**args)

        # 14. run_openfoam_command
        self.tools["run_openfoam_command"] = {
            "name": "run_openfoam_command",
            "description": "Execute any native OpenFOAM utility (e.g. setFields, renumberMesh, transformPoints, foamToVTK) inside Docker.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "case_dir": {"type": "string", "description": "Path to the OpenFOAM case directory."},
                    "command": {"type": "string", "description": "OpenFOAM utility name (e.g. 'setFields', 'renumberMesh', 'foamToVTK')."},
                    "args": {"type": "array", "items": {"type": "string"}, "description": "Optional CLI arguments list."},
                    "timeout_seconds": {"type": "integer", "default": 300, "description": "Timeout in seconds."},
                },
                "required": ["case_dir", "command"],
            },
        }
        self.handlers["run_openfoam_command"] = lambda args: self.toolbox.run_openfoam_command(**args)

        # 15. get_case_status
        self.tools["get_case_status"] = {
            "name": "get_case_status",
            "description": "Inspect current execution state, latest computed time step, and existing files in an OpenFOAM case directory.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "case_dir": {"type": "string", "description": "Path to the OpenFOAM case directory."}
                },
                "required": ["case_dir"],
            },
        }
        self.handlers["get_case_status"] = lambda args: self.toolbox.get_case_status(**args)

        # 16. read_postprocessing_data
        self.tools["read_postprocessing_data"] = {
            "name": "read_postprocessing_data",
            "description": "Read and parse OpenFOAM postProcessing tabular data (forces, residuals, probes) into structured JSON arrays.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "case_dir": {"type": "string", "description": "Path to the OpenFOAM case directory."},
                    "function_name": {"type": "string", "description": "Optional subfolder under postProcessing/ (e.g. 'forces', 'residuals')."},
                },
                "required": ["case_dir"],
            },
        }
        self.handlers["read_postprocessing_data"] = lambda args: self.toolbox.read_postprocessing_data(**args)

        # 17. diagnose_case_failure
        self.tools["diagnose_case_failure"] = {
            "name": "diagnose_case_failure",
            "description": "Analyze OpenFOAM fatal error logs and return expert root-cause diagnosis and actionable fix steps.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "error_log": {"type": "string", "description": "Error log text snippet from OpenFOAM or Docker."}
                },
                "required": ["error_log"],
            },
        }
        self.handlers["diagnose_case_failure"] = lambda args: self.toolbox.diagnose_case_failure(**args)

        # 18. list_case_types
        self.tools["list_case_types"] = {
            "name": "list_case_types",
            "description": "List all 18 supported OpenFOAM solver families and their primary fields.",
            "inputSchema": {
                "type": "object",
                "properties": {},
            },
        }
        self.handlers["list_case_types"] = lambda args: self.toolbox.list_case_types()

    def handle_request(self, request: dict[str, Any]) -> dict[str, Any] | None:
        """Handle an incoming JSON-RPC 2.0 request."""
        if request.get("jsonrpc") != "2.0" or not isinstance(request.get("method"), str):
            return {
                "jsonrpc": "2.0",
                "id": request.get("id"),
                "error": {"code": -32600, "message": "Invalid JSON-RPC 2.0 request."},
            }
        req_id = request.get("id")
        method = request.get("method")
        params = request.get("params", {})

        if req_id is None:
            return None

        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {}, "prompts": {}},
                    "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                },
            }

        elif method == "tools/list":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"tools": list(self.tools.values())},
            }

        elif method == "tools/call":
            tool_name = params.get("name")
            arguments = params.get("arguments", {})

            if tool_name not in self.handlers:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32601, "message": f"Tool '{tool_name}' not found."},
                }

            try:
                handler = self.handlers[tool_name]
                result = handler(arguments)
                text_output = json.dumps(result, indent=2, ensure_ascii=False)
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": text_output}],
                        "isError": not result.get("success", True) if isinstance(result, dict) else False,
                    },
                }
            except (TypeError, ValueError, OSError, RuntimeError) as e:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": f"Error executing tool '{tool_name}': {e!s}"}],
                        "isError": True,
                    },
                }

        elif method == "prompts/list":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "prompts": [
                        {
                            "name": "openfoam_cfd_expert",
                            "description": "System instructions and rules for OpenFOAM v13 CFD case generation and debugging (shared with the Pi 'openfoam' skill).",
                        }
                    ]
                },
            }

        elif method == "prompts/get":
            prompt_content = load_agent_rules()
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "description": "OpenFOAM v13 domain guidance and tools reference.",
                    "messages": [
                        {
                            "role": "user",
                            "content": {"type": "text", "text": prompt_content},
                        }
                    ],
                },
            }

        elif method == "ping":
            return {"jsonrpc": "2.0", "id": req_id, "result": {}}

        else:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"Method '{method}' not recognized."},
            }

    def run_stdio(self) -> None:
        """Run the stdio message loop."""
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                request = json.loads(line)
                response = self.handle_request(request)
                if response is not None:
                    sys.stdout.write(json.dumps(response) + "\n")
                    sys.stdout.flush()
            except (json.JSONDecodeError, TypeError, ValueError) as e:
                err_resp = {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": f"Parse error: {e}"},
                }
                sys.stdout.write(json.dumps(err_resp) + "\n")
                sys.stdout.flush()


def _parse_workspace_dir(argv: list[str]) -> str | None:
    """Extract --workspace-dir <path> (or --workspace-dir=<path>) from argv."""
    for index, arg in enumerate(argv):
        if arg == "--workspace-dir":
            if index + 1 >= len(argv):
                raise SystemExit("--workspace-dir requires a path argument")
            return argv[index + 1]
        if arg.startswith("--workspace-dir="):
            return arg.split("=", 1)[1]
    return None


def main() -> None:
    workspace_dir = _parse_workspace_dir(sys.argv[1:])
    if workspace_dir is not None:
        Path(workspace_dir).expanduser().mkdir(parents=True, exist_ok=True)

    if "--list-tools" in sys.argv:
        server = MCPOpenFOAMServer(workspace_dir=workspace_dir)
        print(f"Registered {len(server.tools)} OpenFOAM tools:")
        for name, t in server.tools.items():
            print(f" - {name}: {t['description']}")
        return

    if "--self-test" in sys.argv:
        server = MCPOpenFOAMServer(workspace_dir=workspace_dir)
        print("Running MCP self-test...")
        init_res = server.handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        assert init_res and "result" in init_res, "Initialize failed"
        tools_res = server.handle_request({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        assert tools_res and len(tools_res["result"]["tools"]) >= 12, "tools/list failed"
        call_res = server.handle_request({
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "search_openfoam_knowledge", "arguments": {"query": "cavity"}},
        })
        assert call_res and "result" in call_res, "tools/call failed"
        print(f"MCP Server self-test PASSED ({len(server.tools)} tools registered; knowledge call verified).")
        return

    server = MCPOpenFOAMServer(workspace_dir=workspace_dir)
    server.run_stdio()


if __name__ == "__main__":
    main()
