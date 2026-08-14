"""Syntax Validation Agent: executes the validation ladder against a rendered
case and turns failures into localized, classified errors for the retry loop.

The ladder (docs/phase-ii-architecture.md §2.2) runs real OpenFOAM v13 inside a
Docker container - `microfluidica/openfoam:13` - so no VM or local OpenFOAM
install is needed (the official Foundation images stop at v11; this community
image provides v13, which shares v11's case conventions). Rungs:
  2a. blockMesh   - does the mesh generate?
  2b. checkMesh   - is the mesh quality acceptable?
  3.  foamRun     - does the case actually run (first timesteps)?

parse_errors() localizes the offending file/line where OpenFOAM's FATAL IO
ERROR names it, per the FLARE finding (error specificity > retry mechanism):
temp/paper-research-agentic-cfd-llm/pdfs/2606.03852_FLARE.pdf.
"""

from __future__ import annotations

import hashlib
import os
import queue
import re
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import ClassVar

DEFAULT_IMAGE = os.environ.get("FOAM_DOCKER_IMAGE", "microfluidica/openfoam:13")
DEFAULT_TIMEOUT = int(os.environ.get("FOAM_VALIDATION_TIMEOUT", "600"))  # seconds; foamRun can be slow on large meshes — raise via env if time permits
# Decompose-and-run the solver across N ranks (mpirun inside the container).
# Default 1 = serial foamRun (unchanged behavior). Set e.g. FOAM_VALIDATION_PROCS=8
# on a multi-core host to cut runtime on large meshes.
DEFAULT_PROCS = int(os.environ.get("FOAM_VALIDATION_PROCS", "1"))


class FailureClass(Enum):
    # blockMesh refused to build a mesh - vertex ordering / block geometry.
    # Route back to the Meshing Agent's geometry intent.
    MESH_GENERATION = auto()
    # blockMesh succeeded but checkMesh flagged quality (non-orthogonality,
    # skewness). Route to Meshing - different fix (grading/cell count).
    MESH_QUALITY = auto()
    # Dictionary parse / IO error - a malformed or inconsistent dict file.
    # OpenFOAM usually names the file+line; route to whoever owns that file.
    DICTIONARY_SYNTAX = auto()
    # The case parsed and meshed but the solver failed at runtime (divergence,
    # missing field, incompatible BC). Route to the Physics Agent's intent.
    SOLVER_RUNTIME = auto()
    # Docker/OpenFOAM environment itself unavailable - not a case error.
    ENVIRONMENT = auto()
    UNKNOWN = auto()


@dataclass
class ValidationError:
    failure_class: FailureClass
    file_hint: str | None  # e.g. "system/blockMeshDict" / "0/p" when identifiable
    message: str  # the localized, specific error text - not full raw output
    line: int | None = None


@dataclass
class ValidationResult:
    ok: bool
    stage: str  # "blockMesh" / "checkMesh" / "foamRun"
    errors: list[ValidationError] = field(default_factory=list)
    log: str = ""  # full captured output, for logging / trajectory data


