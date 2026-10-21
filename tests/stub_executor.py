"""A scriptable executor that speaks the protocol but runs nothing.

Used by tests/test_subprocess_executor.py to exercise the client's half of
the protocol — handshake checks, error mapping, stream events, an executor
that dies — without a container runtime. Behaviour comes from a JSON file
named by AGENTGAUGE_STUB_SCRIPT:

    {
      "handshake": {...},              # or omitted for a valid default
      "exit_immediately": false,
      "record_to": "/path/params.json",
      "stream": [{"kind": "stdout", "data": "one\\n"}],
      "responses": {"read_bytes": {"error": {"code": "not_found", ...}}}
    }

A method with no entry gets `{"result": {}}`.
"""

import json
import os
import sys
from pathlib import Path
from typing import Any

# Run as a script, so the package is not importable by default. Taking the
# version from the real module rather than restating it means the stub cannot
# drift out of date and start failing the version check for the wrong reason.
sys.path.insert(0, str(Path(__file__).parent.parent))

from agentgauge.sandbox.protocol import PROTOCOL_VERSION  # noqa: E402

DEFAULT_HANDSHAKE = {
    "protocol_version": PROTOCOL_VERSION,
    "backend": "stub",
    "capabilities": ["stream"],
}


def emit(message: object) -> None:
    """Length-prefixed framing, as in the protocol spec."""
    payload = json.dumps(message).encode()
    sys.stdout.buffer.write(f"Content-Length: {len(payload)}\r\n\r\n".encode("ascii"))
    sys.stdout.buffer.write(payload)
    sys.stdout.buffer.flush()


def read_message() -> dict[str, Any] | None:
    """Read one length-prefixed message from stdin, or None at EOF."""
    length = None
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            return None
        header = line.strip()
        if not header:
            break
        name, _, value = header.decode().partition(":")
        if name.strip().lower() == "content-length":
            length = int(value.strip())
    if length is None:
        return None
    message: dict[str, Any] = json.loads(sys.stdin.buffer.read(length))
    return message


def main() -> None:
    with open(os.environ["AGENTGAUGE_STUB_SCRIPT"]) as handle:
        script = json.load(handle)
    if script.get("never_handshake"):
        # Sit silent so the client's handshake deadline is what fires.
        import time

        time.sleep(script.get("silence_s", 3))
        return

    emit(script.get("handshake", DEFAULT_HANDSHAKE))
    if script.get("exit_immediately"):
        return

    responses = script.get("responses", {})
    while True:
        request = read_message()
        if request is None:
            break
        request_id, method, params = request["id"], request["method"], request["params"]

        if script.get("record_to"):
            with open(script["record_to"], "w") as handle:
                json.dump({"method": method, "params": params}, handle)

        for chunk in script.get("stream", []):
            emit({"stream": request_id, **chunk})

        reply = responses.get(method, {"result": {}})
        emit({"id": request_id, **reply})


if __name__ == "__main__":
    main()
