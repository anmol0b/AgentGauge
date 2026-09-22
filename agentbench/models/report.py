"""Aggregate report schemas."""

from pydantic import BaseModel

from agentbench.models.run import RunResult


class Interval(BaseModel):
    """A 95% confidence interval on a proportion."""

    low: float
    high: float


class Stat(BaseModel):
    """mean ± stddev over however many runs supplied a value."""

    mean: float | None = None
    stddev: float = 0.0
    n: int = 0


class VarianceReport(BaseModel):
    task_id: str
    model: str
    n_runs: int = 0  # all attempts, including ones the harness errored on
    n_valid: int = 0  # attempts that actually produced a model verdict
    # passes / n_valid. None when every attempt errored — reporting 0% there
    # would blame the model for a harness failure.
    pass_rate: float | None = None
    # With N=3 a pass rate is three coin flips; the interval says so.
    pass_rate_ci: Interval | None = None
    error_rate: float = 0.0  # harness/infra failures / n_runs
    tampered_count: int = 0  # valid runs disqualified for editing tests
    regressed_count: int = 0  # valid runs that broke a previously passing test
    # cost/latency/effort are over valid runs only, to match pass_rate.
    # cost is None when no valid run used a priced model.
    n_priced: int = 0
    cost_mean: float | None = None
    cost_stddev: float = 0.0
    latency_mean: float | None = None
    latency_stddev: float = 0.0
    tool_calls: Stat = Stat()
    tokens_in: Stat = Stat()
    tokens_out: Stat = Stat()


class ModelSummary(BaseModel):
    """One model across every task — the leaderboard row."""

    model: str
    n_runs: int = 0
    n_valid: int = 0
    pass_rate: float | None = None
    pass_rate_ci: Interval | None = None
    error_rate: float = 0.0
    tampered_count: int = 0
    regressed_count: int = 0
    total_cost: float | None = None  # None if any valid run was unpriced
    n_unpriced: int = 0
    latency_mean: float | None = None
    tool_calls: Stat = Stat()
    tokens_in: Stat = Stat()
    tokens_out: Stat = Stat()


class RepoSummary(BaseModel):
    """One repository across every task and model run against it.

    Deliberately a profile rather than a single "AI-friendliness score". A
    repo where agents pass often but take 30 tool calls and break a
    neighbouring test is not the same as one where they pass often, quickly
    and cleanly, and collapsing those into one number hides the thing you
    would actually want to act on.
    """

    repo_id: str
    n_tasks: int = 0
    n_runs: int = 0
    n_valid: int = 0
    pass_rate: float | None = None
    pass_rate_ci: Interval | None = None
    error_rate: float = 0.0
    # how much work a fix took, and what it cost to get there
    tool_calls: Stat = Stat()
    tokens_in: Stat = Stat()
    n_priced: int = 0
    cost_mean: float | None = None
    # how often working here goes wrong in ways a pass rate does not show
    regressed_count: int = 0
    tampered_count: int = 0
    # Two repos are only comparable when the same models were run against a
    # comparable spread of tasks. Both are carried so the report can say when
    # they are not, instead of ranking things that cannot be ranked.
    models: list[str] = []
    difficulty_mix: dict[str, int] = {}


class DifficultyBreakdown(BaseModel):
    model: str
    difficulty: str
    n_valid: int = 0
    pass_rate: float | None = None


class AggregateReport(BaseModel):
    variance: list[VarianceReport]
    models: list[ModelSummary] = []
    repos: list[RepoSummary] = []
    by_difficulty: list[DifficultyBreakdown] = []
    runs: list[RunResult]