class ValidationAgent:
    _CHECKED_MESHES: ClassVar[set[str]] = set()

    def __init__(self, image: str = DEFAULT_IMAGE, timeout: float = DEFAULT_TIMEOUT,
                 nprocs: int = DEFAULT_PROCS, check_mesh_all: bool = False):
        self.image = image
        self.timeout = timeout
        self.nprocs = max(1, nprocs)
        self.check_mesh_all = check_mesh_all or bool(os.environ.get("FOAM_CHECKMESH_ALL", "").strip())

    @classmethod
    def clear_mesh_cache(cls) -> None:
        """Clear cached checkMesh pass keys."""
        cls._CHECKED_MESHES.clear()

    def _get_mesh_key(self, case_dir: Path) -> str | None:
        boundary = case_dir / "constant" / "polyMesh" / "boundary"
        if not boundary.is_file():
            boundary = case_dir / "boundary"
        if boundary.is_file():
            try:
                content = boundary.read_bytes()
                content_hash = hashlib.md5(content).hexdigest()
                return f"{boundary.name}:{len(content)}:{content_hash}"
            except OSError:
                return str(boundary.resolve())
        return None

    # --- ladder rungs -----------------------------------------------------

    def run_block_mesh(self, case_dir: Path) -> ValidationResult:
        cp = self._docker_run(case_dir, ["blockMesh"])
        ok = cp.returncode == 0
        errs = [] if ok else self.parse_errors(cp.stdout + cp.stderr, FailureClass.MESH_GENERATION)
        return ValidationResult(ok, "blockMesh", errs, cp.stdout + cp.stderr)

    def run_check_mesh(self, case_dir: Path, force: bool = False,
                       all_checks: bool | None = None) -> ValidationResult:
        key = self._get_mesh_key(case_dir)
        if not force and key and key in self._CHECKED_MESHES:
            return ValidationResult(True, "checkMesh", [], "checkMesh skipped (cached pass for static mesh)")

        # Run checkMesh. Extra topology/geometry flags are optional to avoid high overhead on large meshes.
        cmd = ["checkMesh"]
        use_all = self.check_mesh_all if all_checks is None else all_checks
        if use_all:
            cmd.extend(["-allTopology", "-allGeometry", "-meshQuality"])

        cp = self._docker_run(case_dir, cmd)
        out = cp.stdout + cp.stderr
        # checkMesh often exits 0 even on quality failures, so inspect the output:
        # it prints "***" lines and a "Failed N mesh checks." summary on failure.
        failed = cp.returncode != 0 or "***" in out or re.search(r"Failed \d+ mesh checks", out)
        if not failed:
            if key:
                self._CHECKED_MESHES.add(key)
            return ValidationResult(True, "checkMesh", [], out)
        return ValidationResult(False, "checkMesh",
                                self.parse_errors(out, FailureClass.MESH_QUALITY), out)

    def read_dictionary_entry(self, case_dir: Path, dict_path: str, entry: str) -> str | None:
        """Query an OpenFOAM dictionary entry value using foamDictionary inside the container."""
        cp = self._docker_run(case_dir, ["foamDictionary", dict_path, "-entry", entry, "-value"])
        if cp.returncode == 0 and cp.stdout.strip():
            return cp.stdout.strip()
        return None

    def list_times(self, case_dir: Path) -> list[str]:
        """Query recorded time directories using foamListTimes or inspecting case directories."""
        cp = self._docker_run(case_dir, ["foamListTimes"])
        if cp.returncode == 0 and cp.stdout.strip():
            return [line.strip() for line in cp.stdout.splitlines() if line.strip()]
        # Fallback: scan disk for numeric time directories
        times: list[str] = []
        if case_dir.is_dir():
            for child in case_dir.iterdir():
                if child.is_dir():
                    try:
                        float(child.name)
                        times.append(child.name)
                    except ValueError:
                        continue
        return sorted(times, key=lambda x: float(x) if x.replace(".", "", 1).isdigit() else 0.0)

    def run_solver(self, case_dir: Path) -> ValidationResult:
        if self.nprocs > 1:
            return self._run_solver_parallel(case_dir)
        cp = self._docker_run(case_dir, ["foamRun"], stop_on_nan=True)
        ok = cp.returncode == 0
        errs = [] if ok else self.parse_errors(cp.stdout + cp.stderr, FailureClass.SOLVER_RUNTIME)
        return ValidationResult(ok, "foamRun", errs, cp.stdout + cp.stderr)

    def _run_solver_parallel(self, case_dir: Path) -> ValidationResult:
        """Decompose the mesh and run `foamRun -parallel` across N ranks.

        Mirrors the serial rung but with a `decomposePar -> mpirun foamRun`
        pipeline. Writes a scotch decomposeParDict if none exists so the case
        is self-contained. The decomposition is written to processorN/ dirs
        inside the case; we do not reconstruct afterward (validation only needs
        proof the solver ran, and reconstruction is wasted time here).
        """
        ddict = case_dir / "system" / "decomposeParDict"
        # Always (re)write the dict to match this run's nprocs - a stale dict
        # from a previous attempt (or the renderer) would decompose into a
        # different number of subdomains than mpirun launches and the run fails
        # with "number of processor directories != number of processors".
        # decomposePar -force below re-creates the processor*/ dirs, and mpirun
        # only reads what it needs, so leaving stale sibling dirs is harmless.
        banner = (
            "/*--------------------------------*- C++ -*----------------------------------*\\\n"
            "  =========                 |\n"
            "  \\\\      /  F ield         | OpenFOAM: The Open Source CFD Toolbox\n"
            "   \\\\    /   O peration     | Website:  https://openfoam.org\n"
            "    \\\\  /    A nd           | Version:  13\n"
            "     \\\\/     M anipulation  |\n"
            "\\*---------------------------------------------------------------------------*/\n"
        )
        ddict.write_text(
            banner
            + "FoamFile\n{\n"
            "    format      ascii;\n"
            "    class       dictionary;\n"
            "    location    \"system\";\n"
            "    object      decomposeParDict;\n"
            "}\n"
            "// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //\n\n"
            f"numberOfSubdomains  {self.nprocs};\n\n"
            "method              scotch;\n\n"
            "// generated by the validation ladder for parallel foamRun\n"
        )

        dec = self._docker_run(case_dir, ["decomposePar", "-force"])
        if dec.returncode != 0:
            errs = self.parse_errors(dec.stdout + dec.stderr, FailureClass.SOLVER_RUNTIME)
            return ValidationResult(False, "decomposePar", errs, dec.stdout + dec.stderr)

        cp = self._docker_run(case_dir, ["mpirun", "--allow-run-as-root", "--oversubscribe",
                                         "-np", str(self.nprocs), "foamRun", "-parallel"],
                              stop_on_nan=True)
        ok = cp.returncode == 0
        errs = [] if ok else self.parse_errors(cp.stdout + cp.stderr, FailureClass.SOLVER_RUNTIME)
        return ValidationResult(ok, "foamRun(-parallel)", errs, cp.stdout + cp.stderr)

    def run_ladder(self, case_dir: Path, skip_mesh_generation: bool = False) -> ValidationResult:
        """Run rungs in order; return the first failure, or the final success.

        `skip_mesh_generation=True` is for externally-provided meshes (R1, no
        mesh generation in this repo): there is no blockMeshDict to run, the
        mesh is already at constant/polyMesh/, so the ladder starts at
        checkMesh instead of blockMesh.
        """
        rungs = (
            (self.run_check_mesh, self.run_solver) if skip_mesh_generation
            else (self.run_block_mesh, self.run_check_mesh, self.run_solver)
        )
        result: ValidationResult = ValidationResult(True, "none")
        for rung in rungs:
            result = rung(case_dir)
            if not result.ok:
                return result
        return result

    # --- docker plumbing --------------------------------------------------

    # A diverged solver prints these forever. The *first* NaN residual already
    # proves non-convergence — keep going just wastes CPU on a failed case, so
    # abort the container immediately and (per _find_nan_lines) the failure
    # classifies SOLVER_RUNTIME, routing to build_intent for a physics fix.
    _NAN_RE = re.compile(r"(?:residual|sum local|sum global|cumulative)\s*=\s*-?(?:nan|inf)\b",
                          re.IGNORECASE)

    def _docker_run(self, case_dir: Path, args: list[str],
                    stop_on_nan: bool = False) -> subprocess.CompletedProcess:
        host = case_dir.resolve().as_posix()  # e.g. D:/.../case -> Docker Desktop maps it
        cname = f"foam_run_{uuid.uuid4().hex[:8]}"
        cmd = ["docker", "run", "--name", cname, "--rm", "--init", "-v", f"{host}:/case", "-w", "/case", self.image, *args]

        def _cleanup_container(p: subprocess.Popen | None) -> None:
            if p is not None:
                try:
                    p.kill()
                except OSError:
                    pass
            try:
                subprocess.run(["docker", "rm", "-f", cname],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
            except Exception:
                pass

        proc = None
        reason: str | None = None  # "timeout" | "nan" -> overrides returncode
        try:
            total_text: list[str] = []
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, bufsize=1)
            exit_code: int | None = None
            line_buffer: list[str] = []
            start = time.monotonic()

            if proc.stdout is not None:
                q: queue.Queue[str | None] = queue.Queue()

                def reader():
                    try:
                        for line in iter(proc.stdout.readline, ""):
                            q.put(line)
                    except Exception:
                        pass
                    finally:
                        q.put(None)

                reader_thread = threading.Thread(target=reader, daemon=True)
                reader_thread.start()

                while True:
                    elapsed = time.monotonic() - start
                    if elapsed > self.timeout:
                        reason = "timeout"
                        break
                    try:
                        timeout_step = min(0.5, max(0.05, self.timeout - elapsed))
                        line = q.get(timeout=timeout_step)
                    except queue.Empty:
                        continue
                    if line is None:
                        break  # EOF: process finished writing
                    line_buffer.append(line)
                    if stop_on_nan and self._NAN_RE.search(line):
                        reason = "nan"
                        break

            if reason in ("nan", "timeout"):
                _cleanup_container(proc)

            tail: str = ""
            try:
                communicate_timeout = max(0.1, self.timeout - (time.monotonic() - start)) if reason is None else 1.0
                tail = proc.communicate(timeout=communicate_timeout)[0] or ""
                exit_code = proc.returncode
            except (subprocess.TimeoutExpired, Exception):
                _cleanup_container(proc)
                try:
                    tail, _ = proc.communicate(timeout=1.0)
                except Exception:
                    tail = ""
                exit_code = None
                if reason is None:
                    reason = "timeout"

            total_text.extend(line_buffer)
            if tail:
                total_text.append(tail)
            joined = "".join(total_text)

            if reason == "timeout":
                msg = (f"{' '.join(args)} timed out after {self.timeout}s"
                       + (f"\n--- partial output (last 4000 chars) ---\n{joined[-4000:]}" if joined else ""))
                return _fake_cp(msg)
            if reason == "nan":
                nan_lines = self._find_nan_lines(joined)
                msg = (f"{' '.join(args)} diverged to NaN - aborted on first non-finite residual\n"
                       + (nan_lines if nan_lines else joined[-1000:]))
                return _fake_cp(msg)
            return subprocess.CompletedProcess(cmd, exit_code or 0, joined, "")
        except FileNotFoundError:
            return _fake_cp("docker not found on PATH - install Docker Desktop")

    # --- error parsing ----------------------------------------------------

    def _find_nan_lines(self, text: str) -> str:
        """Return the lines around a NaN residual, or '' if none found.

        OpenFOAM logs divergence as e.g.
            GAMG:  Solving for p, Initial residual = nan, Final residual = nan
            time step continuity errors : sum local = nan, global = nan
        The solver keeps printing these forever instead of crashing, so they
        are the *cause* when the run later times out.
        """
        hits = [ln.strip() for ln in text.splitlines()
                if re.search(r"=\s*-?nan\b|residual = -?nan|local = -?nan", ln, re.IGNORECASE)]
        if not hits:
            return ""
        return "\n".join(hits[:12])

    def parse_errors(self, raw_output: str, default_class: FailureClass) -> list[ValidationError]:
        """Localize + classify an OpenFOAM failure from its output.

        Extracts the `--> FOAM FATAL [IO ]ERROR` block; IO errors name the
        offending file + line, which we surface (that specificity is what the
        FLARE finding says matters). Falls back to the tail of the output.
        """
        text = raw_output.strip()
        if not text:
            return []
        if "docker not found" in text:
            return [ValidationError(FailureClass.ENVIRONMENT, None, text)]

        # A runtime crash (SIGFPE/SIGSEGV/...) prints a signal stack trace
        # instead of a "FOAM FATAL ERROR" block, so the regex below never
        # matches and the fallback would otherwise surface the banner (the
        # start of `text`) instead of the actual failure. Report the lines
        # just before the crash (e.g. "Courant Number ... Time = ...", which
        # is usually the actual clue - a divergence) plus the top stack frame.
        sm = re.search(r"^#0\s+Foam::error::printStack", text, re.MULTILINE)
        if sm:
            pre_crash = [ln for ln in text[:sm.start()].rstrip().splitlines() if ln.strip()][-6:]
            top_frame = text[sm.start():].splitlines()[0]
            message = "\n".join([*pre_crash, top_frame])
            return [ValidationError(default_class, None, message)]

        m = re.search(r"-->\s*FOAM FATAL (IO )?ERROR\s*:?(.*?)(?:\n\s*\n|FOAM exiting|$)",
                      text, re.DOTALL)
        is_io = bool(m and m.group(1))
        block = (m.group(2) if m else text).strip()

        file_hint, line = None, None
        # OpenFOAM names the offending file a few ways, e.g.
        #   file: /case/0/p at line 21.
        #   keyword vertices is undefined in dictionary "/case/system/blockMeshDict"
        #   ... in file "/case/system/controlDict" at line 18
        # Scan the whole error text (the file/line detail often follows the
        # message, after a blank line, so it's outside `block`).
        fm = re.search(r'(?:in dictionary|in file|file:?)\s+"?([^"\s]+)"?', text)
        if fm:
            path = fm.group(1)
            file_hint = path.split("case/")[-1].lstrip("/") if "case/" in path else path
        lm = re.search(r"at line\s+(\d+)", text)
        if lm:
            line = int(lm.group(1))

        cls = FailureClass.DICTIONARY_SYNTAX if is_io else default_class
        # Keep the message tight - the first few lines carry the actual reason.
        message = "\n".join(block.splitlines()[:6]).strip() or text[-500:]
        if m:
            return [ValidationError(cls, file_hint, message, line)]

        # A diverged solver (NaN residuals) usually does NOT exit - it burns CPU
        # printing NaN solves until the wall-clock timeout. So the timeout text
        # arrives together with the divergence evidence; the divergence is the
        # real failure (route to the Physics intent), not an infra error.
        nan_lines = self._find_nan_lines(text)
        if nan_lines:
            return [ValidationError(FailureClass.SOLVER_RUNTIME, None,
                                    "solver diverged to NaN (no convergence):\n" + nan_lines)]
        if "timed out after" in text:
            return [ValidationError(FailureClass.ENVIRONMENT, None, text)]

        return [ValidationError(default_class, file_hint, message, line)]


def _fake_cp(msg: str) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr=msg)
