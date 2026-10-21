"""A SandboxExecutor backed by an out-of-process executor binary.

This speaks the protocol in `docs/PROTOCOL.md` to any conforming
implementation — the Rust executor is one, but nothing here is Rust-specific.
The binary is a child process: requests go to its stdin, responses and stream
events come back on its stdout, one JSON object per line. Its stderr is left
attached to ours so its logs are visible and can never corrupt the stream.
"""

import asyncio
import base64
import contextlib
import json
from pathlib import Path
from typing import Any

from agentgauge.sandbox.policy import SandboxPolicy
from agentgauge.sandbox.protocol import (
    PROTOCOL_VERSION,
    ExecResult,
    Handshake,
    OutputChunk,
    OutputSink,
    RepoSource,
    SandboxHandle,
)

# How long to wait for the handshake before concluding the binary is not an
# executor at all (wrong file on PATH, or it died on startup).
_HANDSHAKE_TIMEOUT_S = 30
# Slack on top of the command's own deadline. The executor enforces the
# timeout itself; this only catches an executor that has stopped responding.
_RESPONSE_GRACE_S = 60


class ExecutorProtocolError(RuntimeError):
    """The executor violated the protocol, or died mid-request."""


class SubprocessExecutor:
    def __init__(self, binary: str | Path, policy: SandboxPolicy | None = None) -> None:
        self._binary = str(binary)
        self._policy = policy
        self._process: asyncio.subprocess.Process | None = None
        self._reader: asyncio.Task[None] | None = None
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._sinks: dict[int, OutputSink] = {}
        self._next_id = 0
        self._start_lock = asyncio.Lock()
        self._broken: Exception | None = None
        self.handshake: Handshake | None = None

    def supports(self, capability: str) -> bool:
        """Whether the executor declared it can do this.

        Only meaningful after the handshake; before that nothing is known, so
        nothing is claimed.
        """
        return self.handshake is not None and capability in self.handshake.capabilities

    # --- process lifecycle ------------------------------------------------

    async def _ensure_started(self) -> asyncio.subprocess.Process:
        async with self._start_lock:
            if self._process is not None and self._process.returncode is None:
                return self._process

            process = await asyncio.create_subprocess_exec(
                self._binary,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                # stderr stays on ours: the executor logs there by contract
            )
            self._process = process
            # Read the handshake before the reader task exists: two
            # coroutines awaiting the same StreamReader is a RuntimeError,
            # and the reader would otherwise swallow this first line.
            await self._handshake(process)
            self._reader = asyncio.create_task(self._read_loop(process))
            return process

    async def _handshake(self, process: asyncio.subprocess.Process) -> None:
        assert process.stdout is not None
        try:
            message = await asyncio.wait_for(
                _read_message(process.stdout), timeout=_HANDSHAKE_TIMEOUT_S
            )
        except TimeoutError as exc:
            raise ExecutorProtocolError(
                f"{self._binary} sent no handshake within {_HANDSHAKE_TIMEOUT_S}s"
            ) from exc
        if message is None:
            raise ExecutorProtocolError(f"{self._binary} exited before the handshake")

        handshake = Handshake(**message)
        theirs = handshake.protocol_version.split(".")[0]
        ours = PROTOCOL_VERSION.split(".")[0]
        if theirs != ours:
            raise ExecutorProtocolError(
                f"{self._binary} speaks protocol {handshake.protocol_version}, "
                f"this client speaks {PROTOCOL_VERSION}"
            )
        self.handshake = handshake

    async def _read_loop(self, process: asyncio.subprocess.Process) -> None:
        assert process.stdout is not None
        reason = ExecutorProtocolError(f"{self._binary} exited unexpectedly")
        try:
            while True:
                message = await _read_message(process.stdout)
                if message is None:
                    break
                self._dispatch(message)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # an unreadable stream, not a failed command
            reason = ExecutorProtocolError(f"{self._binary} stream failed: {exc}")
        finally:
            # Nothing will answer the outstanding calls, and nothing will
            # answer future ones either: without this the next call writes to
            # a process with no reader and waits forever.
            self._broken = reason
            self._fail_pending(reason)

    def _dispatch(self, message: dict[str, Any]) -> None:
        if "stream" in message:
            sink = self._sinks.get(message["stream"])
            if sink is not None:
                sink(OutputChunk(kind=message["kind"], data=message["data"]))
            return

        pending = self._pending.pop(message["id"], None)
        if pending is None or pending.done():
            return
        if message.get("error"):
            pending.set_exception(_as_exception(message["error"]))
        else:
            pending.set_result(message.get("result") or {})

    def _fail_pending(self, error: Exception) -> None:
        for future in self._pending.values():
            if not future.done():
                future.set_exception(error)
        self._pending.clear()
        self._sinks.clear()

    async def aclose(self) -> None:
        """Stop the executor. It reaps its own containers on the way out."""
        process, self._process = self._process, None
        if process is None:
            return
        if process.returncode is None and process.stdin is not None:
            process.stdin.close()  # EOF ends its serve loop
            try:
                await asyncio.wait_for(process.wait(), timeout=30)
            except TimeoutError:
                process.kill()
                await process.wait()
        if self._reader is not None:
            self._reader.cancel()
            # CancelledError is expected; anything else is already logged by
            # the loop itself and must not mask the shutdown.
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._reader
            self._reader = None

    # --- request plumbing -------------------------------------------------

    async def _call(
        self,
        method: str,
        params: dict[str, Any],
        on_output: OutputSink | None = None,
        timeout_s: float | None = None,
    ) -> dict[str, Any]:
        if self._broken is not None:
            raise self._broken
        process = await self._ensure_started()
        assert process.stdin is not None

        self._next_id += 1
        request_id = self._next_id
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        if on_output is not None:
            self._sinks[request_id] = on_output

        try:
            process.stdin.write(_frame({"id": request_id, "method": method, "params": params}))
            await process.stdin.drain()
            if timeout_s is None:
                return await future
            return await asyncio.wait_for(future, timeout=timeout_s)
        except TimeoutError as exc:
            self._pending.pop(request_id, None)
            raise ExecutorProtocolError(f"{method} got no response within {timeout_s}s") from exc
        finally:
            self._sinks.pop(request_id, None)

    # --- SandboxExecutor --------------------------------------------------

    async def start(
        self,
        repo: RepoSource,
        image: str,
        policy: SandboxPolicy | None = None,
    ) -> SandboxHandle:
        effective = policy or self._policy
        params: dict[str, Any] = {"repo": repo.model_dump(), "image": image}
        if effective is not None:
            params["policy"] = effective.model_dump()
        result = await self._call("start", params)
        return SandboxHandle(**result["handle"])

    async def exec(
        self,
        handle: SandboxHandle,
        command: list[str],
        timeout_s: int,
        on_output: OutputSink | None = None,
    ) -> ExecResult:
        await self._ensure_started()  # the handshake decides what to ask for
        # Asking an executor to stream when it said it cannot is how a
        # capability list becomes decorative. Without streaming the output
        # still comes back in the result, so the caller loses progress
        # reporting rather than data.
        streaming = on_output is not None and self.supports("stream")
        result = await self._call(
            "exec",
            {
                "handle": handle.model_dump(),
                "command": command,
                "timeout_s": timeout_s,
                "stream": streaming,
            },
            on_output=on_output if streaming else None,
            timeout_s=timeout_s + _RESPONSE_GRACE_S,
        )
        return ExecResult(**result)

    async def read_bytes(self, handle: SandboxHandle, path: str) -> bytes:
        result = await self._call("read_bytes", {"handle": handle.model_dump(), "path": path})
        return base64.b64decode(result["content_b64"])

    async def write_bytes(self, handle: SandboxHandle, path: str, content: bytes) -> None:
        await self._call(
            "write_bytes",
            {
                "handle": handle.model_dump(),
                "path": path,
                "content_b64": base64.b64encode(content).decode(),
            },
        )

    async def read_file(self, handle: SandboxHandle, path: str) -> str:
        return (await self.read_bytes(handle, path)).decode("utf-8", errors="replace")

    async def write_file(self, handle: SandboxHandle, path: str, content: str) -> None:
        await self.write_bytes(handle, path, content.encode("utf-8"))

    async def teardown(self, handle: SandboxHandle) -> None:
        await self._call("teardown", {"handle": handle.model_dump()})


