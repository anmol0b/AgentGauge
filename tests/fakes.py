"""In-memory fakes shared across tests (no Docker, no API keys)."""

from pathlib import Path

from agentbench.sandbox.executor import ExecResult, SandboxHandle


class FakeExecutor:
    """SandboxExecutor with an in-memory filesystem."""

    def __init__(self, files: dict[str, str] | None = None) -> None:
        self.files = dict(files or {})
        self.commands: list[list[str]] = []
        self.torn_down = False

    async def start(self, repo_path: Path, image: str) -> SandboxHandle:
        return SandboxHandle(id="fake")

    async def exec(self, handle: SandboxHandle, command: list[str], timeout_s: int) -> ExecResult:
        self.commands.append(command)
        return ExecResult(exit_code=0, stdout="ok", stderr="")

    async def read_file(self, handle: SandboxHandle, path: str) -> str:
        if path not in self.files:
            raise FileNotFoundError(path)
        return self.files[path]

    async def write_file(self, handle: SandboxHandle, path: str, content: str) -> None:
        self.files[path] = content

    async def teardown(self, handle: SandboxHandle) -> None:
        self.torn_down = True
