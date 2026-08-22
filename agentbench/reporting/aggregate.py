"""Aggregation: pass rate, cost/latency variance per (task, model).

LLM outputs are non-deterministic — variance is reported explicitly,
never hidden behind a single number.
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
        VarianceReport(
            task_id=task_id,
            model=model,
            pass_rate=sum(r.passed for r in group) / len(group),
            cost_mean=statistics.fmean(r.cost_usd for r in group),
            cost_stddev=_stddev([r.cost_usd for r in group]),
            latency_mean=statistics.fmean(r.latency_s for r in group),
            latency_stddev=_stddev([r.latency_s for r in group]),
        )
        for (task_id, model), group in sorted(groups.items())
    ]
    return AggregateReport(variance=variance, runs=runs)


def _stddev(values: list[float]) -> float:
    return statistics.stdev(values) if len(values) > 1 else 0.0
