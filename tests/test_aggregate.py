import pytest

from agentbench.models.run import RunResult
from agentbench.reporting.aggregate import aggregate, comparable_repos


def _run(
    task_id="t1",
    model="m1",
    run_index=0,
    passed=True,
    cost=0.01,
    latency=10.0,
    error=None,
    tampered=None,
):
    return RunResult(
        task_id=task_id,
        run_index=run_index,
        model=model,
        passed=passed,
        tokens_in=100,
        tokens_out=50,
        cost_usd=cost,
        latency_s=latency,
        tool_call_count=3,
        diff="",
        regressions=[],
        error=error,
        tampered_tests=tampered or [],
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
    assert v1.n_runs == 3 and v1.n_valid == 3
    assert v1.error_rate == 0.0

    v2 = next(v for v in report.variance if v.model == "m2")
    assert v2.pass_rate == 1.0
    assert v2.cost_stddev == 0.0  # single run: no fake precision


def test_aggregate_empty():
    assert aggregate([]).variance == []


def test_harness_errors_excluded_from_pass_rate():
    """A Docker/API failure is not a model failure — it must not depress the score."""
    runs = [
        _run(run_index=0, passed=True),
        _run(run_index=1, passed=False, error="DockerException: boom"),
        _run(run_index=2, passed=False, error="RateLimitError: 429"),
    ]
    v = aggregate(runs).variance[0]
    assert v.n_runs == 3 and v.n_valid == 1
    assert v.pass_rate == 1.0  # the one run that actually ran, passed
    assert v.error_rate == pytest.approx(2 / 3)


def test_all_errored_reports_no_pass_rate():
    """Reporting 0% when nothing ever ran would blame the model for our outage."""
    v = aggregate([_run(error="boom"), _run(run_index=1, error="boom")]).variance[0]
    assert v.pass_rate is None
    assert v.error_rate == 1.0
    assert v.n_valid == 0


def test_errored_runs_excluded_from_cost_and_latency_means():
    runs = [
        _run(run_index=0, cost=0.02, latency=10.0),
        _run(run_index=1, cost=999.0, latency=999.0, error="boom"),
    ]
    v = aggregate(runs).variance[0]
    assert v.cost_mean == pytest.approx(0.02)
    assert v.latency_mean == pytest.approx(10.0)


def test_tampered_runs_are_counted():
    runs = [
        _run(run_index=0, passed=True),
        _run(run_index=1, passed=False, tampered=["test_calc.py"]),
    ]
    v = aggregate(runs).variance[0]
    assert v.tampered_count == 1
    assert v.pass_rate == 0.5  # disqualified run counts against the model


def test_pass_rate_carries_a_confidence_interval():
    """3/3 is not 100% +/- 0 — the interval has to say what N supports."""
    v = aggregate([_run(run_index=i) for i in range(3)]).variance[0]
    assert v.pass_rate == 1.0
    assert v.pass_rate_ci is not None
    assert v.pass_rate_ci.high == 1.0
    assert v.pass_rate_ci.low < 0.5  # three runs support very little


def test_interval_narrows_with_more_runs():
    few = aggregate([_run(run_index=i) for i in range(3)]).variance[0]
    many = aggregate([_run(run_index=i) for i in range(50)]).variance[0]
    assert few.pass_rate_ci is not None and many.pass_rate_ci is not None
    assert many.pass_rate_ci.low > few.pass_rate_ci.low


def test_model_summary_totals_cost_across_tasks():
    runs = [
        _run(task_id="t1", cost=0.01),
        _run(task_id="t2", cost=0.02),
    ]
    summary = aggregate(runs).models[0]
    assert summary.total_cost == pytest.approx(0.03)
    assert summary.n_valid == 2


def test_model_cost_total_withheld_when_any_run_is_unpriced():
    """A partial total would read as the cost of the whole set."""
    runs = [_run(task_id="t1", cost=0.01), _run(task_id="t2")]
    runs[1].cost_usd = None
    summary = aggregate(runs).models[0]
    assert summary.total_cost is None
    assert summary.n_unpriced == 1


def test_difficulty_breakdown_orders_easy_to_hard():
    runs = [
        _run(run_index=0, task_id="h"),
        _run(run_index=1, task_id="e"),
    ]
    runs[0].difficulty, runs[1].difficulty = "hard", "easy"
    order = [d.difficulty for d in aggregate(runs).by_difficulty]
    assert order == ["easy", "hard"]


def test_regressions_are_counted_per_model():
    runs = [_run(run_index=0), _run(run_index=1)]
    runs[1].regressions = ["pkg::test_other"]
    assert aggregate(runs).models[0].regressed_count == 1


# --- repository summaries -------------------------------------------------
# Scoring a repository rather than a model: "is this codebase easy for an
# agent to work in". Deliberately a profile, not one number.


def _repo_run(repo_id, task_id, **overrides):
    run = _run(task_id=task_id, **overrides)
    run.repo_id = repo_id
    return run


def test_runs_are_grouped_by_repository():
    runs = [
        _repo_run("alpha", "a1"),
        _repo_run("alpha", "a2", run_index=1),
        _repo_run("beta", "b1", run_index=2, passed=False),
    ]
    repos = {r.repo_id: r for r in aggregate(runs).repos}
    assert set(repos) == {"alpha", "beta"}
    assert repos["alpha"].n_tasks == 2
    assert repos["alpha"].pass_rate == 1.0
    assert repos["beta"].pass_rate == 0.0


def test_a_repo_profile_carries_effort_and_damage_not_just_pass_rate():
    """Passing often while taking 30 tool calls and breaking a neighbouring
    test is not the same repo as passing often, cheaply and cleanly."""
    runs = [_repo_run("alpha", "a1"), _repo_run("alpha", "a2", run_index=1)]
    runs[0].tool_call_count = 30
    runs[1].tool_call_count = 10
    runs[1].regressions = ["pkg::test_other"]

    repo = aggregate(runs).repos[0]
    assert repo.tool_calls.mean == 20
    assert repo.regressed_count == 1
    assert repo.tampered_count == 0


def test_repo_difficulty_mix_counts_tasks_not_runs():
    """Three runs of one easy task is not three easy tasks."""
    runs = [_repo_run("alpha", "a1", run_index=i) for i in range(3)]
    for run in runs:
        run.difficulty = "easy"
    runs.append(_repo_run("alpha", "a2", run_index=9))
    runs[-1].difficulty = "hard"

    assert aggregate(runs).repos[0].difficulty_mix == {"easy": 1, "hard": 1}


def test_repo_errors_leave_the_pass_rate_denominator():
    runs = [_repo_run("alpha", "a1"), _repo_run("alpha", "a2", run_index=1, error="boom")]
    repo = aggregate(runs).repos[0]
    assert repo.pass_rate == 1.0
    assert repo.n_valid == 1 and repo.n_runs == 2
    assert repo.error_rate == 0.5


def test_runs_without_a_repo_id_are_left_out():
    """Pre-v2 rows have none; inventing one would put them in a bucket they
    were never measured in."""
    assert aggregate([_run()]).repos == []


def test_repos_are_comparable_only_when_the_same_models_ran():
    """A repo evaluated only with a weak model looks hostile to agents when
    the difference is the model."""
    same = aggregate(
        [_repo_run("alpha", "a1", model="m1"), _repo_run("beta", "b1", model="m1")]
    ).repos
    assert comparable_repos(same)

    different = aggregate(
        [_repo_run("alpha", "a1", model="m1"), _repo_run("beta", "b1", model="m2")]
    ).repos
    assert not comparable_repos(different)
