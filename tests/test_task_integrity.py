"""Every task in example.yaml must still describe reality.

A benchmark task is a claim: "this named test fails in this repo, and the rest
of the suite passes". Nothing enforced that claim, so a fixture bug that got
tidied away would turn its task into a free pass for every model, and a typo in
a test path would look like a task no model could solve. Both would quietly
corrupt every number the tool reports.

These run the fixture suites directly on the host — no Docker, no agent.
"""

import shutil
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

import pytest
import yaml

from agentbench.models.task import Task, TaskSet

REPO_ROOT = Path(__file__).parent.parent
TASKS_FILE = REPO_ROOT / "agentbench" / "tasks" / "example.yaml"

# pytest's own exit codes
TESTS_FAILED = 1
NO_TESTS_COLLECTED = 5
USAGE_ERROR = 4


def _tasks() -> list[Task]:
    return TaskSet(**yaml.safe_load(TASKS_FILE.read_text())).tasks


def _local_tasks() -> list[Task]:
    return [t for t in _tasks() if (REPO_ROOT / t.repo_url).is_dir()]


def _run(command: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    # the task's command starts with "pytest"; run this interpreter's copy
    argv = command.split()
    assert argv[0] == "pytest", f"expected a pytest command, got {command!r}"
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", *argv[1:]],
        cwd=cwd,
        capture_output=True,
        text=True,
    )


def _ids(tasks: list[Task]) -> list[str]:
    return [t.id for t in tasks]


def test_every_task_points_at_a_real_repo():
    for task in _tasks():
        if task.repo_url.startswith(("http://", "https://", "git@")):
            assert task.repo_ref, f"{task.id}: a remote repo must pin repo_ref"
        else:
            assert (REPO_ROOT / task.repo_url).is_dir(), f"{task.id}: {task.repo_url} missing"


def test_task_ids_are_unique():
    ids = _ids(_tasks())
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize("task", _local_tasks(), ids=_ids(_local_tasks()))
def test_declared_failing_test_actually_fails(task: Task, tmp_path: Path):
    """If it passes, the task is a free point for every model."""
    repo = tmp_path / "repo"
    shutil.copytree(REPO_ROOT / task.repo_url, repo)
    result = _run(task.failing_test_command, repo)

    assert result.returncode not in (NO_TESTS_COLLECTED, USAGE_ERROR), (
        f"{task.id}: failing_test_command matched no test — check the path\n{result.stdout[-2000:]}"
    )
    assert result.returncode == TESTS_FAILED, (
        f"{task.id}: the declared failing test does not fail, so the task is "
        f"already solved\n{result.stdout[-2000:]}"
    )


@pytest.mark.parametrize("repo_id", sorted({t.repo_id for t in _local_tasks() if t.repo_id}))
def test_only_declared_tests_fail_in_each_fixture(repo_id: str, tmp_path: Path):
    """Undeclared failures mean a broken fixture, and they pollute the
    regression baseline for every task sharing the repo."""
    tasks = [t for t in _local_tasks() if t.repo_id == repo_id]
    repo = tmp_path / "repo"
    shutil.copytree(REPO_ROOT / tasks[0].repo_url, repo)

    result = _run(tasks[0].full_test_command, repo)
    failed = {
        line.split()[1].split("::")[-1]
        for line in result.stdout.splitlines()
        if line.startswith("FAILED ")
    }
    declared = {t.failing_test_command.split("::")[-1] for t in tasks}

    undeclared = failed - declared
    assert not undeclared, (
        f"{repo_id}: tests fail that no task declares: {sorted(undeclared)}. "
        f"Either add a task for them or fix the fixture."
    )


def test_every_fixture_repo_is_used_by_a_task():
    """An orphaned fixture is dead weight that still looks load-bearing."""
    fixtures = {p.name for p in (REPO_ROOT / "agentbench/tasks/fixtures").iterdir() if p.is_dir()}
    used = {t.repo_id for t in _local_tasks()}
    assert not fixtures - used, f"unused fixture repos: {sorted(fixtures - used)}"


def test_difficulty_labels_are_from_the_known_set():
    allowed = {"easy", "medium", "hard"}
    by_difficulty: dict[str | None, list[str]] = defaultdict(list)
    for task in _tasks():
        by_difficulty[task.difficulty].append(task.id)
    unknown = set(by_difficulty) - allowed
    assert not unknown, f"unknown difficulty labels: {unknown}"
