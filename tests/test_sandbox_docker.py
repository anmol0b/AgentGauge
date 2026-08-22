"""Docker integration test — skipped unless the daemon is reachable."""

import shutil
import subprocess

import pytest

from agentbench.sandbox.docker_executor import DockerSandboxExecutor

pytestmark = pytest.mark.docker

_daemon_up = shutil.which("docker") is not None and subprocess.run(
    ["docker", "info"], capture_output=True
).returncode == 0


@pytest.mark.skipif(not _daemon_up, reason="Docker daemon not available")
async def test_docker_executor_round_trip(tmp_path):
    (tmp_path / "f.txt").write_text("data\n")
    executor = DockerSandboxExecutor()
    handle = await executor.start(tmp_path, image="agentbench-base:py3.12")
    try:
        result = await executor.exec(handle, ["cat", "f.txt"], timeout_s=30)
        assert result.exit_code == 0 and result.stdout == "data\n"

        await executor.write_file(handle, "new.txt", "hello\n")
        assert await executor.read_file(handle, "new.txt") == "hello\n"

        timed = await executor.exec(handle, ["sleep", "5"], timeout_s=1)
        assert timed.timed_out
    finally:
        await executor.teardown(handle)
