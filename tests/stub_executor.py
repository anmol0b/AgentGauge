"""A scriptable executor that speaks the protocol but runs nothing.

Used by tests/test_subprocess_executor.py to exercise the client's half of
the protocol — handshake checks, error mapping, stream events, an executor
that dies — without a container runtime. Behaviour comes from a JSON file
named by AGENTBENCH_STUB_SCRIPT:

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

PROTOCOL_VERSION = "1.0"
DEFAULT_HANDSHAKE = {
    "protocol_version": PROTOCOL_VERSION,
    "backend": "stub",
    "capabilities": ["stream"],
}


def emit(message: object) -> None:
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def main() -> None:
    with open(os.environ["AGENTBENCH_STUB_SCRIPT"]) as handle:
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
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        request = json.loads(line)
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
