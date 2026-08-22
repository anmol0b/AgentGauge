"""SandboxExecutor interface — the seam between orchestration and container runtimes.

Orchestration code NEVER imports docker directly; it only knows this Protocol.
A future Rust executor (or any other runtime) only has to satisfy these methods.
"""

from pathlib import Path
from typing import Protocol

from pydantic import BaseModel


class SandboxHandle(BaseModel):
    """Opaque reference to a running sandbox. Contents are executor-private."""

    id: str
    workdir: str = "/repo"


class ExecResult(BaseModel):
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False


class SandboxExecutor(Protocol):
    async def start(self, repo_path: Path, image: str) -> SandboxHandle: ...
    async def exec(
        self, handle: SandboxHandle, command: list[str], timeout_s: int
    ) -> ExecResult: ...
    async def read_file(self, handle: SandboxHandle, path: str) -> str: ...
    async def write_file(self, handle: SandboxHandle, path: str, content: str) -> None: ...
    async def teardown(self, handle: SandboxHandle) -> None: ...
