"""Runs one task once: sandbox -> agent loop -> evaluate -> RunResult."""

import shutil
import subprocess
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from agentgauge.agent_loop.loop import DEFAULT_SYSTEM_PROMPT, AgentLoopError, run_agent_loop
from agentgauge.agent_loop.transcript import compact_transcript
from agentgauge.models.run import RunResult, Transcript
from agentgauge.models.task import Task
from agentgauge.providers import make_provider
from agentgauge.providers.base import ModelProvider
from agentgauge.sandbox.executor import OutputSink, SandboxExecutor, SandboxHandle
from agentgauge.sandbox.protocol import HostPathRepo

DEFAULT_IMAGE = "python:3.12-slim"
TEST_TIMEOUT_S = 300
SETUP_TIMEOUT_S = 300


async def run_once(
    task: Task,
    run_index: int,
    model: str,
    executor: SandboxExecutor,
    image: str = DEFAULT_IMAGE,
    max_turns: int = 25,
    provider: ModelProvider | None = None,
    on_output: OutputSink | None = None,
) -> RunResult:
    """Run `task` once against `model` and return the measured result.

    `provider` is injectable so callers can share one client across runs (and
    so this function is testable without API keys). When omitted it is built
    from `model`, inside the try block, so a missing key is reported as a run
    error rather than crashing the harness.
    """
    started = time.monotonic()
    tokens_in = tokens_out = tool_call_count = 0
    cost: float | None = 0.0
    diff = ""
    error: str | None = None
    stop_reason: str | None = None
    transcript: Transcript | None = None
    passed = False
    regressions: list[str] | None = None
    tampered: list[str] | None = None
    handle = None
    # Phase timings. A run's wall-clock is dominated by model inference, so
    # these are what say whether sandbox work is worth optimizing at all.
    setup_s = agent_s = grading_s = 0.0

    with tempfile.TemporaryDirectory(prefix=f"agentgauge-{task.id}-") as tmp:
        try:
            repo_path = _materialize_repo(task.repo_url, Path(tmp), task.repo_ref)
            handle = await executor.start(HostPathRepo(path=str(repo_path)), image)
            for command in task.setup_commands:
                result = await executor.exec(handle, ["sh", "-c", command], SETUP_TIMEOUT_S)
                if result.exit_code != 0:
                    raise RuntimeError(f"setup command failed: {command}\n{result.stderr}")

            baseline = await _failing_tests(executor, handle, task)
            setup_s = time.monotonic() - started

            agent_started = time.monotonic()

            run_provider = provider or make_provider(model, system=DEFAULT_SYSTEM_PROMPT)
            failure: BaseException | None = None
            try:
                loop_result = await run_agent_loop(
                    run_provider,
                    executor,
                    handle,
                    instruction=await _instruction(executor, handle, task),
                    max_turns=max_turns,
                    on_output=on_output,
                )
            except AgentLoopError as exc:
                # record the turns that did run, then fail the run as before
                loop_result, failure = exc.partial, exc.__cause__
            tokens_in = loop_result.usage.tokens_in
            tokens_out = loop_result.usage.tokens_out
            cost = loop_result.cost_usd
            tool_call_count = loop_result.tool_call_count
            stop_reason = loop_result.stop_reason
            # recorded here, not after grading: a run that fails later still
            # has a record of what the agent did
            transcript = compact_transcript(loop_result.messages)
            agent_s = time.monotonic() - agent_started
            if failure is not None:
                raise failure

            grading_started = time.monotonic()
            check = await executor.exec(
                handle, ["sh", "-c", task.failing_test_command], TEST_TIMEOUT_S
            )
            passed = check.exit_code == 0

            after = await _failing_tests(executor, handle, task)
            if baseline is not None and after is not None:
                regressions = sorted(after - baseline)

            await executor.exec(
                handle, ["sh", "-c", "git add -A >/dev/null 2>&1; true"], timeout_s=30
            )
            changed = await executor.exec(
                handle, ["sh", "-c", "git diff --cached --name-only"], timeout_s=30
            )
            tampered = sorted(
                path
                for path in changed.stdout.split("\n")
                if path.strip() and _is_test_path(path.strip())
            )
            if tampered:
                # The agent was told not to touch tests. Editing the test it was
                # asked to satisfy turns the exit-code grade into a rubber stamp,
                # so a tampered run never counts as a pass.
                passed = False

            diff_result = await executor.exec(
                handle, ["sh", "-c", "git diff --cached"], timeout_s=30
            )
            diff = diff_result.stdout
            grading_s = time.monotonic() - grading_started
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
        finally:
            if handle is not None:
                await executor.teardown(handle)

    return RunResult(
        task_id=task.id,
        repo_id=task.repo_id,
        difficulty=task.difficulty,
        run_index=run_index,
        model=model,
        passed=passed,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost_usd=cost,
        latency_s=time.monotonic() - started,
        setup_s=setup_s,
        agent_s=agent_s,
        grading_s=grading_s,
        tool_call_count=tool_call_count,
        diff=diff,
        regressions=regressions,
        error=error,
        stop_reason=stop_reason,
        transcript=transcript,
        tampered_tests=tampered,
    )


