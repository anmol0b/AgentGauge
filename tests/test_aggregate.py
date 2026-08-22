import pytest

from agentbench.models.run import RunResult
from agentbench.reporting.aggregate import aggregate


def _run(task_id="t1", model="m1", run_index=0, passed=True, cost=0.01, latency=10.0):
    return RunResult(
        task_id=task_id, run_index=run_index, model=model, passed=passed,
        tokens_in=100, tokens_out=50, cost_usd=cost, latency_s=latency,
        tool_call_count=3, diff="", regressions=[],
    )


def test_aggregate_groups_by_task_and_model():
    runs = [
        _run(run_index=0, passed=True, cost=0.01, latency=10.0),
        _run(run_index=1, passed=False, cost=0.03, latency=20.0),
        _run(run_index=2, passed=True, cost=0.02, latency=15.0),
        _run(model="m2", run_index=0, passed=True, cost=0.05, latency=30.0),
    ]
    report = aggregate(runs)
    assert len(report.variance) == 2

    v1 = next(v for v in report.variance if v.model == "m1")
    assert v1.pass_rate == pytest.approx(2 / 3)
    assert v1.cost_mean == pytest.approx(0.02)
    assert v1.cost_stddev == pytest.approx(0.01)
    assert v1.latency_mean == pytest.approx(15.0)
    assert v1.latency_stddev == pytest.approx(5.0)

    v2 = next(v for v in report.variance if v.model == "m2")
    assert v2.pass_rate == 1.0
    assert v2.cost_stddev == 0.0  # single run: no fake precision


def test_aggregate_empty():
    assert aggregate([]).variance == []
