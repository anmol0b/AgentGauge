"""Docker integration test — skipped unless the daemon is reachable."""

import shutil
import subprocess

import pytest

from agentgauge.sandbox.docker_executor import DockerSandboxExecutor
from tests.fakes import host_path

pytestmark = pytest.mark.docker

_daemon_up = (
    shutil.which("docker") is not None
    and subprocess.run(["docker", "info"], capture_output=True).returncode == 0
)


@pytest.mark.skipif(not _daemon_up, reason="Docker daemon not available")
async def test_docker_executor_round_trip(tmp_path):
    (tmp_path / "f.txt").write_text("data\n")
    executor = DockerSandboxExecutor()
    handle = await executor.start(host_path(tmp_path), image="agentgauge-base:py3.12")
    try:
        result = await executor.exec(handle, ["cat", "f.txt"], timeout_s=30)
        assert result.exit_code == 0 and result.stdout == "data\n"

        await executor.write_file(handle, "new.txt", "hello\n")
        assert await executor.read_file(handle, "new.txt") == "hello\n"

        timed = await executor.exec(handle, ["sleep", "30"], timeout_s=1)
        assert timed.timed_out

        # the process must actually be dead, not merely abandoned: the
        # container should be responsive and running no stray sleep
        still_alive = await executor.exec(handle, ["sh", "-c", "echo alive"], timeout_s=10)
        assert still_alive.stdout.strip() == "alive"
        procs = await executor.exec(handle, ["sh", "-c", "ps -eo args || true"], timeout_s=10)
        assert "sleep 30" not in procs.stdout
    finally:
        await executor.teardown(handle)
