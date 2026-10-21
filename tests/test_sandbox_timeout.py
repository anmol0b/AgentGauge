"""Timeout-wrapping logic for the Docker executor, without a daemon.

Constructing DockerSandboxExecutor pings Docker, so these build the object
without __init__ and exercise the pure decision logic.
"""

import threading

from agentgauge.sandbox.docker_executor import (
    _HOST_TIMEOUT_GRACE_S,
    _TIMEOUT_EXIT_CODES,
    DockerSandboxExecutor,
)


class FakeContainer:
    def __init__(self, has_timeout=True):
        self.has_timeout = has_timeout
        self.probes = 0

    def exec_run(self, command, **kwargs):
        self.probes += 1
        return (0 if self.has_timeout else 1), b""


def _executor() -> DockerSandboxExecutor:
    executor = object.__new__(DockerSandboxExecutor)
    executor._has_timeout = {}
    executor._probe_lock = threading.Lock()
    return executor


def test_command_is_wrapped_so_the_deadline_kills_it():
    container = FakeContainer(has_timeout=True)
    wrapped = _executor()._with_timeout(container, "c1", ["pytest", "-x"], 120)
    assert wrapped == ["timeout", "--signal=KILL", "120", "pytest", "-x"]


def test_falls_back_when_the_image_has_no_timeout_binary():
    container = FakeContainer(has_timeout=False)
    command = ["pytest", "-x"]
    assert _executor()._with_timeout(container, "c1", command, 120) == command


def test_probe_runs_once_per_container():
    executor, container = _executor(), FakeContainer()
    for _ in range(5):
        executor._with_timeout(container, "c1", ["true"], 10)
    assert container.probes == 1

    executor._with_timeout(container, "c2", ["true"], 10)
    assert container.probes == 2  # a different container is probed again


def test_host_deadline_is_looser_than_the_container_one():
    """The in-container kill should fire first; the host wait is a backstop."""
    assert _HOST_TIMEOUT_GRACE_S > 0


def test_kill_exit_codes_are_recognised():
    assert 124 in _TIMEOUT_EXIT_CODES  # timeout expired
    assert 137 in _TIMEOUT_EXIT_CODES  # SIGKILL


def test_relative_paths_resolve_against_the_workdir():
    from agentgauge.sandbox.docker_executor import _absolute
    from agentgauge.sandbox.protocol import SandboxHandle

    handle = SandboxHandle(id="c1", workdir="/repo")
    assert _absolute(handle, "calc.py") == "/repo/calc.py"
    assert _absolute(handle, "pkg/mod.py") == "/repo/pkg/mod.py"
    # absolute paths are left alone: the junit report is written to /tmp
    assert _absolute(handle, "/tmp/agentgauge-junit.xml") == "/tmp/agentgauge-junit.xml"
