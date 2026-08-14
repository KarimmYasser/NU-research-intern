"""Level A preflight: static dictionary audit of a rendered OpenFOAM case.

Runs entirely on the files the renderer produced (no Docker). Checks are chosen
from the concrete failure classes the pipeline has hit (see
docs/progress/progress-log.md), so each finding maps to a known root cause:

  - missing `rho` relaxation factor in compressible transient fvSolution
    (root cause of the RHVT *first-solve* NaN)
  - missing energy-field solver entry for the compressible `fluid` family
    ("keyword h is undefined" DICTIONARY_SYNTAX failure)
  - missing PIMPLE/SIMPLE loop block
  - non-positive internalFields for solved scalars (T, k, epsilon, p)
  - 0/ field files missing patches that exist in the mesh boundary
    (patches rendered inconsistently across fields -> patchNotInMesh read error)
  - ddtSchemes default `none` or missing (solver has no time term)
  - compressible case without a positive initial p / T
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .ofdict import boundary_fields, entries, finite_value, section

LEVEL_RANK = {"pass": 0, "warn": 1, "fail": 2}


@dataclass(frozen=True)
class AuditFinding:
    code: str  # short machine-readable id, e.g. "missing_rho_relaxation"
    level: str  # "pass" | "warn" | "fail"
    message: str  # specific, human-readable, with evidence

    def as_dict(self) -> dict:
        return {"code": self.code, "level": self.level, "message": self.message}


def _finding(fail_words: list[str], warn_words: list[str], pass_words: list[str]) -> str:
    return " ".join(s for s in fail_words or warn_words or pass_words).strip()


def audit_case(case_dir: Path) -> list[AuditFinding]:
    """Run the Level A battery. Returns findings in check order."""
    findings: list[AuditFinding] = []
    system = case_dir / "system"
    zero = case_dir / "0"
    constant = case_dir / "constant"

    control = _read_dir(system / "controlDict")
    fvschemes = _read_dir(system / "fvSchemes")
    fvsolution = _read_dir(system / "fvSolution")

    solver = (entries(control).get("solver") if control else None) or "?"
    # v13 compressible families carry the thermo dict inside constant/physicalProperties
    # (thermoType block) — the old OpenFOAM-9 thermophysicalProperties name is legacy.
    phys = _read_dir(constant / "physicalProperties")
    compressible = "fluid" in solver or "thermoType" in phys

    # 1. controlDict: solver + basic time integrity
    cd = entries(control) if control else {}
    if not control:
        findings.append(AuditFinding("missing_controlDict", "fail",
                                     "system/controlDict missing or unreadable"))
    else:
        findings.append(AuditFinding("solver_entry", "pass",
                                     f"controlDict solver = '{solver}'"))
        _check_time(cd, findings)

    # 2. fvSchemes: ddtSchemes must have a real default term.
    ddt_default = None
    if not fvschemes:
        findings.append(AuditFinding("missing_fvSchemes", "fail",
                                     "system/fvSchemes missing or unreadable"))
    else:
        ddt = section(fvschemes, "ddtSchemes")
        if ddt is None:
            findings.append(AuditFinding("missing_ddtSchemes", "fail",
                                         "fvSchemes has no ddtSchemes block"))
        else:
            ddt_default = entries(ddt).get("default")
            if not ddt_default or ddt_default == "none":
                findings.append(AuditFinding("ddt_default_none", "fail",
                                             f"ddtSchemes.default = '{ddt_default or 'MISSING'}' "
                                             f"— solver has no time-marching term"))
            else:
                findings.append(AuditFinding("ddt_default", "pass",
                                             f"ddtSchemes.default = '{ddt_default}'"))

    # 3. fvSolution: loop block + relaxation + compressible energy solver entry.
    if not fvsolution:
        findings.append(AuditFinding("missing_fvSolution", "fail",
                                     "system/fvSolution missing or unreadable"))
    else:
        _check_fvsolution(fvsolution, compressible, ddt_default, findings)

    # 3.5 Courant number check for transient solvers
    if control:
        _check_courant_number(cd, zero, ddt_default, findings)

    # 4. 0/ fields: positivity for solved scalars.
    _check_internal_fields(zero, compressible, findings)

    # 5. Patch coverage: every 0/ field's boundaryField must list every mesh patch.
    _check_patch_coverage(zero, constant / "polyMesh" / "boundary", findings,
                          blockmesh_dict=system / "blockMeshDict")

    # 6. Compressible sanity: thermophysicalProperties present with positive rho.
    if compressible:
        _check_thermo_properties(constant, findings)

    return findings


def _read_dir(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _check_time(cd: dict[str, str], findings: list[AuditFinding]) -> None:
    start = finite_value(cd.get("startTime"))
    end = finite_value(cd.get("endTime"))
    dt = finite_value(cd.get("deltaT"))
    if end is not None and start is not None and end <= start:
        findings.append(AuditFinding("endTime_le_start", "fail",
                                     f"endTime {end} <= startTime {start} — nothing would run"))
    else:
        findings.append(AuditFinding("time_range", "pass",
                                     f"startTime={start} endTime={end} deltaT={dt}"))
    if dt is not None and dt <= 0:
        findings.append(AuditFinding("nonpositive_deltaT", "fail",
                                     f"deltaT = {dt} — non-positive timestep"))


def _check_courant_number(cd: dict[str, str], zero: Path, ddt_default: str | None, findings: list[AuditFinding]) -> None:
    """Estimate Courant number Co = V_max * deltaT / h_est for transient solver modes."""
    import math
    if ddt_default in ("none", "steadyState", None):
        return
    dt = finite_value(cd.get("deltaT"))
    if dt is None or dt <= 0:
        return
    u_file = zero / "U"
    if not u_file.is_file():
        return
    u_text = _read_dir(u_file)
    vectors = re.findall(r"uniform\s*\(\s*([-\d.eE+]+)\s+([-\d.eE+]+)\s+([-\d.eE+]+)\s*\)", u_text)
    if not vectors:
        return
    mags = [math.sqrt(float(x)**2 + float(y)**2 + float(z)**2) for x, y, z in vectors]
    v_max = max(mags) if mags else 0.0
    if v_max <= 0:
        return
    h_est = 0.01
    co = (v_max * dt) / h_est
    if co > 10.0 or (v_max > 10.0 and dt >= 0.1):
        findings.append(AuditFinding(
            "high_courant_number", "fail",
            f"Estimated Courant number Co = {co:.1f} (V_max = {v_max:.1f} m/s, deltaT = {dt} s, h = {h_est} m) "
            f"exceeds transient stability limit (Co > 10). Set deltaT <= 1e-4s for high-speed transient flows."
        ))


def _check_fvsolution(fv: str, compressible: bool, ddt_default: str | None,
                      findings: list[AuditFinding]) -> None:
    solvers = section(fv, "solvers")
    if solvers is None:
        findings.append(AuditFinding("missing_solvers_block", "fail",
                                     "fvSolution has no solvers block"))
    else:
        findings.append(AuditFinding("solvers_block", "pass", "fvSolution.solvers present"))
        _check_solver_entries(solvers, findings)

    loop = None
    for name in ("PIMPLE", "SIMPLE", "PISO"):
        if section(fv, name) is not None:
            loop = name
            break
    if loop is None:
        findings.append(AuditFinding("missing_loop_block", "fail",
                                     "fvSolution has no PIMPLE/SIMPLE/PISO block — "
                                     "the solution algorithm is undefined"))
    else:
        # The v13 solver *modules* (incompressibleFluid / fluid) look up their
        # control dict by algorithm name: PIMPLE in transient mode, SIMPLE in
        # steady mode (pimpleSingleRegionControl selects on
        # mesh().schemes().steady()). A loop block that contradicts the ddt
        # scheme fails at startup with "keyword PIMPLE is undefined in
        # dictionary fvSolution" (seen live: a transient Euler cavity whose
        # LLM override had emitted a SIMPLE-only fvSolution).
        expected = "SIMPLE" if ddt_default == "steadyState" else "PIMPLE"
        if loop == expected:
            findings.append(AuditFinding("loop_block", "pass",
                                         f"fvSolution.{loop} present (matches {ddt_default or '?'} ddt)"))
        elif expected == "PIMPLE" and loop == "PISO":
            # PISO is the transient incompressible alternative — accepted.
            findings.append(AuditFinding("loop_block", "pass",
                                         f"fvSolution.{loop} present (transient PISO)"))
        else:
            findings.append(AuditFinding("loop_block_mismatch", "fail",
                                         f"fvSolution uses {loop} but ddtSchemes.default='{ddt_default}' "
                                         f"needs {expected} — the v13 solver module reads {expected} "
                                         "and fails with 'keyword ... is undefined'"))

    relax = section(fv, "relaxationFactors")
    if relax is None:
        findings.append(AuditFinding("missing_relaxationFactors", "warn",
                                     "fvSolution has no relaxationFactors block"))
    elif not relax.strip():
        findings.append(AuditFinding("missing_relaxationFactors", "warn",
                                     "fvSolution.relaxationFactors is empty"))
    else:
        findings.append(AuditFinding("relaxationFactors", "pass",
                                     "relaxationFactors present"))

    # The ρ-relaxation finding: for compressible transient flow the *field*
    # rho relaxation is what prevents the very first solve from NaN-ing.
    if compressible:
        rho_relax = False
        if relax is not None:
            fld = section(relax, "fields")
            if fld is not None:
                rho_relax = "rho" in entries(fld) or "rho.*" in entries(fld)
        if not rho_relax and ddt_default != "steadyState":
            findings.append(AuditFinding("missing_rho_relaxation", "fail",
                                         "compressible transient: relaxationFactors.fields has "
                                         "no `rho` entry — the root cause of the RHVT first-solve "
                                         "NaN (supervisor recipe: rho 0.05)"))
        else:
            findings.append(AuditFinding("rho_relaxation", "pass",
                                         "compressible rho field relaxation present or steady-state"))

    # Compressible `fluid` family: energy-field solver entry required. The
    # renderer always emits `(U|h|e|k|epsilon|R)`; a stray override may drop it.
    if compressible and solvers is not None:
        from .ofdict import keys as _keys
        solver_names = set(_keys(solvers))
        # A solver key covers the energy field if it IS h/e/he/hEqn, or if it's
        # a parenthesized alternation regex mentioning an energy field.
        has_energy = bool(solver_names & {"h", "e", "he", "hEqn"}) or any(
            "(" in name and re.search(r"[he]", name) for name in solver_names
        )
        if not has_energy:
            findings.append(AuditFinding("missing_energy_solver", "fail",
                                         'compressible Fluid: solvers block has no energy-field '
                                         'entry (h/e/he or a "(U|h|e|...)" regex) — '
                                         'this is the "keyword h is undefined" DICTIONARY_SYNTAX root cause'))
        else:
            findings.append(AuditFinding("energy_solver", "pass",
                                         "compressible energy-field solver entry present"))


def _check_solver_entries(solvers: str, findings: list[AuditFinding]) -> None:
    """Every linear-solver entry must carry the keys its solver type requires.

    GAMG needs `smoother`; PCG/PBiCGStab/PBiCG need `preconditioner`; smoothSolver
    needs `smoother`. A missing key is a DICTIONARY_SYNTAX error caught live in the
    Docker ladder (`keyword smoother is undefined in dictionary solvers/h`).
    """
    from .ofdict import entries as _entries
    from .ofdict import sub_blocks
    bad: list[str] = []
    for name, body in sub_blocks(solvers):
        e = _entries(body)
        kind = e.get("solver", "").strip()
        need = {"GAMG": "smoother",
                "PCG": "preconditioner",
                "PBiCG": "preconditioner",
                "PBiCGStab": "preconditioner",
                "smoothSolver": "smoother"}.get(kind)
        if need and not e.get(need):
            bad.append(f"{name} ({kind})")
    if bad:
        findings.append(AuditFinding("incomplete_solver_entries", "fail",
                                     f"solvers entries missing required keys: "
                                     f"{', '.join(bad)}"))
    else:
        findings.append(AuditFinding("solver_entries", "pass",
                                     "solver entries carry their required keys"))


def _check_internal_fields(zero: Path, compressible: bool,
                           findings: list[AuditFinding]) -> None:
    if not zero.is_dir():
        findings.append(AuditFinding("missing_0_dir", "fail", "0/ directory missing"))
        return
    from .ofdict import strip_comments
    # Solved scalar fields whose internalField must be strictly positive for the
    # solver to start (and stay bounded): p=0 is only fine for incompressible.
    # `nut` and `alphat` are *derived* fields (computed from k/epsilon via wall
    # functions), and the v13 tutorials legitimately start them at `uniform 0`
    # (e.g. fluid/cavity/0/nut), so they must NOT be positivity-flagged.
    _POSITIVE_SOLVED = {"p", "T", "k", "epsilon", "omega", "h", "e", "nuTilda"}
    for field_file in sorted(zero.glob("*")):
        if not field_file.is_file():
            continue
        name = field_file.name
        text = strip_comments(_read_dir(field_file))
        raw = _top_level_internal_field(text)
        val = finite_value(raw or "")
        if name not in _POSITIVE_SOLVED:
            continue
        if name == "p" and not compressible:
            continue  # gauge pressure: any finite value is fine
        if val is None:
            findings.append(AuditFinding(f"internalField_{name}", "warn",
                                         f"0/{name}: internalField value not parseable "
                                         f"({raw!r})"))
            continue
        if val <= 0:
            reason = ("negative/zero pressure would invert density" if name == "p"
                      else "must be strictly positive for the solver to start")
            findings.append(AuditFinding(f"nonpositive_{name}", "fail",
                                         f"0/{name}: internalField = {val} (≤ 0) — {reason}"))
        else:
            findings.append(AuditFinding(f"internalField_{name}", "pass",
                                         f"0/{name}: internalField = {val} (> 0)"))


def _top_level_internal_field(body: str) -> str | None:
    """internalField is a top-level `internalField uniform N;` scalar entry."""
    import re as _re
    for m in _re.finditer(r'(?:^|\n)\s*internalField\s*', body):
        start = m.end()
        end = body.find(";", start)
        if end == -1:
            return None
        val = body[start:end].strip()
        if val and "{" not in val:
            return val
    return None


def _check_patch_coverage(zero: Path, boundary: Path,
                          findings: list[AuditFinding], blockmesh_dict: Path | None = None) -> None:
    """Every patch in the mesh boundary file must appear in every 0/ field.

    The renderer builds each field's boundaryField from the *intent's* patch
    list; if a patch got dropped from one field's BC map, OpenFOAM throws
    "patch ... not found in boundary of mesh" at read time. Compare across 0/.
    """
    mesh_patches: set[str] = set()
    if boundary.is_file():
        mesh_patches = set(_mesh_patch_names(boundary))
    elif blockmesh_dict is not None and blockmesh_dict.is_file():
        mesh_patches = set(_blockmesh_patch_names(blockmesh_dict))
    if not mesh_patches and not zero.is_dir():
        return
    field_patch_sets: dict[str, set[str]] = {}
    for field_file in sorted(zero.glob("*")):
        if not field_file.is_file() or field_file.name.startswith("."):
            continue
        bf = boundary_fields(_read_dir(field_file))
        if not bf:
            continue
        field_patch_sets[field_file.name] = set(bf.keys())

    if not field_patch_sets:
        return

    # Mesh-side patches must all be covered by every field.
    if mesh_patches:
        for fname, names in field_patch_sets.items():
            missing = sorted(mesh_patches - names)
            if missing:
                findings.append(AuditFinding("patch_not_in_field", "fail",
                                             f"0/{fname}: mesh patches missing from "
                                             f"boundaryField: {missing}"))
            else:
                findings.append(AuditFinding(f"patch_cover_{fname}", "pass",
                                             f"0/{fname}: all {len(mesh_patches)} mesh patches covered"))

    # Cross-field consistency: every field must agree on the patch set (a patch
    # declared on U but missing on p breaks at file-read, not at solve).
    base = next(iter(field_patch_sets.values()))
    for fname, names in list(field_patch_sets.items())[1:]:
        if names != base:
            only_in = sorted(names - base)
            missing_from = sorted(base - names)
            findings.append(AuditFinding("patch_set_mismatch", "warn",
                                         f"0/{fname}: patch set differs from other fields "
                                         f"(extra={only_in}, missing_relative={missing_from})"))


def _mesh_patch_names(boundary_path: Path) -> list[str]:
    text = boundary_path.read_text(encoding="utf-8", errors="replace")
    names: list[str] = []
    import re as _re
    # boundary file: N ( name { type ...; nFaces ...; startFace ...; } ... )
    for m in _re.finditer(r'^\s*([A-Za-z][A-Za-z0-9_]*)\s*\{', text, _re.MULTILINE):
        # skip the header FoamFile block
        names.append(m.group(1))
    # drop the first "FoamFile" if present
    names = [n for n in names if n != "FoamFile"]
    # dedupe preserving order
    seen: set[str] = set()
    out: list[str] = []
    for n in names:
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out


def _blockmesh_patch_names(blockmesh_path: Path) -> list[str]:
    text = blockmesh_path.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"boundary\s*\(\s*(.*?)\s*\)\s*;", text, re.DOTALL)
    if not m:
        return []
    bnd = m.group(1)
    names: list[str] = []
    for pm in re.finditer(r'^\s*([A-Za-z][A-Za-z0-9_]*)\s*\{', bnd, re.MULTILINE):
        n = pm.group(1)
        if n != "FoamFile":
            names.append(n)
    return list(dict.fromkeys(names))


def _check_thermo_properties(constant: Path, findings: list[AuditFinding]) -> None:
    tp = constant / "physicalProperties"
    if not tp.is_file():
        findings.append(AuditFinding("missing_thermo", "fail",
                                     "compressible: constant/physicalProperties missing"))
        return
    text = tp.read_text(encoding="utf-8", errors="replace")
    # v13 compressible physicalProperties has a thermoType block; the old
    # thermophysicalProperties file name is the OpenFOAM-9 convention.
    if "thermoType" not in text:
        findings.append(AuditFinding("thermo_unrecognized", "warn",
                                     "physicalProperties has no thermoType block "
                                     "(v13 compressible expects one)"))
        return
    mw = finite_value(next((v for k, v in entries(section(text, "mixture") or "").items()
                            if "molWeight" in k or "molweight" in k), None))
    cp = finite_value(next((v for k, v in entries(section(text, "thermo") or "").items()
                            if k in ("Cp", "cp")), None))
    fails: list[str] = []
    if mw is not None and mw <= 0:
        fails.append(f"molWeight {mw} ≤ 0")
    if cp is not None and cp <= 0:
        fails.append(f"Cp {cp} ≤ 0")
    if fails:
        findings.append(AuditFinding("thermo_nonpositive", "fail", "compressible thermo: " + "; ".join(fails)))
    else:
        findings.append(AuditFinding("thermo_sane", "pass",
                                     "compressible thermo file present and sane"
                                     + (f" (Mw={mw}, Cp={cp})" if mw is not None or cp is not None else "")))