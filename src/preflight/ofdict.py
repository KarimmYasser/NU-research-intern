"""Tolerant OpenFOAM dictionary helpers for static case auditing.

We deliberately do NOT build a full OpenFOAM dictionary parser — the audit only
needs a few stable, well-formed top-level sections (controlDict, fvSchemes,
fvSolution, boundaryField) from files the renderer produced. The helpers below
answer two questions tolerantly, even if an LLM patch reformats a file:

  1. give `{...}` section content by top-level name
  2. give `key value` entries and per-patch BC entries inside a section

All functions are deliberately lenient: on malformed input they return the
closest partial answer (or empty) rather than raising.
"""

from __future__ import annotations

import re
from typing import Iterator

# Matches the start of a top-level entry: optional whitespace, then a
# name (word or quoted), then `{`-block, `;`-terminated value, or sub-dict.
# Not line-anchored: an LLM override can compress the whole file onto one line
# (`... } solvers { ... }`), so the name may appear mid-line. The negative
# lookbehind stops matches inside words/quoted values.
_SECTION_RE = re.compile(
    r'(?<![\w"\'])\s*(?P<name>"[^"]+"|[A-Za-z][A-Za-z0-9_]*)\s*(?=\{|;|$)', re.M
)

_HEADER_RE = re.compile(r"^\s*FoamFile\s*\{", re.M)
_SEP_RE = re.compile(r"^/\*\*|\*\*/|^//[ */]+\$", re.M)


def strip_comments(text: str) -> str:
    """Remove `// line` and `/* block */` comments that survive the renderer."""
    text = _SEP_RE.sub("", text)
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"//[^\n]*", "", text)


def section(text: str, name: str) -> str | None:
    """Return the body of the top-level `name { ... }` block, or None.

    Tolerant of comments and the FoamFile header. Nested braces are tracked so
    the returned body is exactly the block's inner region.
    """
    text = strip_comments(text)
    text = _HEADER_RE.sub("", text, count=1)  # drop the FoamFile header block
    for m in _SECTION_RE.finditer(text):
        if m.group("name").strip('"') != name:
            continue
        pos = m.end()
        while pos < len(text) and text[pos] not in "{};":
            pos += 1
        if pos >= len(text) or text[pos] != "{":
            # A `name value;` entry or missing block — no dict content.
            return ""
        depth = 1
        i = pos + 1
        while depth and i < len(text):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
            i += 1
        return text[pos + 1 : i - 1]
    return None


def entries(text: str | None) -> dict[str, str]:
    """Parse `key value;` pairs out of a section body (scalar-only entries).

    Nested `{...}` sub-blocks are skipped (their content is not scalar). Returns
    key -> joined value. Last-write-wins for duplicate keys.
    """
    out: dict[str, str] = {}
    if not text:
        return out
    body = strip_comments(text)
    # Each entry: name (word or quoted) then tokens until ';' or '{'. Matches
    # mid-line too (one-line LLM overrides).
    for m in re.finditer(r'(?<![\w"\'])\s*(?P<name>"[^"]+"|[A-Za-z][A-Za-z0-9_]*)\s*', body):
        start = m.end()
        if start >= len(body) or body[start] == "{":
            continue  # sub-dict (or trailing "name {" without body)
        end = body.find(";", start)
        if end == -1:
            continue
        val = body[start:end].strip()
        if val and not val.endswith("}") and "{" not in val:
            out[m.group("name").strip('"')] = val
    return out


def sub_blocks(text: str | None) -> list[tuple[str, str]]:
    """Top-level `name { ... }` sub-blocks of a section body, in order.

    Returns (name, body) pairs. Matches mid-line so one-line LLM overrides
    (`... } solvers { p { solver GAMG; } ...`) are handled.
    """
    out: list[tuple[str, str]] = []
    if not text:
        return out
    body = strip_comments(text)
    for m in _SECTION_RE.finditer(body):
        name = m.group("name").strip('"')
        pos = m.end()
        while pos < len(body) and body[pos] not in "{};":
            pos += 1
        if pos >= len(body) or body[pos] != "{":
            continue  # scalar entry, not a sub-block
        depth = 1
        i = pos + 1
        while depth and i < len(body):
            if body[i] == "{":
                depth += 1
            elif body[i] == "}":
                depth -= 1
            i += 1
        if depth == 0:
            out.append((name, body[pos + 1 : i - 1]))
    return out


def keys(text: str | None) -> list[str]:
    """Top-level entry names in a section body, in order (both scalar `key value;`
    entries and `key { ... }` sub-dict names, quoted keys unquoted).

    A word directly followed by `;` is a *value* token (e.g. `solver GAMG;`
    mid-line), not a key — only names that open a sub-dict (`name {`) or carry a
    value before the `;` (`name value;`) are keys.
    """
    out: list[str] = []
    if not text:
        return out
    body = strip_comments(text)
    for m in re.finditer(r'(?<![\w"\'])\s*(?P<name>"[^"]+"|[A-Za-z][A-Za-z0-9_]*)\s*', body):
        start = m.end()
        if start >= len(body):
            continue
        nxt = body[start]
        if nxt == "{":
            out.append(m.group("name").strip('"'))  # sub-dict name
        elif nxt != ";":
            # scalar entry: name must be followed by a value, then ';' — the
            # value itself ends the name's claim before the ';'.
            end = body.find(";", start)
            if end != -1 and not body[start:end].lstrip().startswith("{"):
                out.append(m.group("name").strip('"'))
    return out


def numbered_value(raw: str) -> float | None:
    """Extract the first number from an OpenFOAM value like `uniform 101325` or
    `(1 0 0)` or `3.5e-05`. Returns None if no number is present."""
    if not raw:
        return None
    m = re.search(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", raw)
    return float(m.group()) if m else None


def finite_value(raw: str) -> float | None:
    """Same as numbered_value but rejects nan/inf spellings so positivity checks
    treat them as unusable rather than infinitely-bad."""
    v = numbered_value(raw)
    if v is None:
        return None
    try:
        return v if (v == v and abs(v) != float("inf")) else None
    except Exception:
        return None


def boundary_fields(field_text: str) -> dict[str, dict[str, str]]:
    """Parse a 0/ field file's per-patch boundaryField entries.

    Returns {patch_name: {entry_key: value}}. patch `type` is included as the
    "type" key so callers can check wall functions / BC compatibility.
    """
    text = strip_comments(field_text)
    bf = section(text, "boundaryField") or section(text, "boundaryField ")
    if bf is None:
        return {}
    out: dict[str, dict[str, str]] = {}
    # Inside boundaryField: `patchName { type ...; ... }` blocks.
    for m in re.finditer(r'(?:^|\n)\s*(?P<name>"[^"]+"|[A-Za-z][A-Za-z0-9_]*)\s*\{', bf):
        body = section(bf, m.group("name").strip('"'))
        if body is not None:
            out[m.group("name").strip('"')] = entries(body)
    return out