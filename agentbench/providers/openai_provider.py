"""OpenAI implementation of ModelProvider."""

import json
import uuid

import openai

from agentbench.models.provider import (
    Message,
    ProviderResponse,
    ToolCall,
    ToolSpec,
)
from agentbench.models.run import TokenUsage
from agentbench.providers.pricing import Price, lookup

_STOP_MAP = {
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
        wire: list[dict] = []
        if self._system:
            wire.append({"role": "system", "content": self._system})
        wire.extend(_to_openai_messages(messages))
        response = await self._client.chat.completions.create(
            model=self._model,
            max_tokens=self._max_tokens,
            messages=wire,
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
            for tc in (choice.message.tool_calls or [])
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
                tokens_out=response.usage.completion_tokens,
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


def _to_openai_messages(messages: list[Message]) -> list[dict]:
    out: list[dict] = []
    for msg in messages:
        if msg.role == "tool":
            out.append(
                {"role": "tool", "tool_call_id": msg.tool_call_id, "content": msg.content}
            )
        elif msg.role == "assistant":
            # null content is rejected by some OpenAI-compatible servers
            # (Ollama: "invalid message content type: <nil>") — always a string
            wire: dict = {"role": "assistant", "content": msg.content or ""}
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
