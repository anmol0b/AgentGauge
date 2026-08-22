"""Aggregate report schemas."""

from pydantic import BaseModel

from agentbench.models.run import RunResult


class VarianceReport(BaseModel):
    task_id: str
    model: str
    pass_rate: float  # across N runs
    cost_mean: float
    cost_stddev: float
    latency_mean: float
    latency_stddev: float


class AggregateReport(BaseModel):
    variance: list[VarianceReport]
    runs: list[RunResult]