def _frame(message: dict[str, Any]) -> bytes:
    """Length-prefixed framing, as in LSP.

    A delimiter-based format makes every reader impose a maximum message
    size, and any such limit is a cliff: reading one large file used to
    overrun asyncio's line buffer and hang the client. A declared length has
    no ceiling beyond memory.
    """
    payload = json.dumps(message).encode()
    return f"Content-Length: {len(payload)}\r\n\r\n".encode("ascii") + payload


async def _read_message(stream: asyncio.StreamReader) -> dict[str, Any] | None:
    """Read one framed message, or None at end of stream."""
    length: int | None = None
    while True:
        line = await stream.readline()
        if not line:
            return None
        header = line.strip()
        if not header:
            break  # blank line ends the headers
        name, _, value = header.decode("ascii", errors="replace").partition(":")
        if name.strip().lower() == "content-length":
            length = int(value.strip())

    if length is None:
        raise ExecutorProtocolError("a message arrived with no Content-Length header")
    # readexactly, unlike readline, has no buffer limit to overrun
    message: dict[str, Any] = json.loads(await stream.readexactly(length))
    return message


def _as_exception(error: dict[str, Any]) -> Exception:
    """Map protocol error codes onto the exceptions callers already branch on."""
    message = error.get("message", "")
    if error.get("code") == "not_found":
        return FileNotFoundError(message)
    return ExecutorProtocolError(f"{error.get('code')}: {message}")
