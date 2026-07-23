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

import os
import re
import subprocess
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path

DEFAULT_IMAGE = os.environ.get("FOAM_DOCKER_IMAGE", "microfluidica/openfoam:13")
DEFAULT_TIMEOUT = 600  # seconds; foamRun can be slow, but our test cases are small


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
    def __init__(self, image: str = DEFAULT_IMAGE, timeout: int = DEFAULT_TIMEOUT):
        self.image = image
        self.timeout = timeout

    # --- ladder rungs -----------------------------------------------------

    def run_block_mesh(self, case_dir: Path) -> ValidationResult:
        cp = self._docker_run(case_dir, ["blockMesh"])
        ok = cp.returncode == 0
        errs = [] if ok else self.parse_errors(cp.stdout + cp.stderr, FailureClass.MESH_GENERATION)
        return ValidationResult(ok, "blockMesh", errs, cp.stdout + cp.stderr)

    def run_check_mesh(self, case_dir: Path) -> ValidationResult:
        cp = self._docker_run(case_dir, ["checkMesh"])
        out = cp.stdout + cp.stderr
        # checkMesh often exits 0 even on quality failures, so inspect the output:
        # it prints "***" lines and a "Failed N mesh checks." summary on failure.
        failed = cp.returncode != 0 or "***" in out or re.search(r"Failed \d+ mesh checks", out)
        if not failed:
            return ValidationResult(True, "checkMesh", [], out)
        return ValidationResult(False, "checkMesh",
                                self.parse_errors(out, FailureClass.MESH_QUALITY), out)

    def run_solver(self, case_dir: Path) -> ValidationResult:
        cp = self._docker_run(case_dir, ["foamRun"])
        ok = cp.returncode == 0
        errs = [] if ok else self.parse_errors(cp.stdout + cp.stderr, FailureClass.SOLVER_RUNTIME)
        return ValidationResult(ok, "foamRun", errs, cp.stdout + cp.stderr)

    def run_ladder(self, case_dir: Path) -> ValidationResult:
        """Run rungs in order; return the first failure, or the final success."""
        for rung in (self.run_block_mesh, self.run_check_mesh, self.run_solver):
            result = rung(case_dir)
            if not result.ok:
                return result
        return result

    # --- docker plumbing --------------------------------------------------

    def _docker_run(self, case_dir: Path, args: list[str]) -> subprocess.CompletedProcess:
        host = case_dir.resolve().as_posix()  # e.g. D:/.../case -> Docker Desktop maps it
        cmd = ["docker", "run", "--rm", "-v", f"{host}:/case", "-w", "/case", self.image, *args]
        try:
            return subprocess.run(cmd, capture_output=True, text=True, timeout=self.timeout)
        except FileNotFoundError:
            return _fake_cp("docker not found on PATH - install Docker Desktop")
        except subprocess.TimeoutExpired:
            return _fake_cp(f"{' '.join(args)} timed out after {self.timeout}s")

    # --- error parsing ----------------------------------------------------

    def parse_errors(self, raw_output: str, default_class: FailureClass) -> list[ValidationError]:
        """Localize + classify an OpenFOAM failure from its output.

        Extracts the `--> FOAM FATAL [IO ]ERROR` block; IO errors name the
        offending file + line, which we surface (that specificity is what the
        FLARE finding says matters). Falls back to the tail of the output.
        """
        text = raw_output.strip()
        if not text:
            return []
        if "docker not found" in text or "timed out after" in text:
            return [ValidationError(FailureClass.ENVIRONMENT, None, text)]

        m = re.search(r"-->\s*FOAM FATAL (IO )?ERROR\s*:?(.*?)(?:\n\s*\n|FOAM exiting|$)",
                      text, re.S)
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
        return [ValidationError(cls, file_hint, message, line)]


def _fake_cp(msg: str) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr=msg)
