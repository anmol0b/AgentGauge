"""Anthropic implementation of ModelProvider."""

from typing import Any

import anthropic

from agentgauge.models.provider import (
    Message,
    ProviderResponse,
    StopReason,
    ToolCall,
    ToolSpec,
)
from agentgauge.models.run import TokenUsage
from agentgauge.providers.pricing import lookup

_STOP_MAP: dict[str, StopReason] = {
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
        kwargs: dict[str, Any] = {
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
            usage=_usage(response.usage),
            stop_reason=_STOP_MAP.get(response.stop_reason or "", "error"),
        )

    def compute_cost(self, usage: TokenUsage) -> float | None:
        """None when this model has no price on file — never a guess."""
        price = lookup(self._model)
        return price.cost(usage) if price else None


def _usage(raw: Any) -> TokenUsage:
    """Anthropic reports cache tokens separately from `input_tokens`, so these
    add to the total rather than double-counting it."""
    return TokenUsage(
        tokens_in=raw.input_tokens,
        tokens_out=raw.output_tokens,
        cache_write_tokens=getattr(raw, "cache_creation_input_tokens", 0) or 0,
        cache_read_tokens=getattr(raw, "cache_read_input_tokens", 0) or 0,
    )


def _to_anthropic_messages(messages: list[Message]) -> list[dict[str, Any]]:
    """Convert normalized messages to Anthropic format.

    Tool results (role="tool") become tool_result blocks inside user messages;
    consecutive tool results merge into one user message.
    """
    out: list[dict[str, Any]] = []
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
            content: list[dict[str, Any]] = []
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
