"""Provider message-conversion and pricing tests (no network calls)."""

from agentbench.models.provider import Message, ToolCall
from agentbench.models.run import TokenUsage
from agentbench.providers.anthropic_provider import (
    AnthropicProvider,
    _to_anthropic_messages,
)
from agentbench.providers.openai_provider import (
    OpenAIProvider,
    _to_openai_messages,
    _tool_call_from_content,
)


def _conversation():
    call = ToolCall(id="c1", name="read_file", arguments={"path": "a.py"})
    return [
        Message(role="user", content="fix it"),
        Message(role="assistant", content="", tool_calls=[call]),
        Message(role="tool", content="file contents", tool_call_id="c1"),
        Message(role="tool", content="second result", tool_call_id="c2"),
        Message(role="assistant", content="done"),
    ]


def test_anthropic_conversion_merges_consecutive_tool_results():
    wire = _to_anthropic_messages(_conversation())
    assert wire[0] == {"role": "user", "content": "fix it"}
    assert wire[1]["role"] == "assistant"
    assert wire[1]["content"][0]["type"] == "tool_use"
    # both tool results merged into one user message
    assert wire[2]["role"] == "user"
    assert [b["type"] for b in wire[2]["content"]] == ["tool_result", "tool_result"]
    assert wire[2]["content"][0]["tool_use_id"] == "c1"
    assert wire[3] == {"role": "assistant", "content": [{"type": "text", "text": "done"}]}


def test_openai_conversion_shape():
    wire = _to_openai_messages(_conversation())
    assert wire[0] == {"role": "user", "content": "fix it"}
    assert wire[1]["tool_calls"][0]["function"]["name"] == "read_file"
    assert wire[1]["tool_calls"][0]["function"]["arguments"] == '{"path": "a.py"}'
    assert wire[2] == {"role": "tool", "tool_call_id": "c1", "content": "file contents"}
    assert wire[4] == {"role": "assistant", "content": "done"}


def test_anthropic_pricing():
    provider = AnthropicProvider("claude-sonnet-4-5", api_key="test")
    cost = provider.compute_cost(TokenUsage(tokens_in=1_000_000, tokens_out=1_000_000))
    assert cost == 3.0 + 15.0


def test_openai_pricing():
    provider = OpenAIProvider("gpt-4o-mini", api_key="test")
    cost = provider.compute_cost(TokenUsage(tokens_in=1_000_000, tokens_out=1_000_000))
    assert cost == 0.15 + 0.60


def test_tool_call_from_content_json():
    known = {"read_file", "edit"}
    call = _tool_call_from_content('{"name": "read_file", "arguments": {"path": "a.py"}}', known)
    assert call is not None
    assert call.name == "read_file" and call.arguments == {"path": "a.py"}


def test_tool_call_from_content_fenced():
    call = _tool_call_from_content(
        '```json\n{"name": "edit", "arguments": {"path": "a.py"}}\n```', {"edit"}
    )
    assert call is not None and call.name == "edit"


def test_tool_call_from_content_rejects_non_tool_json():
    assert _tool_call_from_content('{"name": "hack", "arguments": {}}', {"edit"}) is None
    assert _tool_call_from_content("just some prose", {"edit"}) is None
    assert _tool_call_from_content('{"name": "edit", "arguments": "oops"}', {"edit"}) is None


def test_pricing_prefix_match():
    provider = AnthropicProvider("claude-sonnet-4-5-20250929", api_key="test")
    assert provider.compute_cost(TokenUsage(tokens_in=1_000_000, tokens_out=0)) == 3.0
