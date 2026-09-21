from agentbench.models.report import AggregateReport, VarianceReport
from agentbench.models.run import RunResult
from agentbench.models.task import Task, TaskSet


def test_task_round_trip():
    taskset = TaskSet(
        tasks=[
            Task(
                id="t1",
                repo_url="https://github.com/example/repo",
                setup_commands=["pip install -e ."],
                failing_test_command="pytest tests/test_x.py",
            )
        ]
    )
    dumped = taskset.model_dump()
    assert TaskSet(**dumped) == taskset
    assert taskset.tasks[0].difficulty is None
    assert taskset.tasks[0].full_test_command == "pytest"


def test_run_result_defaults():
    result = RunResult(
        task_id="t1",
        run_index=0,
        model="claude-sonnet-4-5",
        passed=True,
        tokens_in=100,
        tokens_out=50,
        cost_usd=0.01,
        latency_s=12.5,
        tool_call_count=4,
        diff="+fix",
        regressions=[],
    )
    assert result.error is None
    assert RunResult(**result.model_dump()) == result


def test_variance_report_schema():
    report = AggregateReport(
        variance=[
            VarianceReport(
                task_id="t1",
                model="m",
                pass_rate=2 / 3,
                cost_mean=0.01,
                cost_stddev=0.001,
                latency_mean=10.0,
                latency_stddev=1.5,
            )
        ],
        runs=[],
    )
    assert AggregateReport(**report.model_dump()) == report
