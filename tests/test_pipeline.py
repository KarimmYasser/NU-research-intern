"""Unit tests for the LLM-extraction JSON parsing helpers in orchestration.pipeline."""

import json

from orchestration import pipeline


def test_extract_json_strips_comments():
    text = '{"regime": "laminar" // trailing comment\n}'
    assert json.loads(pipeline._extract_json(text)) == {"regime": "laminar"}


def test_extract_fenced_json_preserves_pair_slash_content():
    """extract_file_patches embeds whole OpenFOAM files, which are full of `//`
    comment lines and separators. The comment-stripping used for intent JSON
    would corrupt those; the fenced variant must leave them intact."""
    body = (
        '{"file_overrides": {"system/fvSolution": '
        '"solvers { U { solver smoothSolver; } } // * * * * * *\\n'
        'PIMPLE { nCorrectors 2; }"}}'
    )
    out = pipeline._extract_fenced_json(body)
    assert "// * * * * * *" in out, "fenced extractor dropped OpenFOAM // content"


def test_fenced_json_strips_fences():
    text = '```json\n{"file_overrides": {"a": "b"}}\n```'
    assert json.loads(pipeline._extract_fenced_json(text)) == {"file_overrides": {"a": "b"}}