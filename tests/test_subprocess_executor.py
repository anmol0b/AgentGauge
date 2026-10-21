"""Protocol handling in the subprocess client, without Docker.

The executor here is `tests/stub_executor.py`, not the Rust binary: these
cover the client's half of the protocol — handshake checks, error mapping,
stream events, an executor that dies — which is awkward to provoke through a
real backend. `tests/conformance/` covers the real thing.
"""

import base64
import json
import stat
import sys
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import pytest

from agentgauge.sandbox.protocol import (
    PROTOCOL_VERSION,
    HostPathRepo,
    OutputChunk,
    SandboxHandle,
)
from agentgauge.sandbox.subprocess_executor import ExecutorProtocolError, SubprocessExecutor

HANDLE = SandboxHandle(id="stub")
STUB_SOURCE = Path(__file__).parent / "stub_executor.py"

EXEC_OK = {"result": {"exit_code": 0, "stdout": "", "stderr": "", "timed_out": False}}


@pytest.fixture
def stub(tmp_path: Path, monkeypatch: Any) -> Callable[..., SubprocessExecutor]:
    """Build a client wired to a stub executor with the given behaviour."""

    def build(**script: Any) -> SubprocessExecutor:
        script_file = tmp_path / "script.json"
        script_file.write_text(json.dumps(script))
        monkeypatch.setenv("AGENTGAUGE_STUB_SCRIPT", str(script_file))

        launcher = tmp_path / "stub-executor"
        launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{STUB_SOURCE}"\n')
        launcher.chmod(launcher.stat().st_mode | stat.S_IEXEC)
        return SubprocessExecutor(launcher)

    return build


@pytest.fixture
async def closing() -> AsyncIterator[list[SubprocessExecutor]]:
    """Shut down every executor a test started, even when it raised."""
    started: list[SubprocessExecutor] = []
    yield started
    for executor in started:
        await executor.aclose()


async def test_handshake_is_read_and_exposed(stub, closing):
    executor = stub()
    closing.append(executor)

    await executor.teardown(HANDLE)

    assert executor.handshake is not None
    assert executor.handshake.backend == "stub"
    assert "stream" in executor.handshake.capabilities


async def test_incompatible_major_version_is_refused(stub, closing):
    """Better to fail at startup than to misparse every later message."""
    executor = stub(handshake={"protocol_version": "99.0", "backend": "stub", "capabilities": []})
    closing.append(executor)

    with pytest.raises(ExecutorProtocolError, match="protocol 99.0"):
        await executor.teardown(HANDLE)


async def test_not_found_becomes_file_not_found_error(stub, closing):
    """Callers branch on this to detect an absent junit report."""
    executor = stub(
        responses={"read_bytes": {"error": {"code": "not_found", "message": "no such file"}}}
    )
    closing.append(executor)

    with pytest.raises(FileNotFoundError):
        await executor.read_bytes(HANDLE, "x")


async def test_other_error_codes_surface_as_protocol_errors(stub, closing):
    executor = stub(
        responses={"teardown": {"error": {"code": "backend_error", "message": "daemon gone"}}}
    )
    closing.append(executor)

    with pytest.raises(ExecutorProtocolError, match="backend_error"):
        await executor.teardown(HANDLE)


async def test_bytes_round_trip_through_base64(stub, closing):
    blob = bytes(range(256))
    executor = stub(
        responses={"read_bytes": {"result": {"content_b64": base64.b64encode(blob).decode()}}}
    )
    closing.append(executor)

    assert await executor.read_bytes(HANDLE, "blob.bin") == blob


async def test_write_sends_base64(stub, closing, tmp_path):
    record = tmp_path / "sent.json"
    executor = stub(record_to=str(record))
    closing.append(executor)

    await executor.write_bytes(HANDLE, "a.bin", b"\x00\xff")

    sent = json.loads(record.read_text())
    assert sent["method"] == "write_bytes"
    assert base64.b64decode(sent["params"]["content_b64"]) == b"\x00\xff"


async def test_stream_events_reach_the_sink(stub, closing):
    executor = stub(
        stream=[
            {"kind": "stdout", "data": "one\n"},
            {"kind": "stderr", "data": "two\n"},
        ],
        responses={"exec": EXEC_OK},
    )
    closing.append(executor)
    chunks: list[OutputChunk] = []

    result = await executor.exec(HANDLE, ["echo"], 30, on_output=chunks.append)

    assert result.exit_code == 0
    assert [c.kind for c in chunks] == ["stdout", "stderr"]
    assert "".join(c.data for c in chunks) == "one\ntwo\n"


