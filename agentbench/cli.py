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
from agentbench.reporting.aggregate import aggregate, comparable_repos
from agentbench.reporting.html_report import render_html
from agentbench.sandbox.discovery import find_executor_binary
from agentbench.sandbox.executor import SandboxExecutor
from agentbench.storage.db import RunStore


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="agentbench",
        description="Benchmark AI coding agents on real repo tasks, in Docker sandboxes.",
    )
    parser.add_argument("--tasks", required=True, help="YAML task set file")
    parser.add_argument(
        "--repo", default=None, help="Override repo_url for every task (git URL or local path)"
    )
    parser.add_argument(
        "--model",
        action="append",
        required=True,
        help="Model to benchmark (repeatable, e.g. --model claude-sonnet-4-5 --model gpt-4o)",
    )
    parser.add_argument("--runs-per-task", type=int, default=3)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument(
        "--image",
        default="agentbench-base:py3.12",
        help="Sandbox image (must contain test deps; no network in sandbox)",
    )
    parser.add_argument("--db", default="agentbench.db", help="SQLite run history path")
    parser.add_argument("--out", default="report.html", help="HTML report output path")
    parser.add_argument(
        "--executor",
        choices=("auto", "rust", "python"),
        default="auto",
        help="Sandbox executor. auto uses the Rust binary when one is available "
        "and falls back to the in-process Python one.",
    )
    parser.add_argument(
        "--all-history",
        action="store_true",
        help="Report over every run ever recorded in --db, not just this invocation. "
        "Mixes separate benchmark sessions into one pass rate — off by default.",
    )
    return parser.parse_args()


def make_executor(choice: str) -> SandboxExecutor:
    """Resolve --executor to an implementation.

    Both satisfy the same contract (tests/conformance/), so this only picks
    which one runs; it is not a behavioural switch.
    """
    from agentbench.sandbox.docker_executor import DockerSandboxExecutor

    if choice == "python":
        return DockerSandboxExecutor()

    binary = find_executor_binary()
    if binary is None:
        if choice == "rust":
            raise RuntimeError(
                "no executor binary found. Build one with "
                "`cargo build --release` in rust/, or use --executor python."
            )
        return DockerSandboxExecutor()

    from agentbench.sandbox.subprocess_executor import SubprocessExecutor

    print(f"using executor binary: {binary}")
    return SubprocessExecutor(binary)


async def amain() -> None:
    args = parse_args()
    taskset = TaskSet(**yaml.safe_load(Path(args.tasks).read_text()))
    if args.repo:
        for task in taskset.tasks:
            task.repo_url = args.repo
    executor = make_executor(args.executor)
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
    print(f"\nwrote {out} ({len(results)} runs recorded in {args.db}, set {run_set_id})\n")

    for m in report.models:
        ci = m.pass_rate_ci
        if m.pass_rate is None:
            rate = "n/a (every run errored)"
        elif ci is None:
            rate = f"{m.pass_rate:.0%}"
        else:
            rate = f"{m.pass_rate:.0%} (95% CI {ci.low:.0%}-{ci.high:.0%})"
        cost = "unpriced" if m.total_cost is None else f"${m.total_cost:.4f}"
        print(f"  {m.model}: {rate} over {m.n_valid} valid runs, total {cost}")
        notes = []
        if m.error_rate:
            notes.append(f"{m.error_rate:.0%} harness errors")
        if m.tampered_count:
            notes.append(f"{m.tampered_count} disqualified (edited tests)")
        if m.regressed_count:
            notes.append(f"{m.regressed_count} caused regressions")
        if notes:
            print(f"      {', '.join(notes)}")

    if len(report.models) > 1:
        print("\n  Overlapping confidence intervals mean this run does not separate the models.")

    if report.repos:
        print("\n  By repository:")
        for repo in report.repos:
            rate = "n/a" if repo.pass_rate is None else f"{repo.pass_rate:.0%}"
            tools = "—" if repo.tool_calls.mean is None else f"{repo.tool_calls.mean:.0f}"
            print(
                f"    {repo.repo_id}: {rate} over {repo.n_tasks} tasks, {tools} tool calls per run"
            )
        if not comparable_repos(report.repos):
            print(
                "    These repositories ran against different models, "
                "so they cannot be compared to each other."
            )


def main() -> None:
    asyncio.run(amain())


if __name__ == "__main__":
    main()
