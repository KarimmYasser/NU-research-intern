"""Shared local-LLM client. OpenAI-compatible so the backend (Ollama, vLLM,
SGLang - see docs/local-llm-backend-comparison.md) can change without touching
agent code, only this base_url/model config.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()  # reads .env in the repo root if present; no-op otherwise


@dataclass
class LLMConfig:
    base_url: str = field(default_factory=lambda: os.environ.get("LOCAL_LLM_BASE_URL", "http://localhost:11434/v1"))
    model: str = field(default_factory=lambda: os.environ.get("LOCAL_LLM_MODEL", "TODO-pick-a-model"))
    # Ollama/vLLM/SGLang don't check this key, but the OpenAI client requires
    # a non-empty string to be passed. LLM_GATEWAY_API_KEY is used for the
    # Siemens SDC LLM Gateway (cloud, dev/testing only - see .env.example);
    # falls back to "not-needed" for local Ollama.
    api_key: str = field(default_factory=lambda: os.environ.get("LLM_GATEWAY_API_KEY") or "not-needed")


def make_client(config: LLMConfig | None = None) -> OpenAI:
    config = config or LLMConfig()
    # The Siemens SDC LLM Gateway authenticates via an `x-api-key` header
    # (not the SDK's default `Authorization: Bearer`), per its curl example:
    #   curl .../v1/models -H "x-api-key: $LLM_GATEWAY_API_KEY"
    # Passing it as a default header alongside the required (but ignored, for
    # this backend) `api_key` param keeps Ollama/vLLM/local backends working
    # unchanged (they ignore both).
    gateway_key = os.environ.get("LLM_GATEWAY_API_KEY")
    default_headers = {"x-api-key": gateway_key} if gateway_key else None
    return OpenAI(base_url=config.base_url, api_key=config.api_key, default_headers=default_headers)
