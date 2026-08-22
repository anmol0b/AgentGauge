"""ModelProvider interface — all provider-specific code stays in this package."""

from typing import Protocol

from agentbench.models.provider import Message, ProviderResponse, ToolSpec
from agentbench.models.run import TokenUsage


class ModelProvider(Protocol):
    async def run_agent_turn(
        self, messages: list[Message], tools: list[ToolSpec]
    ) -> ProviderResponse: ...

    def compute_cost(self, usage: TokenUsage) -> float: ...
