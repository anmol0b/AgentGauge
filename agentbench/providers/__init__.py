"""Provider factory. API keys are read from env vars only and held in memory —
never written to disk or the database (hard boundary).
"""

import os

from agentbench.providers.anthropic_provider import AnthropicProvider
from agentbench.providers.base import ModelProvider
from agentbench.providers.openai_provider import OpenAIProvider


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
            price=(0.0, 0.0),
        )
    if model.startswith(("gpt", "o1", "o3", "o4")):
        return OpenAIProvider(model, api_key=_require_env("OPENAI_API_KEY"), system=system)
    raise ValueError(f"no provider for model {model!r} (expected claude-*, gpt-*, or ollama/*)")


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} is not set; export it before running")
    return value
