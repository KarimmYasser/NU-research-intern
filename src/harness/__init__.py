"""OpenFOAM Agent Harness package.

Provides tools, protocols (MCP), and environment management for AI coding agents
(Pi, Cline, Claude Code, etc.) to generate, inspect, and validate OpenFOAM CFD cases.
"""

from .tools import OpenFOAMToolbox

__all__ = ["OpenFOAMToolbox"]
