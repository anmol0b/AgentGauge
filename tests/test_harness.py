"""Fan-out tests for run_benchmark. run_once is stubbed: what's under test here
is the scheduling, not a single run.
"""

import asyncio
from typing import Any

from agentbench.models.provider import Message, ProviderResponse, ToolSpec
from agentbench.models.run import RunResult, TokenUsage
from agentbench.models.task import Task
from agentbench.orchestrator.harness import run_benchmark
from tests.fakes import FakeExecutor


class StubProvider:
    """Satisfies ModelProvider without doing anything — run_once is stubbed."""

    async def run_agent_turn(
        self, messages: list[Message], tools: list[ToolSpec]
    ) -> ProviderResponse:
        raise AssertionError("run_once is stubbed; this should never be called")

    def compute_cost(self, usage: TokenUsage) -> float | None:
        return 0.0


def _task(task_id):
    return Task(id=task_id, repo_url="/tmp/x", setup_commands=[], failing_test_command="pytest")


def _result(task_id, model, run_index):
    return RunResult(
        task_id=task_id,
        run_index=run_index,
        model=model,
        passed=True,
        tokens_in=1,
        tokens_out=1,
        cost_usd=0.0,
        latency_s=0.1,
        tool_call_count=1,
        diff="",
        regressions=[],
    )


def _record_calls(monkeypatch, calls: list[dict[str, Any]], hook=None):
    async def fake_run_once(task, run_index, model, executor, provider=None, **kwargs):
        calls.append({"task": task.id, "model": model, "run": run_index, "provider": provider})
        if hook:
            await hook()
        return _result(task.id, model, run_index)

    monkeypatch.setattr("agentbench.orchestrator.harness.run_once", fake_run_once)


async def test_fans_out_over_tasks_models_and_repeats(monkeypatch):
    calls: list[dict[str, Any]] = []
    _record_calls(monkeypatch, calls)

    results = await run_benchmark(
        tasks=[_task("t1"), _task("t2")],
        models=["m1", "m2"],
        runs_per_task=3,
        executor=FakeExecutor(),
        providers={"m1": StubProvider(), "m2": StubProvider()},
    )

    assert len(results) == 2 * 2 * 3
    assert len(calls) == 12
    for task_id in ("t1", "t2"):
        for model in ("m1", "m2"):
            runs = sorted(c["run"] for c in calls if c["task"] == task_id and c["model"] == model)
            assert runs == [0, 1, 2]


async def test_one_provider_is_shared_across_a_models_runs(monkeypatch):
    """Concurrent runs should reuse one client, not open one per run."""
    calls: list[dict[str, Any]] = []
    _record_calls(monkeypatch, calls)
    p1, p2 = StubProvider(), StubProvider()

    await run_benchmark(
        tasks=[_task("t1")],
        models=["m1", "m2"],
        runs_per_task=3,
        executor=FakeExecutor(),
        providers={"m1": p1, "m2": p2},
    )

    assert all(c["provider"] is p1 for c in calls if c["model"] == "m1")
    assert all(c["provider"] is p2 for c in calls if c["model"] == "m2")


async def test_providers_are_built_once_per_model(monkeypatch):
    built: list[str] = []

    def record(model, system=None):
        built.append(model)
        return StubProvider()

    monkeypatch.setattr("agentbench.orchestrator.harness.make_provider", record)
    _record_calls(monkeypatch, [])

    await run_benchmark(
        tasks=[_task("t1"), _task("t2")],
        models=["m1", "m2"],
        runs_per_task=3,
        executor=FakeExecutor(),
    )

    assert sorted(built) == ["m1", "m2"]  # not once per run


async def test_concurrency_is_capped(monkeypatch):
    state = {"now": 0, "peak": 0}

    async def hook():
        state["now"] += 1
        state["peak"] = max(state["peak"], state["now"])
        await asyncio.sleep(0)
        state["now"] -= 1

    _record_calls(monkeypatch, [], hook=hook)

    await run_benchmark(
        tasks=[_task("t1")],
        models=["m1"],
        runs_per_task=10,
        executor=FakeExecutor(),
        max_concurrency=3,
        providers={"m1": StubProvider()},
    )

    assert state["peak"] <= 3


async def test_missing_api_key_fails_before_any_run(monkeypatch):
    """Better to stop now than to report N runs that all errored identically."""

    def explode(model, system=None):
        raise RuntimeError("ANTHROPIC_API_KEY is not set")

    monkeypatch.setattr("agentbench.orchestrator.harness.make_provider", explode)
    calls: list[dict[str, Any]] = []
    _record_calls(monkeypatch, calls)

    try:
        await run_benchmark(
            tasks=[_task("t1")],
            models=["claude-sonnet-4-5"],
            runs_per_task=3,
            executor=FakeExecutor(),
        )
        raise AssertionError("expected the missing key to raise")
    except RuntimeError as exc:
        assert "ANTHROPIC_API_KEY" in str(exc)
    assert calls == []  # nothing was attempted


# --- progress reporting ---------------------------------------------------


def test_progress_sink_reports_the_latest_line(capsys):
    """A five-minute test run showing nothing is indistinguishable from a hang."""
    from agentbench.orchestrator.harness import _progress_sink
    from agentbench.sandbox.protocol import OutputChunk

    sink = _progress_sink("task | model | run 1/3", interval_s=0)
    sink(OutputChunk(kind="stdout", data="collected 5 items\ntests/test_a.py .\n"))

    printed = capsys.readouterr().out
    assert "task | model | run 1/3" in printed
    assert "tests/test_a.py ." in printed


def test_progress_sink_throttles(capsys):
    """Relaying a test suite's output verbatim, times four concurrent runs,
    would be unreadable."""
    from agentbench.orchestrator.harness import _progress_sink
    from agentbench.sandbox.protocol import OutputChunk

    sink = _progress_sink("label", interval_s=60)
    for i in range(10):
        sink(OutputChunk(kind="stdout", data=f"line {i}\n"))

    assert capsys.readouterr().out.count("label") == 1


def test_progress_sink_ignores_blank_output(capsys):
    from agentbench.orchestrator.harness import _progress_sink
    from agentbench.sandbox.protocol import OutputChunk

    sink = _progress_sink("label", interval_s=0)
    sink(OutputChunk(kind="stdout", data="\n   \n"))

    assert capsys.readouterr().out == ""


def test_progress_sink_reports_immediately_on_a_freshly_booted_machine(capsys, monkeypatch):
    """time.monotonic() counts from an arbitrary point. Treating 0.0 as
    "never printed" suppressed the first line for interval_s seconds after
    boot — invisible on a laptop with days of uptime, and caught by CI."""
    from agentbench.orchestrator import harness
    from agentbench.sandbox.protocol import OutputChunk

    # patch the stdlib function harness calls, not an attribute of harness
    monkeypatch.setattr("time.monotonic", lambda: 0.5)
    sink = harness._progress_sink("label", interval_s=60)
    sink(OutputChunk(kind="stdout", data="first line\n"))

    assert "first line" in capsys.readouterr().out