def _is_test_path(path: str) -> bool:
    """Whether `path` looks like test code the agent was supposed to leave alone."""
    parts = Path(path).parts
    name = Path(path).name
    return (
        name == "conftest.py"
        or name.startswith("test_")
        or name.endswith(("_test.py", "_test.js", "_test.go", ".test.ts", ".test.js"))
        or any(part in ("test", "tests", "__tests__", "spec") for part in parts[:-1])
    )


def _materialize_repo(repo_url: str, tmp: Path, repo_ref: str | None = None) -> Path:
    """Clone a git URL or copy a local path into `tmp`.

    `repo_ref` pins the checkout. Leaving it unset on a real repository makes
    the task irreproducible as soon as the default branch moves.

    This runs on the host, before any sandbox exists, so both arguments are
    validated on Task (see models/task.py). The measures here are the second
    layer: `--` so a value can never be read as an option, and refusing the
    `ext` transport, which runs an arbitrary command and which a user with
    protocol.ext.allow=always in their git config would otherwise get.
    """
    dest = tmp / "repo"
    source = Path(repo_url).expanduser()
    if source.is_dir():
        shutil.copytree(source, dest)
        if not (dest / ".git").exists():
            _git(dest, "init", "-q")
            _git(dest, "add", "-A")
            _git(
                dest,
                "-c",
                "user.email=bench@local",
                "-c",
                "user.name=bench",
                "commit",
                "-qm",
                "baseline",
            )
    else:
        subprocess.run(
            [
                "git",
                "-c",
                "protocol.ext.allow=never",
                "clone",
                "-q",
                "--",
                repo_url,
                str(dest),
            ],
            check=True,
        )
    if repo_ref:
        _git(dest, "checkout", "-q", repo_ref)
    return dest


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True)


async def _failing_tests(
    executor: SandboxExecutor, handle: SandboxHandle, task: Task
) -> set[str] | None:
    """Node ids of currently failing tests, via junitxml.

    Returns None when the report could not be produced or parsed — the runner
    must not read that as "nothing is failing". `--junitxml` is a pytest flag,
    so a non-pytest suite lands here and regressions are reported as undetected
    rather than silently empty.
    """
    report = "/tmp/agentgauge-junit.xml"
    # Delete first: a stale report from the baseline pass would otherwise be
    # read back as the post-fix result if this run fails to write one.
    await executor.exec(handle, ["sh", "-c", f"rm -f {report}"], timeout_s=30)
    command = f"{task.full_test_command} --junitxml={report} >/dev/null 2>&1; true"
    await executor.exec(handle, ["sh", "-c", command], TEST_TIMEOUT_S)
    try:
        xml_text = await executor.read_file(handle, report)
    except FileNotFoundError:
        return None
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None
    failing: set[str] = set()
    for testcase in root.iter("testcase"):
        node_id = f"{testcase.get('classname', '')}::{testcase.get('name', '')}"
        if any(child.tag in ("failure", "error") for child in testcase):
            failing.add(node_id)
    return failing


async def _instruction(executor: SandboxExecutor, handle: SandboxHandle, task: Task) -> str:
    listing = await executor.exec(handle, ["git", "ls-files"], timeout_s=30)
    files = listing.stdout.strip() or "(listing unavailable)"
    return (
        f"This repository has a failing test. The failing test command is:\n\n"
        f"  {task.failing_test_command}\n\n"
        f"Repository files:\n{files}\n\n"
        f"Run the failing test, read the relevant source file with read_file, "
        f"find the root cause, and apply the fix with the edit or write_file tool. "
        f"Then re-run the failing test to verify it passes. "
        f"Do not modify test files — a run that edits tests is disqualified. "
        f"Do not break any other tests (full suite: `{task.full_test_command}`)."
    )
