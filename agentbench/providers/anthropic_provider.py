"""Anthropic implementation of ModelProvider."""

import anthropic

from agentbench.models.provider import (
    Message,
    ProviderResponse,
    ToolCall,
    ToolSpec,
)
from agentbench.models.run import TokenUsage

# USD per 1M tokens: (input, output)
_PRICING: dict[str, tuple[float, float]] = {
    "claude-opus-4-1": (15.0, 75.0),
    "claude-sonnet-4-5": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
}
_DEFAULT_PRICE = (3.0, 15.0)

_STOP_MAP = {
    "end_turn": "end_turn",
    "tool_use": "tool_use",
    "max_tokens": "max_tokens",
}


class AnthropicProvider:
    def __init__(
        self, model: str, api_key: str, system: str | None = None, max_tokens: int = 8096
    ) -> None:
        self._model = model
        self._system = system
        self._max_tokens = max_tokens
        self._client = anthropic.AsyncAnthropic(api_key=api_key)

    async def run_agent_turn(
        self, messages: list[Message], tools: list[ToolSpec]
    ) -> ProviderResponse:
        kwargs: dict = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "messages": _to_anthropic_messages(messages),
            "tools": [
                {
                    "name": t.name,
                    "description": t.description,
                    "input_schema": t.parameters,
                }
                for t in tools
            ],
        }
        if self._system:
            kwargs["system"] = self._system
        response = await self._client.messages.create(**kwargs)

        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(
                    ToolCall(id=block.id, name=block.name, arguments=dict(block.input))
                )
        assistant = Message(role="assistant", content="".join(text_parts), tool_calls=tool_calls)
        return ProviderResponse(
            messages=[assistant],
            tool_calls=tool_calls,
            usage=TokenUsage(
                tokens_in=response.usage.input_tokens,
                tokens_out=response.usage.output_tokens,
            ),
            stop_reason=_STOP_MAP.get(response.stop_reason, "error"),
        )

    def compute_cost(self, usage: TokenUsage) -> float:
        price_in, price_out = next(
            (p for prefix, p in sorted(_PRICING.items(), key=lambda kv: -len(kv[0]))
             if self._model.startswith(prefix)),
            _DEFAULT_PRICE,
        )
        return (usage.tokens_in * price_in + usage.tokens_out * price_out) / 1_000_000


def _to_anthropic_messages(messages: list[Message]) -> list[dict]:
    """Convert normalized messages to Anthropic format.

    Tool results (role="tool") become tool_result blocks inside user messages;
    consecutive tool results merge into one user message.
    """
    out: list[dict] = []
    for msg in messages:
        if msg.role == "tool":
            block = {
                "type": "tool_result",
                "tool_use_id": msg.tool_call_id,
                "content": msg.content,
            }
            if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list):
                out[-1]["content"].append(block)
            else:
                out.append({"role": "user", "content": [block]})
        elif msg.role == "assistant":
            content: list[dict] = []
            if msg.content:
                content.append({"type": "text", "text": msg.content})
            for call in msg.tool_calls:
                content.append(
                    {
                        "type": "tool_use",
                        "id": call.id,
                        "name": call.name,
                        "input": call.arguments,
                    }
                )
            out.append({"role": "assistant", "content": content})
        else:
            out.append({"role": "user", "content": msg.content})
    return out
