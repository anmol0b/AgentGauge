"""AgentBench CLI entry point.

Usage:
  python -m agentbench.cli --tasks agentbench/tasks/example.yaml \
      --model claude-sonnet-4-5 --runs-per-task 3
"""

import argparse
import asyncio
from pathlib import Path

import yaml

from agentbench.models.task import TaskSet
from agentbench.orchestrator.harness import run_benchmark
from agentbench.reporting.aggregate import aggregate
from agentbench.reporting.html_report import render_html
from agentbench.sandbox.docker_executor import DockerSandboxExecutor
from agentbench.storage.db import RunStore


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="agentbench",
        description="Benchmark AI coding agents on real repo tasks, in Docker sandboxes.",
    )
    parser.add_argument("--tasks", required=True, help="YAML task set file")
    parser.add_argument("--repo", default=None,
                        help="Override repo_url for every task (git URL or local path)")
    parser.add_argument(
        "--model", action="append", required=True,
        help="Model to benchmark (repeatable, e.g. --model claude-sonnet-4-5 --model gpt-4o)",
    )
    parser.add_argument("--runs-per-task", type=int, default=3)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--image", default="agentbench-base:py3.12",
                        help="Sandbox image (must contain test deps; no network in sandbox)")
    parser.add_argument("--db", default="agentbench.db", help="SQLite run history path")
    parser.add_argument("--out", default="report.html", help="HTML report output path")
    return parser.parse_args()


async def amain() -> None:
    args = parse_args()
    taskset = TaskSet(**yaml.safe_load(Path(args.tasks).read_text()))
    if args.repo:
        for task in taskset.tasks:
            task.repo_url = args.repo
    executor = DockerSandboxExecutor()

    results = await run_benchmark(
        tasks=taskset.tasks,
        models=args.model,
        runs_per_task=args.runs_per_task,
        executor=executor,
        max_concurrency=args.concurrency,
        image=args.image,
    )

    store = RunStore(args.db)
    try:
        store.insert_many(results)
        report = aggregate(store.all_runs())
    finally:
        store.close()

    out = render_html(report, args.out)
    print(f"\nwrote {out} ({len(results)} runs recorded in {args.db})")
    for v in report.variance:
        print(f"  {v.task_id} | {v.model}: pass rate {v.pass_rate:.0%}, "
              f"cost ${v.cost_mean:.4f}±{v.cost_stddev:.4f}")


def main() -> None:
    asyncio.run(amain())


if __name__ == "__main__":
    main()
