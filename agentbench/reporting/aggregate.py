"""Aggregation: pass rate, variance, and per-model summaries.

Two rules run through all of it:

- LLM outputs are non-deterministic, so spread is reported rather than hidden.
  With the small N a benchmark like this runs, a bare pass rate reads as more
  precise than it is, so proportions carry a 95% confidence interval.
- A run the harness failed to complete (Docker fault, rate limit, bad setup
  command) is not a model failure. Those are counted as `error_rate` and
  excluded from the pass-rate denominator.
"""

import math
import statistics
from collections import defaultdict

from agentbench.models.report import (
    AggregateReport,
    DifficultyBreakdown,
    Interval,
    ModelSummary,
    Stat,
    VarianceReport,
)
from agentbench.models.run import RunResult

_Z = 1.96  # 95%


def aggregate(runs: list[RunResult]) -> AggregateReport:
    by_task_model: dict[tuple[str, str], list[RunResult]] = defaultdict(list)
    by_model: dict[str, list[RunResult]] = defaultdict(list)
    by_difficulty: dict[tuple[str, str], list[RunResult]] = defaultdict(list)
    for run in runs:
        by_task_model[(run.task_id, run.model)].append(run)
        by_model[run.model].append(run)
        if run.difficulty:
            by_difficulty[(run.model, run.difficulty)].append(run)

    return AggregateReport(
        variance=[
            _task_model(task_id, model, group)
            for (task_id, model), group in sorted(by_task_model.items())
        ],
        models=[_model(model, group) for model, group in sorted(by_model.items())],
        by_difficulty=[
            DifficultyBreakdown(
                model=model,
                difficulty=difficulty,
                n_valid=len(_valid(group)),
                pass_rate=_rate(_valid(group)),
            )
            for (model, difficulty), group in sorted(
                by_difficulty.items(), key=lambda kv: (kv[0][0], _DIFFICULTY_ORDER(kv[0][1]))
            )
        ],
        runs=runs,
    )


def _DIFFICULTY_ORDER(difficulty: str) -> tuple[int, str]:
    known = ["easy", "medium", "hard"]
    return (known.index(difficulty) if difficulty in known else len(known), difficulty)


def _valid(group: list[RunResult]) -> list[RunResult]:
    """Runs that produced a model verdict rather than a harness error."""
    return [r for r in group if r.error is None]


def _rate(valid: list[RunResult]) -> float | None:
    return (sum(r.passed for r in valid) / len(valid)) if valid else None


def _task_model(task_id: str, model: str, group: list[RunResult]) -> VarianceReport:
    valid = _valid(group)
    priced = [r.cost_usd for r in valid if r.cost_usd is not None]
    return VarianceReport(
        task_id=task_id,
        model=model,
        n_runs=len(group),
        n_valid=len(valid),
        pass_rate=_rate(valid),
        pass_rate_ci=_wilson(sum(r.passed for r in valid), len(valid)),
        error_rate=(len(group) - len(valid)) / len(group) if group else 0.0,
        tampered_count=sum(1 for r in valid if r.tampered_tests),
        regressed_count=sum(1 for r in valid if r.regressions),
        n_priced=len(priced),
        cost_mean=statistics.fmean(priced) if priced else None,
        cost_stddev=_stddev(priced),
        latency_mean=statistics.fmean(r.latency_s for r in valid) if valid else None,
        latency_stddev=_stddev([r.latency_s for r in valid]),
        tool_calls=_stat([r.tool_call_count for r in valid]),
        tokens_in=_stat([r.tokens_in for r in valid]),
        tokens_out=_stat([r.tokens_out for r in valid]),
    )


def _model(model: str, group: list[RunResult]) -> ModelSummary:
    valid = _valid(group)
    unpriced = [r for r in valid if r.cost_usd is None]
    return ModelSummary(
        model=model,
        n_runs=len(group),
        n_valid=len(valid),
        pass_rate=_rate(valid),
        pass_rate_ci=_wilson(sum(r.passed for r in valid), len(valid)),
        error_rate=(len(group) - len(valid)) / len(group) if group else 0.0,
        tampered_count=sum(1 for r in valid if r.tampered_tests),
        regressed_count=sum(1 for r in valid if r.regressions),
        # a partial total would read as the cost of the whole set
        total_cost=(
            None if unpriced else sum(r.cost_usd for r in valid)
        ),
        n_unpriced=len(unpriced),
        latency_mean=statistics.fmean(r.latency_s for r in valid) if valid else None,
        tool_calls=_stat([r.tool_call_count for r in valid]),
        tokens_in=_stat([r.tokens_in for r in valid]),
        tokens_out=_stat([r.tokens_out for r in valid]),
    )


def _stat(values: list[float]) -> Stat:
    if not values:
        return Stat()
    return Stat(mean=statistics.fmean(values), stddev=_stddev(values), n=len(values))


def _stddev(values: list[float]) -> float:
    return statistics.stdev(values) if len(values) > 1 else 0.0


def _wilson(successes: int, n: int) -> Interval | None:
    """Wilson score interval — honest about small samples.

    Three-for-three is not 100%±0: this reports roughly 44%-100%, which is
    what three runs actually tell you.
    """
    if n == 0:
        return None
    p = successes / n
    denominator = 1 + _Z**2 / n
    centre = (p + _Z**2 / (2 * n)) / denominator
    margin = _Z * math.sqrt(p * (1 - p) / n + _Z**2 / (4 * n**2)) / denominator
    return Interval(low=max(0.0, centre - margin), high=min(1.0, centre + margin))
