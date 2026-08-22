"""Standalone smoke test for the Docker sandbox executor.

Usage: .venv/bin/python scripts/sandbox_smoke.py
Requires: running Docker daemon. No API keys needed.
"""

import asyncio
import tempfile
from pathlib import Path

from agentbench.sandbox.docker_executor import DockerSandboxExecutor


async def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        (repo / "hello.txt").write_text("hello from host\n")
        executor = DockerSandboxExecutor()
        handle = await executor.start(repo, image="python:3.12-slim")
        try:
            result = await executor.exec(handle, ["echo", "hello"], timeout_s=30)
            assert result.exit_code == 0 and result.stdout.strip() == "hello", result
            print("exec ok:", result.stdout.strip())

            copied = await executor.read_file(handle, "hello.txt")
            assert copied == "hello from host\n", copied
            print("repo copy ok:", copied.strip())

            await executor.write_file(handle, "subdir/out.txt", "written by executor\n")
            back = await executor.read_file(handle, "subdir/out.txt")
            assert back == "written by executor\n", back
            print("write/read ok:", back.strip())

            timed = await executor.exec(handle, ["sleep", "5"], timeout_s=1)
            assert timed.timed_out, timed
            print("timeout ok")
        finally:
            await executor.teardown(handle)
    print("sandbox smoke test passed")


if __name__ == "__main__":
    asyncio.run(main())
