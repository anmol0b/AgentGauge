"""Task and TaskSet schemas."""

from pathlib import Path

from pydantic import BaseModel, model_validator


class Task(BaseModel):
    id: str
    repo_url: str  # git URL or local path
    setup_commands: list[str]  # e.g. install deps
    failing_test_command: str  # e.g. "pytest tests/test_x.py"
    full_test_command: str = "pytest"  # for regression detection
    difficulty: str | None = None
    # Git ref (commit/tag/branch) to check out after cloning. Pin this for
    # real repos: an unpinned upstream makes results irreproducible the moment
    # the default branch moves.
    repo_ref: str | None = None
    # Which repository this task exercises. Several tasks usually share one.
    # Scoring a repo (rather than a model) means grouping runs by this, so it
    # is recorded on every run from the start — back-filling it later, once
    # real runs exist, is not possible.
    repo_id: str | None = None

    @model_validator(mode="after")
    def _default_repo_id(self) -> "Task":
        if not self.repo_id:
            self.repo_id = Path(self.repo_url.rstrip("/")).name.removesuffix(".git")
        return self


class TaskSet(BaseModel):
    tasks: list[Task]
