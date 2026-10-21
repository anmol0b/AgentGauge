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


async def test_openai_echoes_a_tool_calls_thought_signature_next_turn():
    """Gemini 3 rejects turn two with a 400 unless the signature comes back.

    The response is a real one from Gemini's compatible endpoint (signature
    shortened), parsed by the SDK itself: extra_content is not in its schema,
    so this checks the field survives parsing as well as the round trip.
    """
    from openai.types.chat import ChatCompletion

    response = ChatCompletion.model_validate(
        {
            "id": "743YarDWHNWJjuMPvI-ZuAE",
            "object": "chat.completion",
            "created": 1792577008,
            "model": "gemini-3.5-flash",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "tool_calls",
                    "message": {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "id": "call_958686",
                                "type": "function",
                                "function": {
                                    "name": "read_file",
                                    "arguments": '{"path":"src/app.py"}',
                                },
                                "extra_content": {"google": {"thought_signature": "EuEBCt4B"}},
                            }
                        ],
                    },
                }
            ],
            "usage": {"completion_tokens": 20, "prompt_tokens": 49, "total_tokens": 109},
        }
    )
    provider = OpenAIProvider("gemini-3.5-flash", api_key="t")
    fake = _openai(provider, response)
    first = await provider.run_agent_turn(MESSAGES, TOOLS)

    history = [
        *MESSAGES,
        *first.messages,
        Message(role="tool", tool_call_id="call_958686", content="def add(a, b): ..."),
    ]
    await provider.run_agent_turn(history, TOOLS)

    sent_call = fake.kwargs["messages"][1]["tool_calls"][0]
    assert sent_call["extra_content"] == {"google": {"thought_signature": "EuEBCt4B"}}
    assert first.usage.tokens_out == 60


async def test_openai_sends_no_extra_content_for_a_plain_tool_call():
    """Strict OpenAI-compatible servers may reject an unknown field."""
    call = SimpleNamespace(
        id="call_1",
        type="function",
        function=SimpleNamespace(name="read_file", arguments="{}"),
    )
    provider = OpenAIProvider("gpt-4o", api_key="t")
    fake = _openai(provider, _openai_response(None, [call], "tool_calls"))
    first = await provider.run_agent_turn(MESSAGES, TOOLS)

    await provider.run_agent_turn([*MESSAGES, *first.messages], TOOLS)
    assert "extra_content" not in fake.kwargs["messages"][1]["tool_calls"][0]


async def test_openai_counts_thinking_left_out_of_completion_tokens():
    """Gemini reports thinking only in total_tokens, and bills it as output."""
    provider = OpenAIProvider("gemini-3.6-flash", api_key="t")
    response = _openai_response("Hi there, friend!", None, "stop")
    response.usage = SimpleNamespace(prompt_tokens=7, completion_tokens=5, total_tokens=255)
    _openai(provider, response)

    usage = (await provider.run_agent_turn(MESSAGES, TOOLS)).usage
    assert usage.tokens_in == 7 and usage.tokens_out == 248


async def test_openai_does_not_double_count_reasoning_already_in_completion():
    """OpenAI's completion_tokens already includes reasoning."""
    provider = OpenAIProvider("o3-mini", api_key="t")
    response = _openai_response("done", None, "stop")
    response.usage = SimpleNamespace(prompt_tokens=80, completion_tokens=20, total_tokens=100)
    _openai(provider, response)

    assert (await provider.run_agent_turn(MESSAGES, TOOLS)).usage.tokens_out == 20


def _api_error(status: int, retry_delay: str | None = None) -> Exception:
    """An SDK error shaped like Gemini's: the body is a one-element list."""
    import httpx2
    import openai

    details = [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": retry_delay}]
    body = [{"error": {"code": status, "details": details if retry_delay else []}}]
    response = httpx2.Response(status, request=httpx2.Request("POST", "https://example.test"))
    if status == 429:
        return openai.RateLimitError("quota", response=response, body=body)
    return openai.InternalServerError("high demand", response=response, body=body)


class FlakyCompletions:
    """Raises each queued error once, then returns the response."""

    def __init__(self, errors: list[Exception], response: Any) -> None:
        self._errors = list(errors)
        self._response = response
        self.calls = 0

    async def create(self, **kwargs: Any) -> Any:
        self.calls += 1
        if self._errors:
            raise self._errors.pop(0)
        return self._response


@pytest.fixture
def sleeps(monkeypatch) -> list[float]:
    waited: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        waited.append(seconds)

    monkeypatch.setattr("agentbench.providers.openai_provider._sleep", fake_sleep)
    return waited


def _flaky(provider: OpenAIProvider, errors: list[Exception]) -> FlakyCompletions:
    fake = FlakyCompletions(errors, _openai_response("done", None, "stop"))
    provider._client = SimpleNamespace(  # type: ignore[assignment]
        chat=SimpleNamespace(completions=fake)
    )
    return fake


async def test_a_rate_limit_waits_as_long_as_the_server_asks(sleeps):
    provider = OpenAIProvider("gemini-3.6-flash", api_key="t")
    fake = _flaky(provider, [_api_error(429, "38s")])

    result = await provider.run_agent_turn(MESSAGES, TOOLS)
    assert result.stop_reason == "end_turn" and fake.calls == 2
    assert sleeps == [39.0]


async def test_an_overload_without_a_delay_backs_off(sleeps):
    provider = OpenAIProvider("gemini-3.6-flash", api_key="t")
    _flaky(provider, [_api_error(503), _api_error(503)])

    await provider.run_agent_turn(MESSAGES, TOOLS)
    assert sleeps == [5.0, 10.0]


async def test_a_daily_quota_fails_at_once_instead_of_waiting(sleeps):
    import openai

    provider = OpenAIProvider("gemini-3.6-flash", api_key="t")
    _flaky(provider, [_api_error(429, "3600s")])

    with pytest.raises(openai.RateLimitError):
        await provider.run_agent_turn(MESSAGES, TOOLS)
    assert sleeps == []


async def test_retries_are_bounded(sleeps):
    import openai

    from agentbench.providers.openai_provider import _MAX_ATTEMPTS

    provider = OpenAIProvider("gemini-3.6-flash", api_key="t")
    fake = _flaky(provider, [_api_error(503) for _ in range(_MAX_ATTEMPTS)])

    with pytest.raises(openai.InternalServerError):
        await provider.run_agent_turn(MESSAGES, TOOLS)
    assert fake.calls == _MAX_ATTEMPTS


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
