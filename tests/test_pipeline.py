"""Unit tests for the LLM-extraction JSON parsing helpers in orchestration.pipeline."""

import json
from types import SimpleNamespace

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


def test_extract_intent_injects_primary_tutorial_template(monkeypatch):
    captured_messages = []

    class FakeClient:
        def __init__(self):
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

        def create(self, **kwargs):
            captured_messages.extend(kwargs["messages"])
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({
                "regime": "laminar",
                "geometry": "box",
                "box_size": [1, 1, 0.1],
                "patches": [{"name": "walls", "role": "wall"}],
            })))])

    monkeypatch.setattr(pipeline, "make_client", lambda config: FakeClient())
    intent = pipeline.extract_intent("lid driven cavity", max_retries=1)
    assert intent.geometry == "box"
    user_content = next(message["content"] for message in captured_messages if message["role"] == "user")
    assert "### Primary OpenFOAM v13 Tutorial Template" in user_content
    assert "tutorials/incompressibleFluid/cavity" in user_content
    assert "Do not blindly copy" in user_content