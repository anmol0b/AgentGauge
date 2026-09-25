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


class ToolCallRecord(BaseModel):
    """One tool call and what it returned, abbreviated."""

    name: str
    # rendered and clipped, not replayable: this is a record of what happened
    arguments: dict[str, str]
    output_excerpt: str
    output_chars: int  # true length, so clipping is never silent


class TurnRecord(BaseModel):
    index: int  # 1-based
    text: str  # the assistant's prose, clipped
    # The loop nudges a model that stops without editing anything. Knowing it
    # was prodded explains a run that otherwise looks merely slow.
    nudged: bool = False
    tool_calls: list[ToolCallRecord] = []


class Transcript(BaseModel):
    """What the agent did, small enough to keep for every run.

    Full transcripts are far too large to store for every run: tool output is
    capped at 30k chars and a run can take 25 turns. Everything here is
    clipped, and every clip records the true size it came from.
    """

    turns: list[TurnRecord] = []
    total_turns: int = 0  # before any elision
    elided_turns: int = 0  # dropped from the middle to fit the size budget


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
    # What the agent did. None means not recorded — every pre-v3 row — which
    # is not the same as an empty transcript.
    transcript: Transcript | None = None
