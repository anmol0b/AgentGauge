"""Agent loop tests with a scripted fake provider + in-memory executor."""

from pathlib import Path

from agentbench.agent_loop.loop import run_agent_loop
from agentbench.agent_loop.tools import execute_tool
from agentbench.models.provider import Message, ProviderResponse, ToolCall
from agentbench.models.run import TokenUsage
from tests.fakes import FakeExecutor


class ScriptedProvider:
    """Plays back a fixed sequence of ProviderResponses."""

    def __init__(self, responses: list[ProviderResponse], cost_per_turn: float = 0.001):
        self._responses = responses
        self._cost = cost_per_turn
        self.turns = 0

    async def run_agent_turn(self, messages, tools) -> ProviderResponse:
        response = self._responses[min(self.turns, len(self._responses) - 1)]
        self.turns += 1
        return response

    def compute_cost(self, usage: TokenUsage) -> float:
        return self._cost


def _tool_use_response(call_id="c1", name="write_file", args=None):
    call = ToolCall(id=call_id, name=name, arguments=args or {})
    return ProviderResponse(
        messages=[Message(role="assistant", content="", tool_calls=[call])],
        tool_calls=[call],
        usage=TokenUsage(tokens_in=10, tokens_out=5),
        stop_reason="tool_use",
    )


def _end_response(text="done"):
    return ProviderResponse(
        messages=[Message(role="assistant", content=text)],
        tool_calls=[],
        usage=TokenUsage(tokens_in=10, tokens_out=5),
        stop_reason="end_turn",
    )


async def test_loop_executes_tools_then_stops():
    executor = FakeExecutor()
    provider = ScriptedProvider(
        [
            _tool_use_response(args={"path": "a.py", "content": "fixed"}),
            _end_response(),
        ]
    )
    handle = await executor.start(Path("."), "img")
    result = await run_agent_loop(provider, executor, handle, "fix it")
    assert result.stop_reason == "end_turn"
    assert result.tool_call_count == 1
    assert result.usage.tokens_in == 20 and result.usage.tokens_out == 10
    assert result.cost_usd == 0.002
    assert executor.files["a.py"] == "fixed"
    # tool result was fed back into the message history
    assert any(m.role == "tool" and m.tool_call_id == "c1" for m in result.messages)


async def test_loop_nudges_when_agent_stops_without_editing():
    executor = FakeExecutor()
    provider = ScriptedProvider(
        [
            _end_response("the fix is to change a+b to a-b"),  # prose, no tools
            _tool_use_response(args={"path": "a.py", "content": "fixed"}),
            _end_response(),
        ]
    )
    handle = await executor.start(Path("."), "img")
    result = await run_agent_loop(provider, executor, handle, "fix it")
    assert result.stop_reason == "end_turn"
    assert executor.files["a.py"] == "fixed"
    assert any("have not modified any files" in m.content for m in result.messages)


async def test_loop_hits_max_turns():
    executor = FakeExecutor()
    provider = ScriptedProvider([_tool_use_response()])
    result = await run_agent_loop(
        provider, executor, await executor.start(Path("."), "img"), "fix it", max_turns=3
    )
    assert result.stop_reason == "max_turns"
    assert result.tool_call_count == 3


async def test_edit_tool():
    executor = FakeExecutor(files={"a.py": "x = 1\ny = 2\n"})
    handle = await executor.start(Path("."), "img")

    ok = await execute_tool(
        "edit",
        {"path": "a.py", "old_string": "x = 1", "new_string": "x = 42"},
        executor,
        handle,
        10,
    )
    assert ok.startswith("edited")
    assert executor.files["a.py"] == "x = 42\ny = 2\n"

    missing = await execute_tool(
        "edit",
        {"path": "a.py", "old_string": "nope", "new_string": "z"},
        executor,
        handle,
        10,
    )
    assert "not found" in missing

    executor.files["b.py"] = "dup dup"
    ambiguous = await execute_tool(
        "edit",
        {"path": "b.py", "old_string": "dup", "new_string": "z"},
        executor,
        handle,
        10,
    )
    assert "2 times" in ambiguous


async def test_tool_error_goes_back_to_agent():
    executor = FakeExecutor()
    output = await execute_tool(
        "read_file", {"path": "missing.py"}, executor, await executor.start(Path("."), "img"), 10
    )
    assert output.startswith("error:")
