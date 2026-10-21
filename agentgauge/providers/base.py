"""ModelProvider interface — all provider-specific code stays in this package."""

from typing import Protocol

from agentgauge.models.provider import Message, ProviderResponse, ToolSpec
from agentgauge.models.run import TokenUsage


class ModelProvider(Protocol):
    async def run_agent_turn(
        self, messages: list[Message], tools: list[ToolSpec]
    ) -> ProviderResponse: ...

    # None when the model has no price on file — see providers/pricing.py
    def compute_cost(self, usage: TokenUsage) -> float | None: ...
