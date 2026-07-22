"""Shared local-LLM client. OpenAI-compatible so the backend (Ollama, vLLM,
SGLang - see docs/local-llm-backend-comparison.md) can change without touching
agent code, only this base_url/model config.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from openai import OpenAI


@dataclass
class LLMConfig:
    base_url: str = os.environ.get("LOCAL_LLM_BASE_URL", "http://localhost:11434/v1")
    model: str = os.environ.get("LOCAL_LLM_MODEL", "TODO-pick-a-model")
    # Ollama/vLLM/SGLang don't check this key, but the OpenAI client requires
    # a non-empty string to be passed.
    api_key: str = "not-needed"


def make_client(config: LLMConfig | None = None) -> OpenAI:
    config = config or LLMConfig()
    return OpenAI(base_url=config.base_url, api_key=config.api_key)
