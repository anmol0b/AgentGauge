# Sandbox executor protocol v1.0

AgentBench runs untrusted, model-generated code inside a sandbox. Everything
it needs from that sandbox goes through one narrow interface, so the sandbox
can be implemented in any language without touching evaluation logic.

This document specifies the wire form. The in-process Python form is
`agentbench.sandbox.executor.SandboxExecutor`; the serializable types are in
`agentbench/sandbox/protocol.py`.

**The normative definition of correct behaviour is `tests/conformance/`.**
This document describes the shape of the messages; the suite defines what the
operations must actually do. An implementation is conformant when that suite
passes against it.

## Transport

The executor is a child process. The client writes requests to its **stdin**
and reads messages from its **stdout**, one JSON object per line (`\n`
terminated, no embedded newlines).

**Messages can be large.** One message is one line, and a `read_bytes`
result carries a whole file as base64. A client reading with a fixed line
buffer must size it accordingly — asyncio's 64 KiB default is far too small,
and the Python client raises it to 64 MiB. Treat that as the practical
ceiling on a single message; a future major version would move to
length-prefixed framing to remove the cliff entirely.

**stdout carries protocol messages and nothing else.** An executor that prints
a log line to stdout corrupts the stream. Diagnostics go to **stderr**, which
the client may capture but never parses.

### Handshake

Before the client sends anything, the executor writes exactly one line:

```json
{"protocol_version": "1.0", "backend": "docker", "capabilities": ["stream"]}
```

The client refuses a major version it does not know. Capabilities are optional
features (see below); core operations are always required.

### Requests and responses

```json
→ {"id": 1, "method": "exec", "params": {…}}
← {"id": 1, "result": {…}}
← {"id": 1, "error": {"code": "not_found", "message": "no such file: a.txt"}}
```

`id` is a client-chosen integer, unique while in flight. Responses may arrive
out of order — the client matches on `id`, and issues concurrent requests.

### Stream events

When a request asks to stream, output arrives as it is produced:

```json
← {"stream": 1, "kind": "stdout", "data": "collected 5 items\n"}
```

A stream event carries **no `id` field**, so it can never be mistaken for a
response. `stream` is the `id` of the request it belongs to. Events stop
before that request's response is written.

## Errors

| Code | Meaning |
|---|---|
| `not_found` | The requested path does not exist. The client raises `FileNotFoundError`. |
| `invalid_request` | Malformed params, unknown method, or a stale handle. |
| `unsupported` | A capability this executor does not implement. |
| `backend_error` | The container runtime failed. |

Codes are part of the contract. Callers branch on `not_found` to tell "the
test report is missing" from "the sandbox is broken", so an executor that
invents its own string turns a detectable condition into an opaque failure.

**A command that fails is not a protocol error.** A non-zero exit code, or a
command that times out, is a successful `exec` whose result says so. Errors
are for the executor failing, not the sandboxed program.

## Methods

### `start`

```json
{"repo": {"kind": "host_path", "path": "/tmp/x/repo"},
 "image": "agentbench-base:py3.12",
 "policy": {"mem_limit": "2g", "nano_cpus": 1000000000,
            "pids_limit": 256, "network_mode": "none", "exec_timeout_s": 120}}
→ {"handle": {"id": "…", "workdir": "/repo"}}
```

`repo` is a tagged union. `host_path` is the only kind in v1 and requires the
executor to share a filesystem with the client; a remote executor would add
`{"kind": "tar_b64", …}` without a version bump.

`policy` may be omitted, meaning the executor's configured default. Sandboxes
run with **no network**: the client calls model APIs itself, so the sandbox
never needs egress.

The repository contents must be present at `workdir` when this returns.

### `exec`

```json
{"handle": {…}, "command": ["pytest", "-x"], "timeout_s": 120, "stream": false}
→ {"exit_code": 1, "stdout": "…", "stderr": "…", "timed_out": false}
```

`command` is argv, not a shell string — the client wraps in `sh -c` itself
when it wants a shell. Commands run in `workdir`.

Requirements the conformance suite enforces:

- stdout and stderr are reported separately
- a timeout sets `timed_out` and **terminates the command**; it is never
  raised as an error
- **the sandbox stays usable after a timeout.** This is the hard one. Docker's
  Engine API has no way to kill a running exec ([moby#35703][1], still open),
  so an implementation on that backend must wrap the command — the Python
  executor uses `timeout --signal=KILL` — or an abandoned process keeps
  consuming the sandbox's single CPU for every later call.
- concurrent execs on one handle do not interleave output

With `"stream": true` (capability `stream`), output arrives as stream events
and `stdout`/`stderr` in the result may be empty.

[1]: https://github.com/moby/moby/issues/35703

### `read_bytes` / `write_bytes`

```json
{"handle": {…}, "path": "calc.py"}
→ {"content_b64": "ZGVmIGFkZCgpOgo="}

{"handle": {…}, "path": "a/b/c.py", "content_b64": "…"}
→ {}
```

Contents cross as base64. A repository is not guaranteed to be UTF-8, and a
benchmark that cannot check out a PNG is not benchmarking real repositories.
Relative paths resolve against `workdir`. `write_bytes` creates parent
directories. Reading a missing path returns `not_found`.

### `teardown`

```json
{"handle": {…}}
→ {}
```

Destroys the sandbox. **Must be idempotent** — the client tears down in a
`finally` block that can run after an earlier teardown on some paths.

An executor should also reap its sandboxes when it exits or is signalled;
orphaned containers are the failure mode users notice.

## Capabilities

| Capability | Meaning |
|---|---|
| `stream` | `exec` honours `"stream": true` |

Core operations are not capabilities: an executor that cannot run a command or
store a file is not an executor. Capabilities exist so the conformance suite
can skip optional behaviour rather than let it pass untested.

## Versioning

`protocol_version` is `MAJOR.MINOR`. Adding an optional field, a method, or a
capability bumps MINOR. Removing or retyping anything bumps MAJOR. A client
accepts any MINOR within a MAJOR it knows.

## Writing an executor

1. Emit the handshake before reading anything.
2. Implement the five methods.
3. Run `pytest tests/conformance/` against it by adding an entry to
   `IMPLEMENTATIONS` in `tests/conformance/conftest.py`.

The suite is the specification. If it passes, AgentBench can use your
executor; if it does not, the gap is real even when this document seems to
allow it.
