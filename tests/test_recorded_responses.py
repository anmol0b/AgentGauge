"""Replay provider responses through the real SDK types and our parsing.

The fixtures in `tests/fixtures/responses/` are either recorded from a live
endpoint or built from the SDKs' published response schema — the README
there says which is which. Either way they go through the same path a live
response does: SDK parsing, then the provider, then the agent loop and the
pricing table. That is the whole route a cost figure takes, run with no key.
"""

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx2
import openai
import pytest
from anthropic.types import Message as AnthropicMessage
from openai.types.chat import ChatCompletion

from agentgauge.agent_loop.loop import run_agent_loop
from agentgauge.models.provider import Message
from agentgauge.providers.anthropic_provider import AnthropicProvider
from agentgauge.providers.openai_provider import OpenAIProvider
from agentgauge.providers.pricing import lookup
from tests.fakes import FakeExecutor, host_path

RESPONSES = Path(__file__).parent / "fixtures" / "responses"
FILE_TEXT = "def add(a, b):\n    return a - b\n"


def _load(name: str) -> Any:
    return json.loads((RESPONSES / name).read_text())


def _completion(name: str) -> ChatCompletion:
    return ChatCompletion.model_validate(_load(name))


def _error(name: str) -> openai.APIStatusError:
    """The SDK error a recorded error body would have been raised as."""
    body = _load(name)
    status = body[0]["error"]["code"]
    response = httpx2.Response(status, request=httpx2.Request("POST", "https://example.test"))
    kind = {400: openai.BadRequestError, 429: openai.RateLimitError}.get(
        status, openai.InternalServerError
    )
    return kind(body[0]["error"]["message"], response=response, body=body)


class Replay:
    """Answers each create() with the next recorded response or error."""

    def __init__(self, script: list[Any]) -> None:
        self._script = list(script)
        self.requests: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.requests.append(kwargs)
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture
def sleeps(monkeypatch) -> list[float]:
    waited: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        waited.append(seconds)

    monkeypatch.setattr("agentgauge.providers.openai_provider._sleep", fake_sleep)
    return waited


def _openai(provider: OpenAIProvider, script: list[Any]) -> Replay:
    replay = Replay(script)
    provider._client = SimpleNamespace(  # type: ignore[assignment]
        chat=SimpleNamespace(completions=replay)
    )
    return replay


# --- Gemini: recorded ---------------------------------------------------------


async def test_a_recorded_gemini_run_is_retried_signed_and_costed(sleeps):
    """The first live run's path end to end: throttled, then a signed tool
    call that must go back on the next turn, then a reply whose thinking is
    only visible in total_tokens."""
    provider = OpenAIProvider("gemini-3.6-flash", api_key="t")
    replay = _openai(
        provider,
        [
            _error("gemini/error_429_per_minute.json"),
            _completion("gemini/tool_call.json"),
            _completion("gemini/text_reply.json"),
        ],
    )
    executor = FakeExecutor(files={"src/app.py": FILE_TEXT})
    handle = await executor.start(host_path("repo"), "img")

    result = await run_agent_loop(provider, executor, handle, "fix it", max_turns=2)

    # "Please retry in 38.967430266s", plus a second's margin
    assert sleeps == [pytest.approx(39.967430266)]
    sent = replay.requests[-1]["messages"]
    assistant = next(m for m in sent if m["role"] == "assistant" and m.get("tool_calls"))
    assert assistant["tool_calls"][0]["extra_content"] == {
        "google": {"thought_signature": "EuEBCt4BAWkUfROH"}
    }
    tool_result = next(m for m in sent if m["role"] == "tool")
    assert tool_result["content"] == FILE_TEXT

    # tool call: 49 in, 109 - 49 = 60 out. reply: 7 in, 348 - 7 = 341 out.
    assert result.usage.tokens_in == 56
    assert result.usage.tokens_out == 401
    price = lookup("gemini-3.6-flash")
    assert price is not None
    assert result.cost_usd == pytest.approx((56 * price.input + 401 * price.output) / 1e6)


