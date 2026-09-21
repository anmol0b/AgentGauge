"""Async fan-out: for each (task x model), run N repeats concurrently,
capped by a semaphore so we don't blow past Docker/API rate limits.
"""

import asyncio

from agentbench.agent_loop.loop import DEFAULT_SYSTEM_PROMPT
from agentbench.models.run import RunResult
from agentbench.models.task import Task
from agentbench.orchestrator.runner import run_once
from agentbench.providers import make_provider
from agentbench.providers.base import ModelProvider
from agentbench.sandbox.executor import SandboxExecutor


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
            kwargs = {"image": image} if image else {}
            result = await run_once(
                task, run_index, model, executor, provider=providers.get(model), **kwargs
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
        one(task, model, i)
        for task in tasks
        for model in models
        for i in range(runs_per_task)
    ]
    return list(await asyncio.gather(*coros))
