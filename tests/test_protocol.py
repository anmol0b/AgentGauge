"""The wire format, pinned.

These are not schema-for-schema's-sake tests. Every field name here is a
string a Rust executor has to emit or parse exactly; renaming one in Python
would silently desynchronize the two sides, and the failure would surface as
an unrelated runtime error much later. Changing a name should require
changing a test that says why.
"""

import json

import pytest

from agentbench.sandbox.policy import SandboxPolicy
from agentbench.sandbox.protocol import (
    PROTOCOL_VERSION,
    ErrorObject,
    ExecParams,
    ExecResult,
    Handshake,
    HostPathRepo,
    ReadParams,
    ReadResult,
    Request,
    Response,
    SandboxHandle,
    StartParams,
    StreamEvent,
    WriteParams,
)

HANDLE = SandboxHandle(id="abc123")


def test_protocol_version_is_major_minor():
    major, minor = PROTOCOL_VERSION.split(".")
    assert major.isdigit() and minor.isdigit()


def test_handle_defaults_to_the_repo_workdir():
    assert HANDLE.workdir == "/repo"


def test_repo_source_is_tagged():
    """The tag is what lets a remote executor add a kind without a break."""
    assert HostPathRepo(path="/tmp/repo").model_dump()["kind"] == "host_path"


def test_start_params_round_trip():
    params = StartParams(
        repo=HostPathRepo(path="/tmp/repo"),
        image="agentbench-base:py3.12",
        policy=SandboxPolicy(),
    )
    wire = json.loads(params.model_dump_json())
    assert wire["repo"] == {"kind": "host_path", "path": "/tmp/repo"}
    assert wire["image"] == "agentbench-base:py3.12"
    assert wire["policy"]["network_mode"] == "none"  # sandboxes have no egress
    assert StartParams(**wire) == params


def test_policy_may_be_omitted():
    """Omitted means 'the executor's configured default', not 'no limits'."""
    params = StartParams(repo=HostPathRepo(path="/x"), image="img")
    assert params.policy is None
    assert StartParams(**json.loads(params.model_dump_json())) == params


def test_exec_params_carry_argv_not_a_shell_string():
    params = ExecParams(handle=HANDLE, command=["pytest", "-x"], timeout_s=120)
    wire = json.loads(params.model_dump_json())
    assert wire["command"] == ["pytest", "-x"]
    assert wire["stream"] is False  # streaming is opt-in
    assert ExecParams(**wire) == params


def test_exec_result_round_trip():
    result = ExecResult(exit_code=1, stdout="out", stderr="err", timed_out=True)
    assert ExecResult(**json.loads(result.model_dump_json())) == result


def test_file_contents_cross_as_base64():
    """A repo is not guaranteed to be UTF-8, so the field is bytes-safe."""
    assert "content_b64" in WriteParams.model_fields
    assert "content_b64" in ReadResult.model_fields
    assert "content" not in ReadResult.model_fields


def test_read_params_round_trip():
    params = ReadParams(handle=HANDLE, path="calc.py")
    assert ReadParams(**json.loads(params.model_dump_json())) == params


def test_request_and_response_are_matched_by_id():
    request = Request(id=7, method="exec", params={})
    response = Response(id=7, result={"exit_code": 0})
    assert request.id == response.id
    assert response.error is None


def test_error_response_carries_a_contract_code():
    response = Response(id=1, error=ErrorObject(code="not_found", message="nope"))
    assert response.result is None
    assert json.loads(response.model_dump_json())["error"]["code"] == "not_found"


def test_unknown_error_codes_are_rejected():
    """Callers branch on the code; a free-form string defeats that."""
    with pytest.raises(ValueError):
        ErrorObject(code="whoops", message="…")  # type: ignore[arg-type]


def test_stream_events_cannot_be_mistaken_for_responses():
    """A response is matched on `id`; an event must not carry one."""
    event = StreamEvent(stream=3, kind="stdout", data="collected 5 items\n")
    wire = json.loads(event.model_dump_json())
    assert "id" not in wire
    assert wire["stream"] == 3


def test_handshake_announces_version_and_capabilities():
    handshake = Handshake(
        protocol_version=PROTOCOL_VERSION, backend="docker", capabilities=["stream"]
    )
    wire = json.loads(handshake.model_dump_json())
    assert wire["protocol_version"] == PROTOCOL_VERSION
    assert wire["capabilities"] == ["stream"]


def test_handshake_capabilities_default_to_none_claimed():
    assert Handshake(protocol_version="1.0", backend="minimal").capabilities == []
