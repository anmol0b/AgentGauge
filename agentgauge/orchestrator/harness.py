"""Async fan-out: for each (task x model), run N repeats concurrently,
capped by a semaphore so we don't blow past Docker/API rate limits.
"""

import asyncio
import time

from agentgauge.agent_loop.loop import DEFAULT_SYSTEM_PROMPT
from agentgauge.models.run import RunResult
from agentgauge.models.task import Task
from agentgauge.orchestrator.runner import DEFAULT_IMAGE, run_once
from agentgauge.providers import make_provider
from agentgauge.providers.base import ModelProvider
from agentgauge.sandbox.executor import OutputChunk, OutputSink, SandboxExecutor

# One progress line per run at most this often. A test suite produces far too
# much output to relay, and several runs execute concurrently, so the useful
# signal is "still moving, here is where" rather than the output itself.
PROGRESS_INTERVAL_S = 10.0


def _progress_sink(label: str, interval_s: float = PROGRESS_INTERVAL_S) -> OutputSink:
    """Report that a long command is still producing output.

    Without this a five-minute test run shows nothing until it finishes,
    which is indistinguishable from a hang.
    """
    # None, not 0.0: time.monotonic() is measured from an arbitrary point, so
    # comparing against zero suppresses the first line for interval_s seconds
    # after boot — on a fresh CI runner, exactly when you want to see it.
    last_printed: float | None = None

    def sink(chunk: OutputChunk) -> None:
        nonlocal last_printed
        now = time.monotonic()
        if last_printed is not None and now - last_printed < interval_s:
            return
        lines = [line for line in chunk.data.splitlines() if line.strip()]
        if not lines:
            return
        last_printed = now
        print(f"[{label}] {lines[-1][:100]}", flush=True)

    return sink


async def run_benchmark(
    tasks: list[Task],
    models: list[str],
    runs_per_task: int,
    executor: SandboxExecutor,
    max_concurrency: int = 4,
    image: str | None = None,
    providers: dict[str, ModelProvider] | None = None,
) -> list[RunResult]:
    """Run every (task x model) N times and return every result.

    One provider is built per model and shared across that model's runs, so
    concurrent runs reuse a single HTTP client instead of opening one each.
    Building them up front also means a missing API key fails immediately
    rather than after every run has errored.
    """
    providers = providers or {
        model: make_provider(model, system=DEFAULT_SYSTEM_PROMPT) for model in models
    }
    semaphore = asyncio.Semaphore(max_concurrency)

    async def one(task: Task, model: str, run_index: int) -> RunResult:
        async with semaphore:
            label = f"{task.id} | {model} | run {run_index + 1}/{runs_per_task}"
            result = await run_once(
                task,
                run_index,
                model,
                executor,
                image=image or DEFAULT_IMAGE,
                provider=providers.get(model),
                on_output=_progress_sink(label),
            )
            status = "PASS" if result.passed else "FAIL"
            if result.error:
                status = f"ERROR ({result.error})"
            elif result.tampered_tests:
                status = f"DISQUALIFIED (edited {', '.join(result.tampered_tests)})"
            print(
                f"[{task.id} | {model} | run {run_index + 1}/{runs_per_task}] "
                f"{status} ${result.cost_usd:.4f} {result.latency_s:.0f}s",
                flush=True,
            )
            return result

    coros = [
        one(task, model, i) for task in tasks for model in models for i in range(runs_per_task)
    ]
    return list(await asyncio.gather(*coros))
