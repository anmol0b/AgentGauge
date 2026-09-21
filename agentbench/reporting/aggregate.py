"""Aggregation: pass rate, cost/latency variance per (task, model).

LLM outputs are non-deterministic — variance is reported explicitly,
never hidden behind a single number.

A run that the harness failed to complete (Docker hiccup, rate limit, bad setup
command) is not a model failure. Those are counted separately as `error_rate`
and excluded from the pass-rate denominator, so infrastructure noise cannot
quietly depress a model's score.
"""

import statistics
from collections import defaultdict

from agentbench.models.report import AggregateReport, VarianceReport
from agentbench.models.run import RunResult


def aggregate(runs: list[RunResult]) -> AggregateReport:
    groups: dict[tuple[str, str], list[RunResult]] = defaultdict(list)
    for run in runs:
        groups[(run.task_id, run.model)].append(run)

    variance = [
        _summarize(task_id, model, group)
        for (task_id, model), group in sorted(groups.items())
    ]
    return AggregateReport(variance=variance, runs=runs)


def _summarize(task_id: str, model: str, group: list[RunResult]) -> VarianceReport:
    valid = [r for r in group if r.error is None]
    errored = len(group) - len(valid)
    return VarianceReport(
        task_id=task_id,
        model=model,
        n_runs=len(group),
        n_valid=len(valid),
        pass_rate=(sum(r.passed for r in valid) / len(valid)) if valid else None,
        error_rate=errored / len(group) if group else 0.0,
        tampered_count=sum(1 for r in valid if r.tampered_tests),
        cost_mean=statistics.fmean(r.cost_usd for r in valid) if valid else 0.0,
        cost_stddev=_stddev([r.cost_usd for r in valid]),
        latency_mean=statistics.fmean(r.latency_s for r in valid) if valid else 0.0,
        latency_stddev=_stddev([r.latency_s for r in valid]),
    )


def _stddev(values: list[float]) -> float:
    return statistics.stdev(values) if len(values) > 1 else 0.0
