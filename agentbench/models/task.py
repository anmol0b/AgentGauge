"""Task and TaskSet schemas."""

from pydantic import BaseModel


class Task(BaseModel):
    id: str
    repo_url: str  # git URL or local path
    setup_commands: list[str]  # e.g. install deps
    failing_test_command: str  # e.g. "pytest tests/test_x.py"
    full_test_command: str = "pytest"  # for regression detection
    difficulty: str | None = None


class TaskSet(BaseModel):
    tasks: list[Task]
