"""OpenAI implementation of ModelProvider."""

import json
import uuid
from typing import Any, cast

import openai
from openai.types import CompletionUsage

from agentbench.models.provider import (
    Message,
    ProviderResponse,
    StopReason,
    ToolCall,
    ToolSpec,
)
from agentbench.models.run import TokenUsage
from agentbench.providers.pricing import Price, lookup

_STOP_MAP: dict[str, StopReason] = {
    "stop": "end_turn",
    "tool_calls": "tool_use",
    "length": "max_tokens",
}


class OpenAIProvider:
    def __init__(
        self,
        model: str,
        api_key: str,
        system: str | None = None,
        max_tokens: int = 8096,
        base_url: str | None = None,
        price: Price | None = None,
    ) -> None:
        """`base_url` enables OpenAI-compatible endpoints (Ollama, Groq, ...).
        `price` overrides the pricing table (local models are free)."""
        self._model = model
        self._system = system
        self._max_tokens = max_tokens
        self._price = price
        self._client = openai.AsyncOpenAI(api_key=api_key, base_url=base_url)

    async def run_agent_turn(
        self, messages: list[Message], tools: list[ToolSpec]
    ) -> ProviderResponse:
        wire: list[dict[str, Any]] = []
        if self._system:
            wire.append({"role": "system", "content": self._system})
        wire.extend(_to_openai_messages(messages))
        response = await self._client.chat.completions.create(
            model=self._model,
            max_tokens=self._max_tokens,
            # provider-neutral messages are converted to plain dicts by design
            messages=cast(Any, wire),
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
                for t in tools
            ],
        )

        choice = response.choices[0]
        tool_calls = [
            ToolCall(
                id=tc.id,
                name=tc.function.name,
                arguments=json.loads(tc.function.arguments or "{}"),
            )
            # a custom (non-function) tool call carries no .function and would
            # raise here; we declare only function tools, so skip anything else
            for tc in (choice.message.tool_calls or [])
            if tc.type == "function"
        ]
        content = choice.message.content or ""
        finish_reason = choice.finish_reason
        if not tool_calls:
            # Compatibility shim: some models (e.g. qwen2.5-coder via Ollama)
            # emit tool calls as JSON text in `content` instead of the
            # structured tool_calls field.
            parsed = _tool_call_from_content(content, {t.name for t in tools})
            if parsed is not None:
                tool_calls = [parsed]
                content = ""
                finish_reason = "tool_calls"
        assistant = Message(role="assistant", content=content, tool_calls=tool_calls)
        usage = TokenUsage()
        if response.usage:
            # prompt_tokens already includes any cached tokens, so they are
            # not broken out here — doing so would double-count them.
            usage = TokenUsage(
                tokens_in=response.usage.prompt_tokens,
                tokens_out=_billed_output_tokens(response.usage),
            )
        return ProviderResponse(
            messages=[assistant],
            tool_calls=tool_calls,
            usage=usage,
            stop_reason=_STOP_MAP.get(finish_reason, "error"),
        )

    def compute_cost(self, usage: TokenUsage) -> float | None:
        """None when this model has no price on file — never a guess."""
        price = self._price or lookup(self._model)
        return price.cost(usage) if price else None


def _billed_output_tokens(usage: CompletionUsage) -> int:
    """Output tokens as billed, thinking included.

    OpenAI counts reasoning tokens inside completion_tokens. Gemini's
    compatible endpoint leaves them out and only shows them in total_tokens
    (7 in, 5 out, 255 total for a three-word reply), yet bills them as
    output — so completion_tokens alone under-reports cost several-fold.
    """
    total = getattr(usage, "total_tokens", None)
    if isinstance(total, int):
        return max(usage.completion_tokens, total - usage.prompt_tokens)
    return usage.completion_tokens


def _to_openai_messages(messages: list[Message]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for msg in messages:
        if msg.role == "tool":
            out.append({"role": "tool", "tool_call_id": msg.tool_call_id, "content": msg.content})
        elif msg.role == "assistant":
            # null content is rejected by some OpenAI-compatible servers
            # (Ollama: "invalid message content type: <nil>") — always a string
            wire: dict[str, Any] = {"role": "assistant", "content": msg.content or ""}
            if msg.tool_calls:
                wire["tool_calls"] = [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {
                            "name": call.name,
                            "arguments": json.dumps(call.arguments),
                        },
                    }
                    for call in msg.tool_calls
                ]
            out.append(wire)
        else:
            out.append({"role": "user", "content": msg.content})
    return out


def _tool_call_from_content(content: str, known_tools: set[str]) -> ToolCall | None:
    """Recover a tool call emitted as JSON text.

    Models frequently wrap the call in code fences, pretty-print it, or emit
    trailing junk (extra tokens, a second partial object) — so scan for the
    first decodable JSON object instead of demanding a full match.
    """
    decoder = json.JSONDecoder()
    for index, char in enumerate(content):
        if char != "{":
            continue
        try:
            data, _ = decoder.raw_decode(content[index:])
        except json.JSONDecodeError:
            continue
        if (
            isinstance(data, dict)
            and data.get("name") in known_tools
            and isinstance(data.get("arguments", {}), dict)
        ):
            return ToolCall(
                id=f"call_{uuid.uuid4().hex[:24]}",
                name=data["name"],
                arguments=data.get("arguments", {}),
            )
    return None