async def test_streaming_is_requested_only_when_a_sink_is_given(stub, closing, tmp_path):
    record = tmp_path / "sent.json"
    executor = stub(record_to=str(record), responses={"exec": EXEC_OK})
    closing.append(executor)

    await executor.exec(HANDLE, ["echo"], 30)
    assert json.loads(record.read_text())["params"]["stream"] is False

    await executor.exec(HANDLE, ["echo"], 30, on_output=lambda _chunk: None)
    assert json.loads(record.read_text())["params"]["stream"] is True


async def test_an_executor_that_dies_fails_the_pending_call(stub, closing):
    """Otherwise the caller waits forever on a process that is gone."""
    executor = stub(exit_immediately=True)
    closing.append(executor)

    with pytest.raises(ExecutorProtocolError, match="exited"):
        await executor.teardown(HANDLE)


async def test_a_binary_that_never_handshakes_is_rejected(stub, closing, monkeypatch):
    monkeypatch.setattr("agentgauge.sandbox.subprocess_executor._HANDSHAKE_TIMEOUT_S", 1)
    executor = stub(never_handshake=True, silence_s=3)
    closing.append(executor)

    with pytest.raises(ExecutorProtocolError, match="no handshake"):
        await executor.teardown(HANDLE)


async def test_start_sends_the_repo_as_a_tagged_union(stub, closing, tmp_path):
    record = tmp_path / "sent.json"
    executor = stub(
        record_to=str(record),
        responses={"start": {"result": {"handle": {"id": "c1", "workdir": "/repo"}}}},
    )
    closing.append(executor)

    handle = await executor.start(HostPathRepo(path="/tmp/r"), "img")

    assert handle.id == "c1"
    sent = json.loads(record.read_text())["params"]
    assert sent["repo"] == {"kind": "host_path", "path": "/tmp/r"}
    assert sent["image"] == "img"


async def test_a_message_larger_than_64kib_is_read(stub, closing):
    """asyncio's StreamReader defaults to a 64 KiB line limit and one
    protocol message is one line. Exceeding it used to kill the reader and
    hang every later call."""
    blob = bytes(range(256)) * 2048  # 512 KiB raw, ~683 KiB of base64
    executor = stub(
        responses={"read_bytes": {"result": {"content_b64": base64.b64encode(blob).decode()}}}
    )
    closing.append(executor)

    assert await executor.read_bytes(HANDLE, "big.bin") == blob


async def test_a_broken_stream_fails_later_calls_instead_of_hanging(stub, closing):
    """Once the reader is gone nothing will ever answer, so saying so beats
    writing to a process that cannot reply."""
    executor = stub(exit_immediately=True)
    closing.append(executor)

    with pytest.raises(ExecutorProtocolError):
        await executor.teardown(HANDLE)
    # the second call must fail fast, not wait for a response that cannot come
    with pytest.raises(ExecutorProtocolError):
        await executor.teardown(HANDLE)


async def test_streaming_is_not_requested_from_an_executor_that_cannot(stub, closing, tmp_path):
    """A capability list nothing consults is decoration."""
    record = tmp_path / "sent.json"
    executor = stub(
        record_to=str(record),
        handshake={"protocol_version": PROTOCOL_VERSION, "backend": "stub", "capabilities": []},
        responses={"exec": EXEC_OK},
    )
    closing.append(executor)
    chunks: list[OutputChunk] = []

    await executor.exec(HANDLE, ["echo"], 30, on_output=chunks.append)

    assert json.loads(record.read_text())["params"]["stream"] is False
    assert chunks == []  # nothing was streamed, so nothing is claimed to be
    assert not executor.supports("stream")


async def test_streaming_is_requested_when_declared(stub, closing, tmp_path):
    record = tmp_path / "sent.json"
    executor = stub(record_to=str(record), responses={"exec": EXEC_OK})
    closing.append(executor)

    await executor.exec(HANDLE, ["echo"], 30, on_output=lambda _c: None)

    assert json.loads(record.read_text())["params"]["stream"] is True
    assert executor.supports("stream")


async def test_nothing_is_claimed_before_the_handshake(stub, closing):
    executor = stub()
    closing.append(executor)
    assert not executor.supports("stream")  # not started yet
