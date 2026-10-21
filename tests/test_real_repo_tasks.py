"""Verify the real-repo tasks against actual clones.

A task is a claim: *this named test fails at this commit, and the upstream
fix makes it pass*. For fixture repos `test_task_integrity.py` checks that
cheaply. For real repositories it needs a network clone and a container, so
it lives here behind the `docker` marker and runs in CI.

Both halves matter:

- if the declared test does not fail, the task is a free point for every
  model
- if applying `fix_ref` does not make it pass, the task is unsolvable and
  scores every model at zero — which looks like a hard benchmark and is
  actually a broken one

Neither is visible from the task file, and neither would be caught by any
other suite.
"""

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from agentgauge.models.task import Task, TaskSet
from agentgauge.orchestrator.runner import SETUP_TIMEOUT_S, TEST_TIMEOUT_S, _materialize_repo
from agentgauge.sandbox.protocol import HostPathRepo

TASKS_FILE = Path(__file__).parent.parent / "agentgauge" / "tasks" / "real_repos.yaml"
IMAGE = "agentgauge-base:py3.12"

_daemon_up = (
    shutil.which("docker") is not None
    and subprocess.run(["docker", "info"], capture_output=True).returncode == 0
)


def _tasks() -> list[Task]:
    return TaskSet(**yaml.safe_load(TASKS_FILE.read_text())).tasks


def _ids(tasks: list[Task]) -> list[str]:
    return [task.id for task in tasks]


def test_every_real_task_pins_its_provenance():
    """Cheap, offline, and the thing most likely to be forgotten."""
    for task in _tasks():
        assert task.repo_ref, f"{task.id}: a real repo must pin repo_ref"
        assert task.fix_ref, f"{task.id}: a real repo task must record fix_ref"
        assert task.fix_ref != task.repo_ref, f"{task.id}: fix_ref is the parent"


@pytest.mark.docker
@pytest.mark.skipif(not _daemon_up, reason="Docker daemon not available")
@pytest.mark.parametrize("task", _tasks(), ids=_ids(_tasks()))
async def test_the_task_fails_and_the_upstream_fix_resolves_it(task: Task, tmp_path: Path):
    from agentgauge.sandbox.docker_executor import DockerSandboxExecutor

    repo = _materialize_repo(task.repo_url, tmp_path, task.repo_ref)
    executor = DockerSandboxExecutor()
    handle = await executor.start(HostPathRepo(path=str(repo)), IMAGE)
    try:
        for command in task.setup_commands:
            setup = await executor.exec(handle, ["sh", "-c", command], SETUP_TIMEOUT_S)
            assert setup.exit_code == 0, f"{task.id}: setup failed\n{setup.stderr}"

        before = await executor.exec(
            handle, ["sh", "-c", task.failing_test_command], TEST_TIMEOUT_S
        )
        assert before.exit_code != 0, (
            f"{task.id}: the declared test passes at repo_ref, so the task is "
            f"already solved\n{before.stdout[-2000:]}"
        )

        # Apply the upstream fix wholesale. If the task is well formed this is
        # exactly the change an agent has to rediscover.
        applied = await executor.exec(
            handle, ["sh", "-c", f"git checkout {task.fix_ref} -- ."], SETUP_TIMEOUT_S
        )
        assert applied.exit_code == 0, f"{task.id}: could not apply fix_ref\n{applied.stderr}"

        after = await executor.exec(handle, ["sh", "-c", task.failing_test_command], TEST_TIMEOUT_S)
        assert after.exit_code == 0, (
            f"{task.id}: the upstream fix does not make the test pass, so the "
            f"task is unsolvable as written\n{after.stdout[-2000:]}"
        )
    finally:
        await executor.teardown(handle)
