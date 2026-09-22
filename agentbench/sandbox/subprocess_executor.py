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

from agentbench.sandbox.policy import SandboxPolicy
from agentbench.sandbox.protocol import (
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
        self.handshake: Handshake | None = None

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
            line = await asyncio.wait_for(process.stdout.readline(), timeout=_HANDSHAKE_TIMEOUT_S)
        except TimeoutError as exc:
            raise ExecutorProtocolError(
                f"{self._binary} sent no handshake within {_HANDSHAKE_TIMEOUT_S}s"
            ) from exc
        if not line:
            raise ExecutorProtocolError(f"{self._binary} exited before the handshake")

        handshake = Handshake(**json.loads(line))
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
        try:
            while True:
                line = await process.stdout.readline()
                if not line:
                    break
                self._dispatch(json.loads(line))
        except asyncio.CancelledError:
            raise
        finally:
            # The executor is gone; nothing will answer the outstanding calls.
            self._fail_pending(ExecutorProtocolError(f"{self._binary} exited unexpectedly"))

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
        process = await self._ensure_started()
        assert process.stdin is not None

        self._next_id += 1
        request_id = self._next_id
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        if on_output is not None:
            self._sinks[request_id] = on_output

        payload = json.dumps({"id": request_id, "method": method, "params": params})
        try:
            process.stdin.write(payload.encode() + b"\n")
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
        result = await self._call(
            "exec",
            {
                "handle": handle.model_dump(),
                "command": command,
                "timeout_s": timeout_s,
                "stream": on_output is not None,
            },
            on_output=on_output,
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


def _as_exception(error: dict[str, Any]) -> Exception:
    """Map protocol error codes onto the exceptions callers already branch on."""
    message = error.get("message", "")
    if error.get("code") == "not_found":
        return FileNotFoundError(message)
    return ExecutorProtocolError(f"{error.get('code')}: {message}")
