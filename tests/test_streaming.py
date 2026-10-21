"""run_tests streams its output.

Streaming was implemented through the protocol, both executors and the
conformance suite, and then wired to nothing: the tool that runs tests called
exec without a sink, so a five-minute test run still showed nothing until it
finished. These cover the wiring, which is the part that was missing.
"""

import shutil
import subprocess
import time

import pytest

from agentgauge.agent_loop.tools import execute_tool
from agentgauge.sandbox.protocol import ExecResult, OutputChunk
from tests.fakes import FakeExecutor, host_path

_daemon_up = (
    shutil.which("docker") is not None
    and subprocess.run(["docker", "info"], capture_output=True).returncode == 0
)


async def test_output_reaches_the_model_even_though_the_executor_streamed_it():
    """An executor that streams leaves the result's buffers empty. The tool
    has to hand the model what it collected, or the run sees nothing."""
    executor = FakeExecutor(
        responses={"sh -c pytest": ExecResult(exit_code=1, stdout="F\n1 failed\n", stderr="")}
    )
    handle = await executor.start(host_path("."), "img")

    output = await execute_tool("run_tests", {"command": "pytest"}, executor, handle, 30)

    assert "exit code: 1" in output
    assert "1 failed" in output


async def test_the_caller_sees_chunks_as_they_arrive():
    executor = FakeExecutor(
        responses={"sh -c pytest": ExecResult(exit_code=0, stdout="collected 5\n", stderr="")}
    )
    handle = await executor.start(host_path("."), "img")
    chunks: list[OutputChunk] = []

    await execute_tool(
        "run_tests", {"command": "pytest"}, executor, handle, 30, on_output=chunks.append
    )

    assert [c.data for c in chunks] == ["collected 5\n"]


async def test_output_is_not_duplicated_by_an_executor_that_also_buffers():
    """Not every implementation has to empty its buffers when streaming."""

    class BuffersAnyway(FakeExecutor):
        async def exec(self, handle, command, timeout_s, on_output=None):
            result = ExecResult(exit_code=0, stdout="once\n", stderr="")
            if on_output is not None:
                on_output(OutputChunk(kind="stdout", data=result.stdout))
            return result  # deliberately keeps stdout populated

    executor = BuffersAnyway()
    handle = await executor.start(host_path("."), "img")

    output = await execute_tool("run_tests", {"command": "pytest"}, executor, handle, 30)

    assert output.count("once") == 1


@pytest.mark.docker
@pytest.mark.skipif(not _daemon_up, reason="Docker daemon not available")
async def test_chunks_arrive_before_the_command_finishes():
    """The property that matters, and the only one a fake cannot show: output
    is delivered while the command is still running."""
    from agentgauge.sandbox.docker_executor import DockerSandboxExecutor

    executor = DockerSandboxExecutor()
    handle = await executor.start(host_path("."), "agentgauge-base:py3.12")
    arrivals: list[float] = []
    started = time.monotonic()
    try:
        output = await execute_tool(
            "run_tests",
            {"command": "for i in 1 2 3; do echo line-$i; sleep 1; done"},
            executor,
            handle,
            30,
            on_output=lambda _chunk: arrivals.append(time.monotonic() - started),
        )
    finally:
        await executor.teardown(handle)

    assert "line-1" in output and "line-3" in output
    assert arrivals, "no chunks were delivered"
    # the command takes ~3s; the first chunk must not wait for the end of it
    assert arrivals[0] < 2.0, f"first chunk arrived after {arrivals[0]:.1f}s"
