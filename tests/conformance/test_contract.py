"""The SandboxExecutor contract, enforced against every implementation.

This suite is the real definition of the interface. A type signature cannot
say "a timeout leaves the sandbox usable" or "teardown is idempotent", and
those are exactly the properties an executor in another language gets wrong.

Run against one implementation:  pytest tests/conformance -k docker
"""

import asyncio

import pytest

from agentbench.sandbox.protocol import OutputChunk

from .conftest import Sandbox

# --- files ----------------------------------------------------------------


async def test_write_then_read_round_trips(sandbox: Sandbox):
    await sandbox.executor.write_file(sandbox.handle, "hello.txt", "hello\n")
    assert await sandbox.executor.read_file(sandbox.handle, "hello.txt") == "hello\n"


async def test_unicode_survives_the_round_trip(sandbox: Sandbox):
    text = "héllo — ünicode ✓ 日本語\n"
    await sandbox.executor.write_file(sandbox.handle, "unicode.txt", text)
    assert await sandbox.executor.read_file(sandbox.handle, "unicode.txt") == text


async def test_binary_survives_the_round_trip(sandbox: Sandbox):
    """A repo is not guaranteed to be UTF-8; a PNG must come back byte-exact."""
    blob = bytes(range(256)) * 4
    await sandbox.executor.write_bytes(sandbox.handle, "blob.bin", blob)
    assert await sandbox.executor.read_bytes(sandbox.handle, "blob.bin") == blob


async def test_empty_file_round_trips(sandbox: Sandbox):
    await sandbox.executor.write_bytes(sandbox.handle, "empty.txt", b"")
    assert await sandbox.executor.read_bytes(sandbox.handle, "empty.txt") == b""


async def test_write_creates_parent_directories(sandbox: Sandbox):
    await sandbox.executor.write_file(sandbox.handle, "a/b/c/deep.txt", "deep\n")
    assert await sandbox.executor.read_file(sandbox.handle, "a/b/c/deep.txt") == "deep\n"


async def test_write_overwrites(sandbox: Sandbox):
    await sandbox.executor.write_file(sandbox.handle, "f.txt", "first")
    await sandbox.executor.write_file(sandbox.handle, "f.txt", "second")
    assert await sandbox.executor.read_file(sandbox.handle, "f.txt") == "second"


async def test_reading_a_missing_file_raises_file_not_found(sandbox: Sandbox):
    """Not a generic error: callers branch on this to detect an absent report."""
    with pytest.raises(FileNotFoundError):
        await sandbox.executor.read_bytes(sandbox.handle, "does/not/exist.txt")


# --- lifecycle ------------------------------------------------------------


async def test_repo_contents_are_present_in_the_sandbox(sandbox: Sandbox):
    sandbox.require("repo")
    assert await sandbox.executor.read_file(sandbox.handle, "seeded.txt") == "from the repo\n"


async def test_teardown_is_idempotent(sandbox: Sandbox):
    """run_once tears down in a finally block that can run after an earlier
    teardown on some paths; a second call must not raise."""
    await sandbox.executor.teardown(sandbox.handle)
    await sandbox.executor.teardown(sandbox.handle)


# --- process semantics ----------------------------------------------------


async def test_exit_code_zero(sandbox: Sandbox):
    sandbox.require("process")
    result = await sandbox.executor.exec(sandbox.handle, ["true"], 30)
    assert result.exit_code == 0
    assert result.timed_out is False


async def test_nonzero_exit_code_propagates(sandbox: Sandbox):
    sandbox.require("process")
    result = await sandbox.executor.exec(sandbox.handle, ["sh", "-c", "exit 3"], 30)
    assert result.exit_code == 3


async def test_stdout_and_stderr_stay_separate(sandbox: Sandbox):
    """Grading reads stdout; merging the two would corrupt junit output."""
    sandbox.require("process")
    result = await sandbox.executor.exec(
        sandbox.handle, ["sh", "-c", "echo to-out; echo to-err >&2"], 30
    )
    assert "to-out" in result.stdout and "to-out" not in result.stderr
    assert "to-err" in result.stderr and "to-err" not in result.stdout


async def test_commands_run_in_the_repo_workdir(sandbox: Sandbox):
    sandbox.require("process")
    result = await sandbox.executor.exec(sandbox.handle, ["cat", "seeded.txt"], 30)
    assert result.exit_code == 0
    assert "from the repo" in result.stdout


async def test_a_file_written_through_the_api_is_visible_to_commands(sandbox: Sandbox):
    """The agent writes with write_file and then runs tests over it."""
    sandbox.require("process")
    await sandbox.executor.write_file(sandbox.handle, "written.txt", "by the api\n")
    result = await sandbox.executor.exec(sandbox.handle, ["cat", "written.txt"], 30)
    assert "by the api" in result.stdout


async def test_timeout_is_reported_not_raised(sandbox: Sandbox):
    sandbox.require("process")
    result = await sandbox.executor.exec(sandbox.handle, ["sleep", "30"], 1)
    assert result.timed_out is True
    assert result.exit_code != 0


async def test_sandbox_still_usable_after_a_timeout(sandbox: Sandbox):
    """The killed command must not hold the sandbox. This is the property the
    in-container `timeout` wrapper exists for — Docker's API cannot kill an
    exec, so a naive host-side timeout leaves the process running."""
    sandbox.require("process")
    await sandbox.executor.exec(sandbox.handle, ["sleep", "30"], 1)

    result = await sandbox.executor.exec(sandbox.handle, ["echo", "still here"], 30)
    assert result.exit_code == 0
    assert "still here" in result.stdout


async def test_timed_out_command_leaves_no_process_behind(sandbox: Sandbox):
    sandbox.require("process")
    await sandbox.executor.exec(sandbox.handle, ["sleep", "29"], 1)

    listing = await sandbox.executor.exec(
        sandbox.handle, ["sh", "-c", "ps -eo args 2>/dev/null || true"], 30
    )
    assert "sleep 29" not in listing.stdout


async def test_parallel_execs_do_not_interleave_output(sandbox: Sandbox):
    """The harness runs commands concurrently; each must get its own output."""
    sandbox.require("process")
    results = await asyncio.gather(
        *(sandbox.executor.exec(sandbox.handle, ["echo", f"run-{i}"], 30) for i in range(5))
    )
    assert [r.stdout.strip() for r in results] == [f"run-{i}" for i in range(5)]


# --- streaming ------------------------------------------------------------


async def test_output_is_delivered_incrementally(sandbox: Sandbox):
    """Without this a five-minute test run shows nothing until it finishes."""
    sandbox.require("stream")
    chunks: list[OutputChunk] = []
    result = await sandbox.executor.exec(
        sandbox.handle,
        ["sh", "-c", "echo one; echo two; echo three >&2"],
        30,
        on_output=chunks.append,
    )

    assert result.exit_code == 0
    text = "".join(c.data for c in chunks)
    assert "one" in text and "two" in text and "three" in text
    assert {c.kind for c in chunks} == {"stdout", "stderr"}


async def test_streaming_does_not_also_buffer(sandbox: Sandbox):
    """The point of streaming is not holding the whole output in memory."""
    sandbox.require("stream")
    chunks: list[OutputChunk] = []
    result = await sandbox.executor.exec(
        sandbox.handle, ["echo", "hello"], 30, on_output=chunks.append
    )
    assert chunks
    assert result.stdout == ""
