"""Tests that LLM configuration is safe to inspect in diagnostics."""

from llm.llm_client import LLMConfig


def test_llm_config_redacts_api_key_from_repr_and_diagnostics():
    config = LLMConfig(base_url="https://gateway.example/v1", model="test-model", api_key="super-secret")
    assert "super-secret" not in repr(config)
    assert config.redacted() == {
        "base_url": "https://gateway.example/v1",
        "model": "test-model",
        "api_key": "<redacted>",
    }


def test_local_llm_placeholder_is_not_misrepresented_as_a_secret():
    config = LLMConfig(api_key="not-needed")
    assert config.redacted()["api_key"] == "not-needed"