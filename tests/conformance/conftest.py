"""Parametrization for the executor conformance suite.

Every `SandboxExecutor` implementation is run against the same contract. An
implementation declares what it can do; tests for a capability it lacks are
skipped rather than quietly passing, so a fake never gets credit for process
semantics it does not have.

Adding the Rust executor means adding one entry to IMPLEMENTATIONS.
"""

import shutil
import subprocess
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from agentgauge.sandbox.discovery import find_executor_binary
from agentgauge.sandbox.protocol import SandboxHandle
from tests.fakes import FakeExecutor, host_path

DOCKER_IMAGE = "agentgauge-base:py3.12"

_daemon_up = (
    shutil.which("docker") is not None
    and subprocess.run(["docker", "info"], capture_output=True).returncode == 0
)


@dataclass
class Implementation:
    name: str
    make: Callable[[], Any]
    # "files"   — read/write/delete semantics
    # "process" — actually runs commands (exit codes, streams, timeouts)
    # "stream"  — delivers output incrementally via on_output
    # "repo"    — start() materializes the repo into the sandbox
    capabilities: set[str] = field(default_factory=set)
    image: str = DOCKER_IMAGE


def _docker() -> Any:
    from agentgauge.sandbox.docker_executor import DockerSandboxExecutor

    return DockerSandboxExecutor()


def _rust_binary() -> Any:
    """None when no binary is built or installed — an sdist has none."""
    try:
        return find_executor_binary()
    except FileNotFoundError:
        return None


def _rust() -> Any:
    from agentgauge.sandbox.subprocess_executor import SubprocessExecutor

    binary = _rust_binary()
    assert binary is not None  # guarded by the skipif below
    return SubprocessExecutor(binary)


IMPLEMENTATIONS = [
    pytest.param(
        Implementation(name="fake", make=FakeExecutor, capabilities={"files"}),
        id="fake",
    ),
    pytest.param(
        Implementation(
            name="docker",
            make=_docker,
            capabilities={"files", "process", "stream", "repo"},
        ),
        id="docker",
        marks=[
            pytest.mark.docker,
            pytest.mark.skipif(not _daemon_up, reason="Docker daemon not available"),
        ],
    ),
    pytest.param(
        Implementation(
            name="rust",
            make=_rust,
            # The Rust executor must satisfy exactly what the Python one does.
            # Anything it cannot do is a gap, not a different capability set.
            capabilities={"files", "process", "stream", "repo"},
        ),
        id="rust",
        marks=[
            pytest.mark.docker,
            pytest.mark.skipif(not _daemon_up, reason="Docker daemon not available"),
            pytest.mark.skipif(
                _rust_binary() is None,
                reason="executor binary not built (cargo build --release in rust/)",
            ),
        ],
    ),
]


@dataclass
class Sandbox:
    """One started sandbox, plus what its executor claims to support."""

    executor: Any
    handle: SandboxHandle
    capabilities: set[str]
    repo_dir: Path

    def supports(self, capability: str) -> bool:
        return capability in self.capabilities

    def require(self, capability: str) -> None:
        if not self.supports(capability):
            pytest.skip(f"executor does not support {capability!r}")


@pytest.fixture(params=IMPLEMENTATIONS)
async def sandbox(request: Any, tmp_path: Path) -> AsyncIterator[Sandbox]:
    impl: Implementation = request.param

    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    (repo_dir / "seeded.txt").write_text("from the repo\n")

    executor = impl.make()
    handle = await executor.start(host_path(repo_dir), impl.image)
    try:
        yield Sandbox(
            executor=executor,
            handle=handle,
            capabilities=impl.capabilities,
            repo_dir=repo_dir,
        )
    finally:
        await executor.teardown(handle)
        # an out-of-process executor also has to be shut down
        closer = getattr(executor, "aclose", None)
        if closer is not None:
            await closer()
