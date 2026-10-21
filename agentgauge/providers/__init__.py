"""Provider factory. API keys are read from env vars only and held in memory —
never written to disk or the database (hard boundary).
"""

import os

from agentgauge.providers.anthropic_provider import AnthropicProvider
from agentgauge.providers.base import ModelProvider
from agentgauge.providers.openai_provider import OpenAIProvider
from agentgauge.providers.pricing import lookup

_GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"


def make_provider(model: str, system: str | None = None) -> ModelProvider:
    if model.startswith("claude"):
        return AnthropicProvider(model, api_key=_require_env("ANTHROPIC_API_KEY"), system=system)
    if model.startswith("ollama/"):
        # Local model via Ollama's OpenAI-compatible endpoint. No key, no cost.
        return OpenAIProvider(
            model.removeprefix("ollama/"),
            api_key="ollama",
            system=system,
            base_url="http://localhost:11434/v1",
            price=lookup("ollama/"),  # local models are free, and recorded as such
        )
    if model.startswith(("gpt", "o1", "o3", "o4")):
        return OpenAIProvider(model, api_key=_require_env("OPENAI_API_KEY"), system=system)
    if model.startswith("gemini"):
        # Google's OpenAI-compatible endpoint, which has a free tier.
        return OpenAIProvider(
            model,
            api_key=_require_env("GEMINI_API_KEY"),
            system=system,
            base_url=_GEMINI_BASE_URL,
        )
    raise ValueError(
        f"no provider for model {model!r} (expected claude-*, gpt-*, gemini-*, or ollama/*)"
    )


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} is not set; export it before running")
    return value
