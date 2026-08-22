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