async def test_a_recorded_daily_quota_fails_at_once(sleeps):
    provider = OpenAIProvider("gemini-3.6-flash", api_key="t")
    _openai(provider, [_error("gemini/error_429_per_day.json")])

    with pytest.raises(openai.RateLimitError):
        await provider.run_agent_turn([Message(role="user", content="fix it")], [])
    assert sleeps == []  # it asked for 74258s: a day's quota, not a minute's


async def test_a_recorded_overload_is_retried(sleeps):
    provider = OpenAIProvider("gemini-3.6-flash", api_key="t")
    _openai(
        provider,
        [_error("gemini/error_503_high_demand.json"), _completion("gemini/text_reply.json")],
    )

    result = await provider.run_agent_turn([Message(role="user", content="fix it")], [])
    assert result.stop_reason == "end_turn" and sleeps == [5.0]


async def test_a_recorded_bad_request_is_not_retried(sleeps):
    """A 400 will fail identically every time; retrying only burns quota."""
    provider = OpenAIProvider("gemini-3.6-flash", api_key="t")
    replay = _openai(provider, [_error("gemini/error_400_missing_signature.json")])

    with pytest.raises(openai.BadRequestError, match="thought_signature"):
        await provider.run_agent_turn([Message(role="user", content="fix it")], [])
    assert len(replay.requests) == 1 and sleeps == []


# --- OpenAI: schema-shaped ----------------------------------------------------


async def test_openai_bills_cached_input_at_the_cached_rate():
    """10,000 prompt tokens of which 8,000 cached; reasoning is already
    inside completion_tokens, so output is 500, not 700."""
    provider = OpenAIProvider("gpt-4o", api_key="t")
    _openai(provider, [_completion("openai/tool_call_cached_reasoning.json")])

    result = await provider.run_agent_turn([Message(role="user", content="fix it")], [])

    assert result.tool_calls[0].arguments == {"path": "src/app.py"}
    assert result.usage.tokens_in == 2000
    assert result.usage.cache_read_tokens == 8000
    assert result.usage.tokens_out == 500
    # 2,000 x $2.50 + 8,000 x $1.25 + 500 x $10.00, per million
    assert provider.compute_cost(result.usage) == pytest.approx(0.02)


# --- Anthropic: schema-shaped -------------------------------------------------


async def test_anthropic_bills_cache_writes_and_reads_separately():
    """input_tokens excludes both cache counts, so the three add up."""
    provider = AnthropicProvider("claude-haiku-4-5", api_key="t")
    response = AnthropicMessage.model_validate(_load("anthropic/tool_use_cached.json"))

    async def create(**kwargs: Any) -> AnthropicMessage:
        return response

    provider._client = SimpleNamespace(  # type: ignore[assignment]
        messages=SimpleNamespace(create=create)
    )
    result = await provider.run_agent_turn([Message(role="user", content="fix it")], [])

    assert result.stop_reason == "tool_use"
    assert result.messages[0].content == "I'll read the file first."
    assert result.tool_calls[0].arguments == {"path": "src/app.py"}
    assert (result.usage.tokens_in, result.usage.tokens_out) == (2000, 300)
    assert result.usage.cache_write_tokens == 1000
    assert result.usage.cache_read_tokens == 8000
    # $1 in, $5 out; writes 1.25x and reads 0.10x of input, per million
    assert provider.compute_cost(result.usage) == pytest.approx(
        (2000 * 1.0 + 300 * 5.0 + 1000 * 1.25 + 8000 * 0.10) / 1e6
    )


def test_every_fixture_matches_its_sdk_schema_exactly():
    """An unknown top-level field means a fixture drifted from the published
    shape. (Gemini's extra_content is nested, and read deliberately.)"""
    for path in sorted(RESPONSES.glob("*/*.json")):
        data = json.loads(path.read_text())
        if isinstance(data, list):  # error bodies have no SDK model
            continue
        model = AnthropicMessage if path.parent.name == "anthropic" else ChatCompletion
        parsed = model.model_validate(data)
        assert not parsed.model_extra, f"{path.name}: unexpected {parsed.model_extra}"
