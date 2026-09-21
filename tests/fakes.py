"""In-memory fakes shared across tests (no Docker, no API keys).

FakeExecutor implements the file and lifecycle half of `SandboxExecutor` for
real — byte-accurate storage, missing paths raising, idempotent teardown — so
`tests/conformance/` can hold it to the same contract as the Docker executor.

It does not run processes. `exec` returns whatever the test programmed, and
the conformance suite skips the process-semantics tests for it rather than
letting a fake pretend to have a shell.
"""

from pathlib import Path

from agentbench.sandbox.policy import SandboxPolicy
from agentbench.sandbox.protocol import (
    ExecResult,
    OutputChunk,
    OutputSink,
    RepoSource,
    SandboxHandle,
)


class FakeExecutor:
    """SandboxExecutor with an in-memory filesystem."""

    def __init__(
        self,
        files: dict[str, str] | None = None,
        responses: dict[str, ExecResult] | None = None,
        default_response: ExecResult | None = None,
    ) -> None:
        self.files: dict[str, bytes] = {
            path: content.encode("utf-8") for path, content in (files or {}).items()
        }
        # keyed by the joined command, so a test can say what a command does
        self.responses = responses or {}
        self.default_response = default_response or ExecResult(exit_code=0, stdout="ok", stderr="")
        self.commands: list[list[str]] = []
        self.started: list[RepoSource] = []
        self.policies: list[SandboxPolicy | None] = []
        self.torn_down = False
        self.teardown_count = 0

    async def start(
        self,
        repo: RepoSource,
        image: str,
        policy: SandboxPolicy | None = None,
    ) -> SandboxHandle:
        self.started.append(repo)
        self.policies.append(policy)
        return SandboxHandle(id="fake")

    async def exec(
        self,
        handle: SandboxHandle,
        command: list[str],
        timeout_s: int,
        on_output: OutputSink | None = None,
    ) -> ExecResult:
        self.commands.append(command)
        result = self.responses.get(" ".join(command), self.default_response)
        if on_output is not None:
            if result.stdout:
                on_output(OutputChunk(kind="stdout", data=result.stdout))
            if result.stderr:
                on_output(OutputChunk(kind="stderr", data=result.stderr))
            return result.model_copy(update={"stdout": "", "stderr": ""})
        return result

    async def read_bytes(self, handle: SandboxHandle, path: str) -> bytes:
        if path not in self.files:
            raise FileNotFoundError(path)
        return self.files[path]

    async def write_bytes(self, handle: SandboxHandle, path: str, content: bytes) -> None:
        self.files[path] = content

    async def read_file(self, handle: SandboxHandle, path: str) -> str:
        return (await self.read_bytes(handle, path)).decode("utf-8", errors="replace")

    async def write_file(self, handle: SandboxHandle, path: str, content: str) -> None:
        await self.write_bytes(handle, path, content.encode("utf-8"))

    async def teardown(self, handle: SandboxHandle) -> None:
        self.torn_down = True
        self.teardown_count += 1

    def text(self, path: str) -> str:
        """Stored contents as text — files are held as bytes."""
        return self.files[path].decode("utf-8")


def host_path(path: Path | str) -> RepoSource:
    """Shorthand for the one repo source kind that exists today."""
    from agentbench.sandbox.protocol import HostPathRepo

    return HostPathRepo(path=str(path))
