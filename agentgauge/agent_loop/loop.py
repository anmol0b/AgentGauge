"""The agent tool-calling loop: provider turn -> execute tools -> feed back,
until the model stops or max_turns is hit.
"""

from pydantic import BaseModel

from agentgauge.agent_loop.tools import TOOLS, execute_tool
from agentgauge.models.provider import Message
from agentgauge.models.run import TokenUsage
from agentgauge.providers.base import ModelProvider
from agentgauge.sandbox.executor import OutputSink, SandboxExecutor, SandboxHandle

DEFAULT_SYSTEM_PROMPT = (
    "You are a coding agent working in a sandboxed repository. "
    "Fix the failing test described by the user with the smallest correct change.\n"
    "Rules:\n"
    "- You MUST apply your fix with the edit or write_file tool. Never just "
    "describe the fix in prose — describing it changes nothing.\n"
    "- Do not modify tests unless they are clearly wrong.\n"
    "- After editing, run the failing test with run_tests to confirm it passes.\n"
    "- Only stop calling tools once the failing test passes, then briefly "
    "summarize what you changed."
)

_MUTATING_TOOLS = {"write_file", "edit"}

_NO_EDIT_NUDGE = (
    "You have not modified any files yet. Describing the fix is not enough — "
    "apply it now using the edit or write_file tool, then re-run the failing test."
)


class AgentLoopResult(BaseModel):
    messages: list[Message]
    usage: TokenUsage
    cost_usd: float | None  # None if any turn used an unpriced model
    tool_call_count: int
    stop_reason: str  # "end_turn" | "max_turns" | "max_tokens" | "error"


class AgentLoopError(Exception):
    """A loop that failed part-way, carrying what it had already done.

    Tokens spent on the turns before the failure were billed, so a run that
    dies on turn six must still report them rather than costing $0.
    """

    def __init__(self, partial: AgentLoopResult) -> None:
        super().__init__("agent loop failed")
        self.partial = partial


async def run_agent_loop(
    provider: ModelProvider,
    executor: SandboxExecutor,
    handle: SandboxHandle,
    instruction: str,
    max_turns: int = 25,
    exec_timeout_s: int = 120,
    on_output: OutputSink | None = None,
) -> AgentLoopResult:
    messages: list[Message] = [Message(role="user", content=instruction)]
    total_usage = TokenUsage()
    total_cost: float | None = 0.0
    tool_call_count = 0
    made_mutation = False

    try:
        for _ in range(max_turns):
            response = await provider.run_agent_turn(messages, TOOLS)
            messages.extend(response.messages)
            total_usage.tokens_in += response.usage.tokens_in
            total_usage.tokens_out += response.usage.tokens_out
            turn_cost = provider.compute_cost(response.usage)
            if turn_cost is None:
                total_cost = None  # unpriced model: no honest total exists
            elif total_cost is not None:
                total_cost += turn_cost

            if response.stop_reason != "tool_use" or not response.tool_calls:
                if not made_mutation:
                    # Weak models often describe the fix in prose and stop without
                    # editing anything. Nudge them back to work instead of ending.
                    messages.append(Message(role="user", content=_NO_EDIT_NUDGE))
                    continue
                return AgentLoopResult(
                    messages=messages,
                    usage=total_usage,
                    cost_usd=total_cost,
                    tool_call_count=tool_call_count,
                    stop_reason=response.stop_reason,
                )

            tool_call_count += len(response.tool_calls)
            for call in response.tool_calls:
                output = await execute_tool(
                    call.name, call.arguments, executor, handle, exec_timeout_s
                )
                if call.name in _MUTATING_TOOLS and output.startswith(("wrote ", "edited ")):
                    # only a *successful* edit counts — a failed old_string match
                    # shouldn't stop the no-edit nudge
                    made_mutation = True
                messages.append(Message(role="tool", content=output, tool_call_id=call.id))
    except Exception as exc:
        raise AgentLoopError(
            AgentLoopResult(
                messages=messages,
                usage=total_usage,
                cost_usd=total_cost,
                tool_call_count=tool_call_count,
                stop_reason="error",
            )
        ) from exc

    return AgentLoopResult(
        messages=messages,
        usage=total_usage,
        cost_usd=total_cost,
        tool_call_count=tool_call_count,
        stop_reason="max_turns",
    )
