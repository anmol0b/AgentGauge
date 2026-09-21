"""SandboxExecutor interface — the seam between orchestration and container runtimes.

Orchestration code NEVER imports docker directly; it only knows this Protocol.
An executor in another language satisfies the same contract over a pipe; see
`protocol.py` for its serializable form and `docs/PROTOCOL.md` for the spec.

Any implementation must pass `tests/conformance/`. That suite is the real
definition of this interface — the type signatures below cannot express
"a timeout leaves the sandbox usable" or "teardown is idempotent".
"""

from typing import Protocol

from agentbench.sandbox.policy import SandboxPolicy
from agentbench.sandbox.protocol import (
    ExecResult,
    HostPathRepo,
    OutputChunk,
    OutputSink,
    RepoSource,
    SandboxHandle,
)

__all__ = [
    "ExecResult",
    "HostPathRepo",
    "OutputChunk",
    "OutputSink",
    "RepoSource",
    "SandboxExecutor",
    "SandboxHandle",
]


class SandboxExecutor(Protocol):
    async def start(
        self,
        repo: RepoSource,
        image: str,
        policy: SandboxPolicy | None = None,
    ) -> SandboxHandle:
        """Create a sandbox containing `repo` and return a handle to it.

        `policy` omitted means the executor's configured default.
        """
        ...

    async def exec(
        self,
        handle: SandboxHandle,
        command: list[str],
        timeout_s: int,
        on_output: OutputSink | None = None,
    ) -> ExecResult:
        """Run `command` to completion, or until `timeout_s` elapses.

        A timeout must terminate the command and leave the sandbox usable for
        the next call; it is reported as `timed_out`, never raised. When
        `on_output` is given, chunks are delivered as they arrive and the
        result's stdout/stderr may be empty.
        """
        ...

    async def read_bytes(self, handle: SandboxHandle, path: str) -> bytes:
        """Raise FileNotFoundError if `path` does not exist."""
        ...

    async def write_bytes(self, handle: SandboxHandle, path: str, content: bytes) -> None:
        """Create parent directories as needed."""
        ...

    async def read_file(self, handle: SandboxHandle, path: str) -> str:
        """UTF-8 convenience over read_bytes, for source and reports."""
        ...

    async def write_file(self, handle: SandboxHandle, path: str, content: str) -> None:
        """UTF-8 convenience over write_bytes."""
        ...

    async def teardown(self, handle: SandboxHandle) -> None:
        """Destroy the sandbox. Must be safe to call more than once."""
        ...
