"""AgentBench CLI entry point.

Usage:
  python -m agentbench.cli --tasks agentbench/tasks/example.yaml \
      --model claude-sonnet-4-5 --runs-per-task 3
"""

import argparse
import asyncio
import uuid
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
    parser.add_argument(
        "--all-history", action="store_true",
        help="Report over every run ever recorded in --db, not just this invocation. "
             "Mixes separate benchmark sessions into one pass rate — off by default.",
    )
    return parser.parse_args()


async def amain() -> None:
    args = parse_args()
    taskset = TaskSet(**yaml.safe_load(Path(args.tasks).read_text()))
    if args.repo:
        for task in taskset.tasks:
            task.repo_url = args.repo
    executor = DockerSandboxExecutor()
    run_set_id = uuid.uuid4().hex[:12]

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
        store.insert_many(results, run_set_id)
        scope = store.all_runs() if args.all_history else store.runs_for_set(run_set_id)
        report = aggregate(scope)
    finally:
        store.close()

    out = render_html(report, args.out, concurrency=args.concurrency)
    print(f"\nwrote {out} ({len(results)} runs recorded in {args.db}, set {run_set_id})")
    for v in report.variance:
        rate = "n/a (all runs errored)" if v.pass_rate is None else f"{v.pass_rate:.0%}"
        cost = ("unpriced" if v.cost_mean is None
                else f"${v.cost_mean:.4f}±{v.cost_stddev:.4f}")
        line = (f"  {v.task_id} | {v.model}: pass rate {rate} of {v.n_valid} valid runs, "
                f"cost {cost}")
        if v.error_rate:
            line += f", {v.error_rate:.0%} harness errors"
        if v.tampered_count:
            line += f", {v.tampered_count} disqualified (edited tests)"
        print(line)


def main() -> None:
    asyncio.run(amain())


if __name__ == "__main__":
    main()
