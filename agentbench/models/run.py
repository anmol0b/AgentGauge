"""Run result and metrics schemas."""

from pydantic import BaseModel


class TokenUsage(BaseModel):
    tokens_in: int = 0  # uncached input tokens
    tokens_out: int = 0
    # Cached input is billed at different rates than fresh input. Dropping
    # these undercounts the cost of any run that hits the cache.
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0

    @property
    def total_in(self) -> int:
        return self.tokens_in + self.cache_write_tokens + self.cache_read_tokens


class RunResult(BaseModel):
    task_id: str
    # denormalized from Task so the database is self-describing:
    # reports are built from stored runs, not from the task YAML
    repo_id: str | None = None
    difficulty: str | None = None
    run_index: int  # which of N repeats
    model: str
    passed: bool
    tokens_in: int
    tokens_out: int
    # None when the model has no entry in pricing.yaml — an unpriced run
    # reports no cost rather than a figure guessed from another model.
    cost_usd: float | None
    latency_s: float
    # wall-clock split: sandbox setup + baseline / agent loop / grading.
    # 0.0 on rows recorded before this was tracked, and on phases a
    # run errored before reaching.
    setup_s: float = 0.0
    agent_s: float = 0.0
    grading_s: float = 0.0
    tool_call_count: int
    diff: str
    # other tests the fix broke. None means detection was unavailable
    # (no junit report, unparseable, or a non-pytest suite) — which is
    # not the same as [], meaning it ran and found none.
    regressions: list[str] | None = None
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
