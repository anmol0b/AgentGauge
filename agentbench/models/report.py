"""Aggregate report schemas."""

from pydantic import BaseModel

from agentbench.models.run import RunResult


class VarianceReport(BaseModel):
    task_id: str
    model: str
    n_runs: int = 0  # all attempts, including ones the harness errored on
    n_valid: int = 0  # attempts that actually produced a model verdict
    # passes / n_valid. None when every attempt errored — reporting 0% there
    # would blame the model for a harness failure.
    pass_rate: float | None = None
    error_rate: float = 0.0  # harness/infra failures / n_runs
    tampered_count: int = 0  # valid runs disqualified for editing tests
    # cost/latency are means over valid runs only, to match pass_rate.
    # cost_mean is None when no valid run had a priced model.
    n_priced: int = 0
    cost_mean: float | None = None
    cost_stddev: float = 0.0
    latency_mean: float = 0.0
    latency_stddev: float = 0.0


class AggregateReport(BaseModel):
    variance: list[VarianceReport]
    runs: list[RunResult]
