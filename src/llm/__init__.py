"""LLM client setup sub-package."""

from .llm_client import LLMConfig, make_client

__all__ = [
    "LLMConfig",
    "make_client",
]
