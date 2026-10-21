"""Single-run end-to-end smoke: one task, one model, one run.

Usage: ANTHROPIC_API_KEY=... .venv/bin/python scripts/single_run_smoke.py [model]
Requires: Docker daemon, ANTHROPIC_API_KEY, agentgauge-base:py3.12 image
(build with: agentgauge image build)
"""

import asyncio
import sys
from pathlib import Path

import yaml

from agentgauge.models.task import TaskSet
from agentgauge.orchestrator.runner import run_once
from agentgauge.sandbox.docker_executor import DockerSandboxExecutor


async def main() -> None:
    model = sys.argv[1] if len(sys.argv) > 1 else "claude-sonnet-4-5"
    taskset = TaskSet(**yaml.safe_load(Path("agentgauge/tasks/example.yaml").read_text()))
    task = taskset.tasks[0]
    print(f"running task {task.id!r} with {model} ...")
    result = await run_once(
        task,
        run_index=0,
        model=model,
        executor=DockerSandboxExecutor(),
        image="agentgauge-base:py3.12",
    )
    print(
        f"passed={result.passed} cost=${result.cost_usd:.4f} "
        f"latency={result.latency_s:.1f}s tool_calls={result.tool_call_count} "
        f"tokens={result.tokens_in}+{result.tokens_out}"
    )
    print(f"regressions={result.regressions} error={result.error}")
    print("--- diff ---")
    print(result.diff)


if __name__ == "__main__":
    asyncio.run(main())
