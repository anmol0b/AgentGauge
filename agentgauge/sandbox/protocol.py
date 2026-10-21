"""The wire contract between the client and a sandbox executor.

`SandboxExecutor` (executor.py) is the in-process shape of this contract; the
types here are its serializable form, so an executor written in another
language can satisfy it over a pipe. The transport is newline-delimited JSON
over the child's stdin/stdout, specified in `docs/PROTOCOL.md`.

Two rules the transport depends on:

- stdout carries protocol messages and nothing else. An executor's own logging
  goes to stderr, or it corrupts the stream.
- File contents cross as base64. A repository is not guaranteed to be UTF-8,
  and a benchmark that cannot check out a PNG is not benchmarking real repos.
"""

from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel

from agentgauge.sandbox.policy import SandboxPolicy

# 2.0 replaced newline-delimited framing with length-prefixed framing.
# Bumped when a change would break an executor built against the old shape.
# Additive, optional fields do not require a bump; removing or retyping one
# does. The client refuses a handshake whose major version it does not know.
PROTOCOL_VERSION = "2.0"


class SandboxHandle(BaseModel):
    """Opaque reference to a running sandbox. Contents are executor-private."""

    id: str
    workdir: str = "/repo"


class ExecResult(BaseModel):
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False


class OutputChunk(BaseModel):
    """A piece of output delivered while a command is still running."""

    kind: Literal["stdout", "stderr"]
    data: str


# Called per chunk when the caller asks to stream. When a sink is supplied the
# executor may leave ExecResult.stdout/stderr empty rather than also buffering.
OutputSink = Callable[[OutputChunk], None]


class HostPathRepo(BaseModel):
    """A repository the executor can read directly from the host filesystem."""

    kind: Literal["host_path"] = "host_path"
    path: str


# One kind today, declared as a tagged union so a remote executor can add
# {"kind": "tar_b64", ...} without a breaking change.
RepoSource = HostPathRepo


class StartParams(BaseModel):
    repo: RepoSource
    image: str
    # Omitted means "whatever the executor is configured with", so a client
    # that does not care about limits need not restate them.
    policy: SandboxPolicy | None = None


class StartResult(BaseModel):
    handle: SandboxHandle


class ExecParams(BaseModel):
    handle: SandboxHandle
    command: list[str]
    timeout_s: int
    stream: bool = False


class ReadParams(BaseModel):
    handle: SandboxHandle
    path: str


class ReadResult(BaseModel):
    content_b64: str


class WriteParams(BaseModel):
    handle: SandboxHandle
    path: str
    content_b64: str


class TeardownParams(BaseModel):
    handle: SandboxHandle


Method = Literal["start", "exec", "read_bytes", "write_bytes", "teardown"]

# Error codes are part of the contract: the client maps `not_found` onto
# FileNotFoundError, so an executor that invents its own string turns a
# missing file into an opaque failure.
ErrorCode = Literal[
    "not_found",  # the requested path does not exist
    "invalid_request",  # malformed params, unknown method, stale handle
    "unsupported",  # a capability this executor does not implement
    "backend_error",  # the container runtime failed
]


class ErrorObject(BaseModel):
    code: ErrorCode
    message: str


class Request(BaseModel):
    id: int
    method: Method
    params: dict[str, Any]


class Response(BaseModel):
    id: int
    result: dict[str, Any] | None = None
    error: ErrorObject | None = None


class StreamEvent(BaseModel):
    """Unsolicited output for an in-flight request. Carries no `id` field so
    it can never be mistaken for a Response."""

    stream: int  # the Request.id this output belongs to
    kind: Literal["stdout", "stderr"]
    data: str


class Handshake(BaseModel):
    """First line the executor writes, before any request is sent."""

    protocol_version: str
    backend: str  # e.g. "docker"
    capabilities: list[str] = []  # e.g. ["stream"]
