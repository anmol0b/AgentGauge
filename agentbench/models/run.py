"""Run result and metrics schemas."""

from pydantic import BaseModel


class TokenUsage(BaseModel):
    tokens_in: int = 0
    tokens_out: int = 0


class RunResult(BaseModel):
    task_id: str
    run_index: int  # which of N repeats
    model: str
    passed: bool
    tokens_in: int
    tokens_out: int
    cost_usd: float
    latency_s: float
    tool_call_count: int
    diff: str
    regressions: list[str]  # other tests that broke
    error: str | None = None
    # why the agent loop ended: end_turn | max_turns | max_tokens | error.
    # None for runs recorded before this was tracked.
    stop_reason: str | None = None
    # test files the agent modified. Non-empty means the run is disqualified:
    # a model that edits the test it was asked to fix can pass by cheating.
    # None means the check never ran (run errored early, or a pre-v2 row) —
    # distinct from [], which means it ran and found nothing.
    tampered_tests: list[str] | None = None
    # which CLI invocation produced this run; "" for pre-existing rows
    run_set_id: str = ""
