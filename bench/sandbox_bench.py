"""Measure sandbox operations, per executor.

This benchmarks the sandbox and nothing else. A full run's wall-clock is
dominated by model inference — on the recorded runs, sandbox work is a few
percent — so an end-to-end comparison would mostly measure the model and
hide whatever the executor actually costs. These are the operations the
harness performs, timed directly.

Needs Docker. Needs no API key and no model.

    python bench/sandbox_bench.py                    # both executors
    python bench/sandbox_bench.py --iterations 20
    python bench/sandbox_bench.py --executor rust --json results.json
"""

import argparse
import asyncio
import json
import statistics
import sys
import tempfile
import time
from collections.abc import Awaitable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from agentgauge.sandbox.discovery import find_executor_binary  # noqa: E402
from agentgauge.sandbox.protocol import HostPathRepo  # noqa: E402

IMAGE = "agentgauge-base:py3.12"
BLOB = b"x" * 64 * 1024


def build_executor(name: str):
    if name == "python":
        from agentgauge.sandbox.docker_executor import DockerSandboxExecutor

        return DockerSandboxExecutor()

    binary = find_executor_binary()
    if binary is None:
        raise SystemExit("no executor binary; run `cargo build --release` in rust/")
    from agentgauge.sandbox.subprocess_executor import SubprocessExecutor

    return SubprocessExecutor(binary)


async def timed(awaitable: Awaitable[object]) -> float:
    """Milliseconds to await `awaitable`. Building the coroutine is free."""
    start = time.perf_counter()
    await awaitable
    return (time.perf_counter() - start) * 1000


async def measure(name: str, iterations: int) -> dict[str, list[float]]:
    executor = build_executor(name)
    samples: dict[str, list[float]] = {
        "start": [],
        "exec_trivial": [],
        "exec_output_1k_lines": [],
        "write_64kb": [],
        "read_64kb": [],
        "teardown": [],
    }

    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "repo"
        repo.mkdir()
        (repo / "seed.txt").write_text("seed\n")

        for _ in range(iterations):
            handle = None
            start_began = time.perf_counter()
            try:
                handle = await executor.start(HostPathRepo(path=str(repo)), IMAGE)
                samples["start"].append((time.perf_counter() - start_began) * 1000)

                samples["exec_trivial"].append(await timed(executor.exec(handle, ["true"], 30)))
                samples["exec_output_1k_lines"].append(
                    await timed(executor.exec(handle, ["seq", "1", "1000"], 30))
                )
                samples["write_64kb"].append(
                    await timed(executor.write_bytes(handle, "blob.bin", BLOB))
                )
                samples["read_64kb"].append(await timed(executor.read_bytes(handle, "blob.bin")))
            finally:
                if handle is not None:
                    samples["teardown"].append(await timed(executor.teardown(handle)))

    closer = getattr(executor, "aclose", None)
    if closer is not None:
        await closer()
    return samples


def summarize(samples: list[float]) -> dict[str, float]:
    ordered = sorted(samples)
    return {
        "median_ms": statistics.median(ordered),
        "p90_ms": ordered[min(len(ordered) - 1, int(len(ordered) * 0.9))],
        "n": len(ordered),
    }


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executor", action="append", choices=("python", "rust"))
    parser.add_argument("--iterations", type=int, default=10)
    parser.add_argument("--json", type=Path, help="also write raw results here")
    args = parser.parse_args()

    executors = args.executor or ["python", "rust"]
    results = {}
    for name in executors:
        print(f"measuring {name} ({args.iterations} iterations) ...", file=sys.stderr)
        raw = await measure(name, args.iterations)
        results[name] = {op: summarize(values) for op, values in raw.items() if values}

    operations = list(next(iter(results.values())))
    width = max(len(op) for op in operations) + 2

    header = f"{'operation':<{width}}" + "".join(
        f"{name + ' median':>16}{name + ' p90':>14}" for name in executors
    )
    print("\n" + header)
    print("-" * len(header))
    for op in operations:
        row = f"{op:<{width}}"
        for name in executors:
            stat = results[name][op]
            row += f"{stat['median_ms']:>13.1f}ms" + f"{stat['p90_ms']:>11.1f}ms"
        print(row)

    if len(executors) == 2 and set(executors) == {"python", "rust"}:
        print("\nrust median as a multiple of python (< 1.00 means rust is faster):")
        for op in operations:
            python_ms = results["python"][op]["median_ms"]
            rust_ms = results["rust"][op]["median_ms"]
            ratio = rust_ms / python_ms if python_ms else float("nan")
            print(f"  {op:<{width}} {ratio:.2f}x")

    if args.json:
        args.json.write_text(json.dumps(results, indent=2))
        print(f"\nwrote {args.json}")


if __name__ == "__main__":
    asyncio.run(main())
