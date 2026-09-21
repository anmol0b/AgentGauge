"""run_agent_turn against fake SDK clients.

This is the code that converts provider-native responses into the normalized
schema. It had no coverage, which is where a provider API change would break
silently — the harness would just record zero tool calls and stop.
"""

from types import SimpleNamespace
from typing import Any

import pytest

from agentbench.models.provider import Message, ToolSpec
from agentbench.providers.anthropic_provider import AnthropicProvider
from agentbench.providers.openai_provider import OpenAIProvider

TOOLS = [ToolSpec(name="read_file", description="d", parameters={"type": "object"})]
MESSAGES = [Message(role="user", content="fix it")]


class FakeMessages:
    def __init__(self, response: Any) -> None:
        self._response = response
        self.kwargs: dict[str, Any] = {}

    async def create(self, **kwargs: Any) -> Any:
        self.kwargs = kwargs
        return self._response


def _anthropic(provider: AnthropicProvider, response: Any) -> FakeMessages:
    fake = FakeMessages(response)
    provider._client = SimpleNamespace(messages=fake)  # type: ignore[assignment]
    return fake


async def test_anthropic_turn_extracts_text_tools_and_usage():
    response = SimpleNamespace(
        content=[
            SimpleNamespace(type="text", text="I will read the file."),
            SimpleNamespace(
                type="tool_use", id="tu_1", name="read_file", input={"path": "calc.py"}
            ),
        ],
        usage=SimpleNamespace(
            input_tokens=120,
            output_tokens=45,
            cache_creation_input_tokens=10,
            cache_read_input_tokens=200,
        ),
        stop_reason="tool_use",
    )
    provider = AnthropicProvider("claude-opus-5", api_key="t", system="sys")
    fake = _anthropic(provider, response)

    result = await provider.run_agent_turn(MESSAGES, TOOLS)

    assert result.stop_reason == "tool_use"
    assert [c.name for c in result.tool_calls] == ["read_file"]
    assert result.tool_calls[0].arguments == {"path": "calc.py"}
    assert result.messages[0].content == "I will read the file."
    assert result.usage.tokens_in == 120 and result.usage.tokens_out == 45
    assert result.usage.cache_write_tokens == 10
    assert result.usage.cache_read_tokens == 200
    # the system prompt and tool schema must reach the wire
    assert fake.kwargs["system"] == "sys"
    assert fake.kwargs["tools"][0]["name"] == "read_file"


async def test_anthropic_unknown_stop_reason_becomes_error():
    response = SimpleNamespace(
        content=[],
        usage=SimpleNamespace(input_tokens=1, output_tokens=1),
        stop_reason="something_new",
    )
    provider = AnthropicProvider("claude-opus-5", api_key="t")
    _anthropic(provider, response)
    assert (await provider.run_agent_turn(MESSAGES, TOOLS)).stop_reason == "error"


async def test_anthropic_handles_absent_cache_fields():
    """Older responses omit the cache counters entirely."""
    response = SimpleNamespace(
        content=[],
        usage=SimpleNamespace(input_tokens=5, output_tokens=6),
        stop_reason="end_turn",
    )
    provider = AnthropicProvider("claude-opus-5", api_key="t")
    _anthropic(provider, response)
    usage = (await provider.run_agent_turn(MESSAGES, TOOLS)).usage
    assert usage.cache_write_tokens == 0 and usage.cache_read_tokens == 0


def _openai(provider: OpenAIProvider, response: Any) -> FakeMessages:
    fake = FakeMessages(response)
    provider._client = SimpleNamespace(  # type: ignore[assignment]
        chat=SimpleNamespace(completions=fake)
    )
    return fake


def _openai_response(content: str | None, tool_calls: Any, finish: str) -> Any:
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content, tool_calls=tool_calls),
                finish_reason=finish,
            )
        ],
        usage=SimpleNamespace(prompt_tokens=80, completion_tokens=20),
    )


async def test_openai_turn_extracts_tool_calls():
    call = SimpleNamespace(
        id="call_1",
        type="function",
        function=SimpleNamespace(name="read_file", arguments='{"path": "calc.py"}'),
    )
    provider = OpenAIProvider("gpt-4o", api_key="t", system="sys")
    fake = _openai(provider, _openai_response(None, [call], "tool_calls"))

    result = await provider.run_agent_turn(MESSAGES, TOOLS)

    assert result.stop_reason == "tool_use"
    assert result.tool_calls[0].arguments == {"path": "calc.py"}
    assert result.usage.tokens_in == 80 and result.usage.tokens_out == 20
    assert fake.kwargs["messages"][0] == {"role": "system", "content": "sys"}


async def test_openai_skips_non_function_tool_calls():
    """A custom tool call carries no .function and used to raise here."""
    custom = SimpleNamespace(
        id="call_1", type="custom", custom=SimpleNamespace(name="x", input="y")
    )
    provider = OpenAIProvider("gpt-4o", api_key="t")
    _openai(provider, _openai_response("text", [custom], "tool_calls"))

    result = await provider.run_agent_turn(MESSAGES, TOOLS)
    assert result.tool_calls == []


async def test_openai_recovers_a_tool_call_emitted_as_text():
    """Ollama-hosted models often put the call in content instead."""
    provider = OpenAIProvider("qwen", api_key="t")
    _openai(
        provider,
        _openai_response(
            '```json\n{"name": "read_file", "arguments": {"path": "a.py"}}\n```', None, "stop"
        ),
    )

    result = await provider.run_agent_turn(MESSAGES, TOOLS)
    assert result.stop_reason == "tool_use"
    assert result.tool_calls[0].name == "read_file"
    assert result.messages[0].content == ""


async def test_openai_tolerates_a_response_without_usage():
    provider = OpenAIProvider("gpt-4o", api_key="t")
    response = _openai_response("done", None, "stop")
    response.usage = None
    _openai(provider, response)

    result = await provider.run_agent_turn(MESSAGES, TOOLS)
    assert result.usage.tokens_in == 0 and result.stop_reason == "end_turn"


@pytest.mark.parametrize(
    "finish,expected",
    [
        ("stop", "end_turn"),
        ("tool_calls", "tool_use"),
        ("length", "max_tokens"),
        ("content_filter", "error"),
    ],
)
async def test_openai_stop_reason_mapping(finish, expected):
    provider = OpenAIProvider("gpt-4o", api_key="t")
    _openai(provider, _openai_response("x", None, finish))
    assert (await provider.run_agent_turn(MESSAGES, TOOLS)).stop_reason == expected
