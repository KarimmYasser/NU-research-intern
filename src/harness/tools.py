"""OpenFOAM Tool implementations for AI Agents (Pi, Cline, Claude Code, etc.).

This module provides Python functions that wrap the entire OpenFOAM engine:
- Case scaffolding and dictionary rendering
- Full validation ladder (blockMesh -> checkMesh -> foamRun)
- Level A static preflight audit
- Level B quantitative mesh metrics
- Level C bounded dry-run with divergence/NaN prediction
- External mesh importing and parsing
- Physical envelope clamping
- RAG knowledge retrieval
- Case cleaning and dictionary file I/O
"""

from __future__ import annotations

import math
import os
import re
import shutil
from pathlib import Path
from typing import Any

from ir.case_types import CASE_TYPES
from ir.intent import CaseIntent, PatchIntent, intent_to_spec
from knowledge.envelopes import EnvelopeConstraint
from knowledge.rag import KnowledgeRetriever
from mesh.mesh_io import copy_polymesh, load_external_mesh
from preflight.audit import audit_case
from preflight.dry_run import bounded_run_predict
from preflight.metrics import parse_checkmesh_metrics
from rendering.renderer import render
from validation.snapshot import list_snapshots, snapshot_run
from validation.validation_agent import ValidationAgent


class OpenFOAMToolbox:
    """Consolidated, complete OpenFOAM toolbox for AI coding agents."""

    def __init__(self, workspace_dir: Path | str | None = None) -> None:
        self.workspace_dir = Path(workspace_dir or os.getcwd()).resolve()
        self.rag = KnowledgeRetriever()
        self.envelope_constraint = EnvelopeConstraint()
        self._val_agent = ValidationAgent()

    def _workspace_path(self, path: Path | str, label: str = "path") -> Path:
        resolved = Path(path).resolve()
        if not resolved.is_relative_to(self.workspace_dir):
            raise ValueError(f"{label} must stay inside workspace_dir ({self.workspace_dir})")
        return resolved

    @staticmethod
    def _case_file(case_dir: Path, file_path: str) -> Path:
        relative = Path(file_path)
        if relative.is_absolute():
            raise ValueError("file_path must be relative to case_dir")
        target = (case_dir / relative).resolve()
        if not target.is_relative_to(case_dir):
            raise ValueError("file_path must stay inside case_dir")
        return target

    @staticmethod
    def _looks_like_case(case_dir: Path) -> bool:
        return (
            (case_dir / "0").is_dir()
            and (case_dir / "constant").is_dir()
            and (case_dir / "system" / "controlDict").is_file()
        )

    # --------------------------------------------------------------------------
    # 1. Knowledge & Reference Tools
    # --------------------------------------------------------------------------

    def list_case_types(self) -> list[dict[str, Any]]:
        """List all 18 supported OpenFOAM solver families and their properties."""
        return [
            {
                "case_type": name,
                "solver": defn.solver,
                "flow_type": defn.flow_type,
                "needs_gravity": defn.needs_gravity,
                "phases": list(defn.phases),
                "species": list(defn.species),
            }
            for name, defn in CASE_TYPES.items()
        ]

    def search_openfoam_knowledge(self, query: str, top_k: int = 3, solver: str | None = None, flow_type: str | None = None) -> dict[str, Any]:
        """Perform multi-step chunkless graph RAG over OpenFOAM v13 knowledge.

        Traverses the knowledge graph in 4 distinct steps:
        1. Semantic case family inference from physics description
        2. Solver module resolution
        3. Tutorial case graph walk (file tree, required fields, patch boundary types)
        4. Structured grounding & reference snippets
        """
        graph = self.rag.retrieve_case_graph(query, top_cases=top_k, solver=solver, flow_type=flow_type)
        flat = self.rag.search(query, top_k=top_k)

        return {
            "query": query,
            "recommended_solver": graph.get("solver"),
            "matched_families": graph.get("families", []),
            "reference_cases": graph.get("cases", []),
            "template_contract": graph.get("template_contract", {}),
            "boundary_conditions": graph.get("boundary_conditions", []) or flat.get("boundary_conditions", []),
            "grounding_summary": graph.get("ground", ""),
            "all_matched_solvers": flat.get("solvers", []),
            "thermophysics": flat.get("thermophysics", []),
        }

    def get_tutorial_numerics(self, solver_name: str, flow_type: str = "incompressible") -> dict[str, Any]:
        """Retrieve real OpenFOAM v13 tutorial numerical presets (fvSchemes and fvSolution) for a solver."""
        numerics = self.rag.get_tutorial_numerics(solver_name=solver_name, flow_type=flow_type)
        return {
            "solver": solver_name,
            "flow_type": flow_type,
            "has_schemes": bool(numerics.get("fvSchemes")),
            "has_solution": bool(numerics.get("fvSolution")),
            "snippets": numerics,
        }

    def check_physical_envelope(self, species: str, property_name: str, value: float) -> dict[str, Any]:
        """Check an implemented scalar property against its physical envelope."""
        supported = {"nu", "p_ambient", "temperature", "surface_tension"}
        if property_name not in supported:
            return {
                "success": False,
                "error": f"Unsupported property '{property_name}'. Supported properties: {sorted(supported)}",
            }
        if not math.isfinite(value):
            return {"success": False, "error": "value must be finite."}
        mock_intent = CaseIntent(
            fluid_species=species,
            nu=value if property_name == "nu" else 1e-5,
            p_ambient=value if property_name == "p_ambient" else 100000.0,
            temperature=value if property_name == "temperature" else 300.0,
            surface_tension=value if property_name == "surface_tension" else None,
        )
        constrained, notes, errors = self.envelope_constraint.apply(mock_intent)
        in_bounds = len(notes) == 0 and len(errors) == 0
        return {
            "species": species,
            "property": property_name,
            "requested_value": value,
            "constrained_value": getattr(constrained, property_name),
            "success": not errors,
            "in_bounds": in_bounds,
            "notes": notes,
            "errors": [e.message for e in errors],
            "status": "VALID" if in_bounds else ("CLAMPED" if notes and not errors else "REJECTED"),
        }

    # --------------------------------------------------------------------------
    # 2. Case Generation & Mesh Setup
    # --------------------------------------------------------------------------

    def scaffold_case(
        self,
        case_name: str,
        case_type: str = "incompressibleFluid",
        regime: str = "turbulent",
        flow_type: str = "incompressible",
        solver_mode: str = "transient",
        box_size: list[float] | None = None,
        cells: list[int] | None = None,
        nu: float = 1e-5,
        p_ambient: float = 100000.0,
        temperature: float = 300.0,
        patches: list[dict[str, Any]] | None = None,
        target_dir: str | None = None,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        """Scaffold a baseline OpenFOAM v13 case directory."""
        try:
            b_size = tuple(box_size or [0.1, 0.1, 0.01])
            c_res = tuple(cells or [20, 20, 1])

            patch_intents = []
            if patches:
                for p in patches:
                    vel = tuple(p["velocity"]) if p.get("velocity") else None
                    patch_intents.append(
                        PatchIntent(
                            name=p.get("name", "wall"),
                            role=p.get("role", "wall"),
                            velocity=vel,
                            pressure=p.get("pressure"),
                            temperature=p.get("temperature"),
                            faces=p.get("faces"),
                            bc_overrides=p.get("bc_overrides"),
                        )
                    )
            else:
                patch_intents = [
                    PatchIntent(name="movingWall", role="movingWall", velocity=(1.0, 0.0, 0.0)),
                    PatchIntent(name="fixedWalls", role="wall"),
                    PatchIntent(name="frontAndBack", role="empty"),
                ]

            intent = CaseIntent(
                case_type=case_type,
                regime=regime,  # type: ignore
                flow_type=flow_type,  # type: ignore
                solver_mode=solver_mode,  # type: ignore
                box_size=b_size,  # type: ignore
                cells=c_res,  # type: ignore
                nu=nu,
                p_ambient=p_ambient,
                temperature=temperature,
                patches=patch_intents,
            )

            spec = intent_to_spec(intent)
            case_files = render(spec)

            if not case_name or Path(case_name).name != case_name or case_name in {".", ".."}:
                raise ValueError("case_name must be one non-empty directory name")
            out_base = self._workspace_path(target_dir or self.workspace_dir, "target_dir")
            out_case = (out_base / case_name).resolve()
            if out_case.exists() and any(out_case.iterdir()) and not overwrite:
                raise FileExistsError(f"Target is not empty: {out_case}; pass overwrite=true to replace it")
            if overwrite and out_case.exists():
                shutil.rmtree(out_case)
            out_case.mkdir(parents=True, exist_ok=True)
            case_files.write(out_case)

            all_files = (
                [f"0/{k}" for k in case_files.zero]
                + [f"constant/{k}" for k in case_files.constant]
                + [f"system/{k}" for k in case_files.system]
                + list(case_files.extra.keys())
            )

            return {
                "success": True,
                "case_dir": str(out_case),
                "rendered_files": all_files,
                "message": f"Successfully scaffolded OpenFOAM v13 case at {out_case}",
            }
        except (KeyError, TypeError, ValueError, OSError) as e:
            return {
                "success": False,
                "error": str(e),
                "message": f"Failed to scaffold case: {e}",
            }

    def import_external_mesh(self, case_dir: str, source_mesh_dir: str) -> dict[str, Any]:
        """Copy an externally-provided polyMesh directory into constant/polyMesh and parse its boundaries."""
        c_path = Path(case_dir).resolve()
        s_path = Path(source_mesh_dir).resolve()

        if not s_path.is_dir():
            return {"success": False, "error": f"Source mesh directory does not exist: {source_mesh_dir}"}
        if not c_path.is_dir():
            return {"success": False, "error": f"Target case directory does not exist: {case_dir}"}

        try:
            copy_polymesh(s_path, c_path)
            patches = load_external_mesh(s_path)

            return {
                "success": True,
                "case_dir": str(c_path),
                "patches": [{"name": p.name, "type": p.type, "n_faces": p.n_faces} for p in patches],
                "message": f"Successfully imported external mesh and found {len(patches)} patches.",
            }
        except (ValueError, OSError) as e:
            return {"success": False, "error": str(e)}

    # --------------------------------------------------------------------------
    # 3. Preflight & Quality Audits
    # --------------------------------------------------------------------------

    def run_preflight_audit(self, case_dir: str) -> dict[str, Any]:
        """Run fast Level A static checks without running Docker."""
        c_path = Path(case_dir).resolve()
        if not c_path.is_dir():
            return {"success": False, "error": f"Directory not found: {case_dir}"}

        findings = audit_case(c_path)
        fails = [f for f in findings if f.level == "fail"]
        warns = [f for f in findings if f.level == "warn"]

        return {
            "success": len(fails) == 0,
            "case_dir": str(c_path),
            "verdict": "FAIL" if fails else ("WARN" if warns else "PASS"),
            "failing_checks": [{"code": f.code, "message": f.message} for f in fails],
            "warnings": [{"code": f.code, "message": f.message} for f in warns],
            "total_findings": len(findings),
        }

    def get_mesh_quality_metrics(self, case_dir: str) -> dict[str, Any]:
        """Run checkMesh in Docker (Level B) and return quantitative mesh metrics (non-orthogonality, skewness, aspect ratio)."""
        c_path = Path(case_dir).resolve()
        if not c_path.is_dir():
            return {"success": False, "error": f"Directory not found: {case_dir}"}

        res = self._val_agent.run_check_mesh(c_path)
        metrics = parse_checkmesh_metrics(res.log)

        return {
            "success": res.ok and metrics.ok,
            "case_dir": str(c_path),
            "cells": metrics.cells,
            "non_orthogonality_max": metrics.non_orthogonality_max,
            "non_orthogonality_avg": metrics.non_orthogonality_avg,
            "max_skewness": metrics.max_skewness,
            "max_aspect_ratio": metrics.max_aspect_ratio,
            "min_volume": metrics.min_volume,
            "failed_checks": metrics.failed_checks,
            "summary": metrics.summarize(),
        }

    def run_bounded_dry_run(self, case_dir: str, dry_run_steps: int = 5) -> dict[str, Any]:
        """Execute Level C bounded solver dry-run in Docker to predict numerical divergence and NaN explosions early."""
        c_path = Path(case_dir).resolve()
        if not c_path.is_dir():
            return {"success": False, "error": f"Directory not found: {case_dir}"}

        # bounded_run_predict wants a callable(case_dir) -> ValidationResult, not the
        # agent itself. _bounded_foamrun caps endTime to steps*deltaT and suppresses
        # writes, restoring controlDict afterwards — same path the graph's preflight uses.
        from preflight.runner import _bounded_foamrun

        def _run(case_path: Path):
            return _bounded_foamrun(case_path, self._val_agent, dry_run_steps)

        pred = bounded_run_predict(c_path, _run, dry_run_steps=dry_run_steps)
        return {
            "success": pred.prediction != "FAIL",
            "prediction": pred.prediction,
            "steps_captured": pred.steps,
            "per_field_trends": pred.trend_line,
            "fields_initial_residuals": pred.fields,
            "message": pred.message or f"Dry-run prediction: {pred.prediction}",
        }

    # --------------------------------------------------------------------------
    # 4. Live Docker Validation Ladder & Execution
    # --------------------------------------------------------------------------

    def validate_case_docker(
        self,
        case_dir: str,
        stage: str = "all",
        timeout_seconds: int = 600,
        nprocs: int = 1,
    ) -> dict[str, Any]:
        """Execute the full OpenFOAM v13 validation ladder (blockMesh -> checkMesh -> foamRun) inside Docker."""
        try:
            c_path = self._workspace_path(case_dir, "case_dir")
        except ValueError as exc:
            return {"success": False, "error": str(exc)}
        if not c_path.is_dir():
            return {"success": False, "error": f"Directory not found: {case_dir}"}

        agent = ValidationAgent(timeout=timeout_seconds, nprocs=nprocs)
        external_mesh = (c_path / "constant" / "polyMesh" / "boundary").is_file()
        skip_mesh_generation = external_mesh and not (c_path / "system" / "blockMeshDict").is_file()

        if stage == "blockMesh":
            res = agent.run_block_mesh(c_path)
        elif stage == "checkMesh":
            if skip_mesh_generation:
                res = agent.run_check_mesh(c_path)
            else:
                b_res = agent.run_block_mesh(c_path)
                res = b_res if not b_res.ok else agent.run_check_mesh(c_path)
        else:
            res = agent.run_ladder(c_path, skip_mesh_generation=skip_mesh_generation)

        log_lines = res.log.splitlines()
        truncated_log = "\n".join(log_lines[-100:]) if len(log_lines) > 100 else res.log

        formatted_errors = [
            {
                "failure_class": err.failure_class.name,
                "file_hint": err.file_hint,
                "line": err.line,
                "message": err.message,
            }
            for err in res.errors
        ]

        payload = {
            "success": res.ok,
            "case_dir": str(c_path),
            "stage_reached": res.stage,
            "errors": formatted_errors,
            "log_snippet": truncated_log,
            "log_total_lines": len(log_lines),
            "message": "Validation passed! Case is ready to run." if res.ok else f"Validation failed at stage '{res.stage}'.",
        }

        # A configuration that ran is the one artefact worth keeping: case dirs are
        # re-rendered in place, so the next attempt destroys it. Archive it here rather
        # than trusting the agent to remember (docs/evidence/rhvt-residuals-2026-08-18.md).
        if res.ok:
            try:
                dest = snapshot_run(c_path, res.log, stage=res.stage or stage, nprocs=nprocs)
                payload["snapshot_dir"] = str(dest)
                payload["message"] += f" Configuration archived to {dest.name} under .runs/."
            except OSError as exc:
                # Never let bookkeeping fail a validation that actually passed.
                payload["snapshot_error"] = f"could not archive run: {exc}"

        return payload

    # --------------------------------------------------------------------------
    # 5. File Inspection, Modification & Cleaning Tools
    # --------------------------------------------------------------------------

    def inspect_mesh(self, case_dir: str) -> dict[str, Any]:
        """Inspect the mesh status, polyMesh boundaries, and blockMeshDict."""
        c_path = Path(case_dir).resolve()
        poly_mesh = c_path / "constant" / "polyMesh"
        boundary_file = poly_mesh / "boundary"
        block_mesh_dict = c_path / "system" / "blockMeshDict"

        info: dict[str, Any] = {
            "case_dir": str(c_path),
            "has_polyMesh": poly_mesh.is_dir(),
            "has_blockMeshDict": block_mesh_dict.is_file(),
            "patches": [],
        }

        if boundary_file.is_file():
            try:
                content = boundary_file.read_text(encoding="utf-8")
                patches = re.findall(r"(\w+)\s*\{\s*type\s+(\w+);", content)
                info["patches"] = [{"name": name, "type": ptype} for name, ptype in patches]
            except OSError as e:
                info["boundary_read_error"] = str(e)

        return info

    def read_case_file(self, case_dir: str, file_path: str) -> dict[str, Any]:
        """Read the exact text contents of an OpenFOAM dictionary or field file."""
        try:
            c_path = self._workspace_path(case_dir, "case_dir")
            target = self._case_file(c_path, file_path)
        except ValueError as exc:
            return {"success": False, "error": str(exc)}
        if not target.is_file():
            return {"success": False, "error": f"File '{file_path}' does not exist in {case_dir}"}
        try:
            content = target.read_text(encoding="utf-8", errors="replace")
            return {"success": True, "file_path": file_path, "content": content, "lines": len(content.splitlines())}
        except OSError as e:
            return {"success": False, "error": str(e)}

    def write_case_file(self, case_dir: str, file_path: str, content: str) -> dict[str, Any]:
        """Write or overwrite an OpenFOAM dictionary or field file."""
        try:
            c_path = self._workspace_path(case_dir, "case_dir")
            target = self._case_file(c_path, file_path)
        except ValueError as exc:
            return {"success": False, "error": str(exc)}
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            return {"success": True, "file_path": file_path, "message": f"Successfully wrote {file_path}"}
        except OSError as e:
            return {"success": False, "error": str(e)}

    def clean_case_directory(self, case_dir: str, dry_run: bool = False) -> dict[str, Any]:
        """Clean an OpenFOAM case directory by removing time directories, processor folders, and postProcessing logs."""
        try:
            c_path = self._workspace_path(case_dir, "case_dir")
        except ValueError as exc:
            return {"success": False, "error": str(exc)}
        if not c_path.is_dir():
            return {"success": False, "error": f"Directory not found: {case_dir}"}
        if not self._looks_like_case(c_path):
            return {"success": False, "error": "Refusing to clean: directory is not a recognizable OpenFOAM case."}

        removed = []
        # Remove time directories (e.g. 0.001, 1, 10) but NEVER '0'
        time_dir_pattern = re.compile(r"^[0-9]+(\.[0-9]+)?$")
        for item in c_path.iterdir():
            if item.is_dir():
                if item.name == "0":
                    continue
                if time_dir_pattern.match(item.name) or item.name.startswith("processor") or item.name == "postProcessing":
                    removed.append(item.name)
                    if not dry_run:
                        shutil.rmtree(item)
            elif item.is_file() and item.name.startswith("log."):
                removed.append(item.name)
                if not dry_run:
                    item.unlink()

        return {
            "success": True,
            "case_dir": str(c_path),
            "dry_run": dry_run,
            "removed_items": removed,
            "message": f"{'Would remove' if dry_run else 'Removed'} {len(removed)} temporary/run items.",
        }

    # --------------------------------------------------------------------------
    # 6. Advanced Simulation Controls & Diagnostics
    # --------------------------------------------------------------------------

    def calculate_cfl_timestep(
        self,
        box_size: list[float],
        cells: list[int],
        max_velocity: float,
        target_cfl: float = 0.5,
    ) -> dict[str, Any]:
        """Calculate mathematically stable time-step (deltaT) and controlDict settings based on Courant number (CFL).

        Args:
            box_size: 3D bounding box [Lx, Ly, Lz] in meters.
            cells: Mesh resolution [Nx, Ny, Nz].
            max_velocity: Peak anticipated fluid velocity [m/s].
            target_cfl: Target maximum Courant number (typically 0.5 - 1.0). Default 0.5.
        """
        if len(box_size) != 3 or len(cells) != 3:
            return {"success": False, "error": "box_size and cells must each have exactly 3 elements [x, y, z]."}
        if not all(math.isfinite(v) for v in [*box_size, max_velocity, target_cfl]):
            return {"success": False, "error": "Numerical inputs must be finite."}
        valid_cells = all(isinstance(n, int) and not isinstance(n, bool) and n > 0 for n in cells)
        if any(length <= 0 for length in box_size) or not valid_cells:
            return {"success": False, "error": "Box dimensions and integer cell counts must be greater than 0."}
        if max_velocity <= 0 or target_cfl <= 0:
            return {"success": False, "error": "max_velocity and target_cfl must be greater than 0."}

        dx = [L / N for L, N in zip(box_size, cells)]
        min_dx = min(dx)
        max_dt = target_cfl * min_dx / max_velocity

        # Format to nice engineering scientific notation
        dt_order = 10 ** math.floor(math.log10(max_dt))
        dt_rounded = round(max_dt / dt_order, 2) * dt_order

        # Domain transit time (time for fluid to pass through Lx)
        transit_time = box_size[0] / max_velocity
        suggested_end_time = round(3.0 * transit_time, 4)  # 3 pass-throughs
        suggested_write_interval = max(dt_rounded, round(suggested_end_time / 20.0, 5))

        return {
            "success": True,
            "min_cell_size_m": min_dx,
            "max_velocity_ms": max_velocity,
            "target_cfl": target_cfl,
            "recommended_deltaT": dt_rounded,
            "domain_transit_time_s": transit_time,
            "suggested_endTime": suggested_end_time,
            "suggested_writeInterval": suggested_write_interval,
            "formula": "deltaT = target_cfl * min_dx / max_velocity",
        }

    def run_openfoam_command(
        self,
        case_dir: str,
        command: str,
        args: list[str] | None = None,
        timeout_seconds: int = 300,
    ) -> dict[str, Any]:
        """Execute any OpenFOAM utility (e.g. setFields, renumberMesh, transformPoints, checkMesh, foamToVTK) inside Docker.

        Args:
            case_dir: Path to the OpenFOAM case directory.
            command: Utility name (e.g. 'setFields', 'renumberMesh', 'foamToVTK').
            args: Optional command line arguments list.
            timeout_seconds: Maximum runtime in seconds.
        """
        c_path = Path(case_dir).resolve()
        if not c_path.is_dir():
            return {"success": False, "error": f"Directory not found: {case_dir}"}

        if timeout_seconds <= 0:
            return {"success": False, "error": "timeout_seconds must be greater than 0."}
        full_cmd = [command] + (args or [])
        agent = ValidationAgent(timeout=timeout_seconds)
        res = agent.run_command(c_path, full_cmd)

        out_lines = (res.stdout + res.stderr).splitlines()
        truncated = "\n".join(out_lines[-60:]) if len(out_lines) > 60 else "\n".join(out_lines)

        return {
            "success": res.returncode == 0,
            "command": " ".join(full_cmd),
            "exit_code": res.returncode,
            "output_snippet": truncated,
            "total_lines": len(out_lines),
        }

    def get_case_status(self, case_dir: str) -> dict[str, Any]:
        """Inspect the current execution state and computed time steps in a case directory.

        Args:
            case_dir: Path to the OpenFOAM case directory.
        """
        c_path = Path(case_dir).resolve()
        if not c_path.is_dir():
            return {"success": False, "error": f"Directory not found: {case_dir}"}

        time_dirs = []
        for p in c_path.iterdir():
            if p.is_dir():
                try:
                    float(p.name)
                    time_dirs.append(p.name)
                except ValueError:
                    pass

        sorted_times = sorted(time_dirs, key=float)
        has_mesh = (c_path / "constant" / "polyMesh" / "boundary").is_file() or (c_path / "system" / "blockMeshDict").is_file()

        # Inspect controlDict for solver name and target endTime
        solver = "unknown"
        end_time = "unknown"
        legacy_solver_entry = False
        cdict = c_path / "system" / "controlDict"
        if cdict.is_file():
            try:
                text = cdict.read_text(encoding="utf-8", errors="replace")
                # v13 selects the solver module with a top-level `solver` entry.
                # `application` is the pre-v12 binary convention; still read it so a
                # legacy case reports something useful, but flag it rather than
                # silently presenting it as valid.
                s_match = re.search(r"^\s*solver\s+(\w+)\s*;", text, re.MULTILINE)
                if s_match:
                    solver = s_match.group(1)
                else:
                    legacy = re.search(r"^\s*application\s+(\w+)\s*;", text, re.MULTILINE)
                    if legacy:
                        solver = legacy.group(1)
                        legacy_solver_entry = True
                e_match = re.search(r"endTime\s+([0-9eE.+-]+);", text)
                if e_match:
                    end_time = e_match.group(1)
            except OSError as exc:
                return {"success": False, "error": str(exc), "case_dir": str(c_path)}

        status = {
            "success": True,
            "case_dir": str(c_path),
            "has_mesh": has_mesh,
            "solver": solver,
            "target_endTime": end_time,
            "computed_time_steps": sorted_times,
            "latest_time": sorted_times[-1] if sorted_times else "0",
            "is_solved": len(sorted_times) > 1,
        }
        if legacy_solver_entry:
            status["warning"] = (
                "controlDict uses a pre-v13 `application` entry; v13 selects the solver "
                "module with a top-level `solver` entry and will not run this case"
            )
        # Surface archived runs so the settings that worked can be found again.
        snapshots = [p.name for p in list_snapshots(c_path)]
        if snapshots:
            status["archived_runs"] = snapshots
            status["archived_runs_dir"] = str(c_path / ".runs")
        return status

    def read_postprocessing_data(self, case_dir: str, function_name: str | None = None) -> dict[str, Any]:
        """Read and parse tabular OpenFOAM postProcessing data (forces, residuals, probes).

        Args:
            case_dir: Path to the OpenFOAM case directory.
            function_name: Specific subfolder under postProcessing (e.g. 'forces', 'residuals', 'probes').
        """
        c_path = Path(case_dir).resolve()
        post_dir = c_path / "postProcessing"
        if not post_dir.is_dir():
            return {"success": False, "error": f"No postProcessing/ directory found in {case_dir}"}

        available = [p.name for p in post_dir.iterdir() if p.is_dir()]
        if not available:
            return {"success": False, "message": "postProcessing/ directory is empty.", "available_functions": []}

        target_func = function_name or available[0]
        func_dir = post_dir / target_func
        if not func_dir.is_dir():
            return {"success": False, "error": f"Function '{target_func}' not found under postProcessing/.", "available_functions": available}

        # Find latest dat file in the function directory
        dat_files = list(func_dir.glob("**/*.dat"))
        if not dat_files:
            return {"success": False, "message": f"No .dat files found under postProcessing/{target_func}", "available_functions": available}

        target_file = dat_files[0]
        try:
            lines = target_file.read_text(encoding="utf-8", errors="replace").splitlines()
            header_lines = [l for l in lines if l.startswith("#")]
            data_lines = [l.strip() for l in lines if l.strip() and not l.startswith("#")]

            columns = []
            if header_lines:
                # Parse column names from the last header line
                last_header = header_lines[-1].lstrip("#").strip()
                columns = [c.strip() for c in re.split(r"\t+|\s{2,}", last_header) if c.strip()]

            # Return the last 20 data rows
            recent_rows = [d.split() for d in data_lines[-20:]]

            return {
                "success": True,
                "function_name": target_func,
                "file_path": str(target_file.relative_to(c_path)),
                "columns": columns,
                "total_rows": len(data_lines),
                "latest_sample": recent_rows[-1] if recent_rows else [],
                "recent_samples": recent_rows,
                "available_functions": available,
            }
        except OSError as e:
            return {"success": False, "error": str(e), "available_functions": available}

    def diagnose_case_failure(self, error_log: str) -> dict[str, Any]:
        """Diagnose OpenFOAM fatal errors and return expert root-cause and remediation steps.

        Args:
            error_log: Full or partial error log text from Docker or OpenFOAM execution.
        """
        text = error_log.lower()

        # 1. Missing keyword in dictionary
        kw_match = re.search(r"keyword\s+([a-zA-Z0-9_]+)\s+is undefined", error_log)
        if kw_match:
            kw = kw_match.group(1)
            return {
                "failure_class": "DICTIONARY_SYNTAX",
                "root_cause": f"Missing required keyword '{kw}' in OpenFOAM dictionary.",
                "recommended_fix": f"Add the keyword '{kw}' and its required value to the corresponding dictionary file.",
                "target_file": "system/fvSolution or system/fvSchemes or constant/physicalProperties",
            }

        # 2. Patch not in mesh
        if "patchnotinmesh" in text or "not in mesh" in text:
            return {
                "failure_class": "MESH_BOUNDARY_MISMATCH",
                "root_cause": "A boundary condition patch name specified in 0/ field files does not exist in constant/polyMesh/boundary.",
                "recommended_fix": "Call 'inspect_mesh' to get the exact list of mesh boundary names, and rename the patch blocks in 0/ files to match.",
                "target_file": "0/* (0/p, 0/U, etc.)",
            }

        # 3. Courant / CFL explosion
        if "courant number" in text or "continuity error" in text or re.search(r"\bnan\b", text):
            return {
                "failure_class": "NUMERICAL_DIVERGENCE",
                "root_cause": "The solver diverged due to Courant number (CFL) violation or aggressive relaxation factors.",
                "recommended_fix": "Call 'calculate_cfl_timestep' to compute a stable deltaT, reduce deltaT in system/controlDict, or increase PIMPLE non-orthogonal corrector loops (nNonOrthogonalCorrectors 2;) in system/fvSolution.",
                "target_file": "system/controlDict & system/fvSolution",
            }

        # 4. Unknown solver / module
        if "cannot find solver" in text or "unknown solver" in text:
            return {
                "failure_class": "SOLVER_MODULE_MISMATCH",
                "root_cause": "The solver specified in system/controlDict does not match OpenFOAM v13 foundation modular solver names.",
                "recommended_fix": "OpenFOAM v13 selects the solver module with a top-level 'solver' entry in system/controlDict and runs the case with the generic 'foamRun' runner. Do NOT add an 'application' entry: that is the pre-v12 convention and no v13 tutorial uses it. Call 'list_case_types' and pick the module matching this case's physics rather than reusing a name from another case.",
                "target_file": "system/controlDict",
            }

        return {
            "failure_class": "UNKNOWN_ERROR",
            "root_cause": "Unrecognized OpenFOAM runtime error.",
            "recommended_fix": "Inspect the localized lines in the error log snippet and verify dictionary syntax brackets and semicolons.",
            "target_file": "case directory",
        }
