"""run_once orchestration tests. No Docker, no API keys: the sandbox is an
in-memory fake and the provider is monkeypatched at its construction site.
"""

from pathlib import Path

from agentbench.models.provider import Message, ProviderResponse, ToolCall
from agentbench.models.run import TokenUsage
from agentbench.models.task import Task
from agentbench.orchestrator.runner import _is_test_path, run_once
from agentbench.sandbox.executor import ExecResult, SandboxHandle

_JUNIT = """<?xml version="1.0"?>
<testsuites><testsuite name="pytest">
  <testcase classname="test_calc" name="test_add"/>
  <testcase classname="test_calc" name="test_subtract"><failure>boom</failure></testcase>
</testsuite></testsuites>"""


class ScriptedExecutor:
    """Fake sandbox that answers each command run_once issues, by pattern."""

    def __init__(self, changed_files="calc.py", test_exit_code=0):
        self.changed_files = changed_files
        self.test_exit_code = test_exit_code
        self.files: dict[str, str] = {}

    async def start(self, repo_path: Path, image: str) -> SandboxHandle:
        return SandboxHandle(id="fake")

    async def exec(self, handle, command, timeout_s) -> ExecResult:
        joined = " ".join(command)
        if "--junitxml" in joined or "git add -A" in joined:
            return ExecResult(exit_code=0, stdout="", stderr="")
        if "git ls-files" in joined:
            return ExecResult(exit_code=0, stdout="calc.py\ntest_calc.py\n", stderr="")
        if "--name-only" in joined:
            return ExecResult(exit_code=0, stdout=self.changed_files, stderr="")
        if "git diff --cached" in joined:
            return ExecResult(exit_code=0, stdout="--- a/calc.py\n+++ b/calc.py\n", stderr="")
        return ExecResult(exit_code=self.test_exit_code, stdout="", stderr="")

    async def read_file(self, handle, path) -> str:
        if path.endswith("junit.xml"):
            return _JUNIT
        if path not in self.files:
            raise FileNotFoundError(path)
        return self.files[path]

    async def write_file(self, handle, path, content) -> None:
        self.files[path] = content

    async def teardown(self, handle) -> None:
        self.torn_down = True


class OneEditProvider:
    """Edits a file on turn 1, then ends the turn."""

    def __init__(self, stop_reason="end_turn"):
        self._stop_reason = stop_reason
        self.turns = 0

    async def run_agent_turn(self, messages, tools) -> ProviderResponse:
        self.turns += 1
        if self.turns == 1:
            call = ToolCall(
                id="c1", name="write_file",
                arguments={"path": "calc.py", "content": "fixed"},
            )
            return ProviderResponse(
                messages=[Message(role="assistant", content="", tool_calls=[call])],
                tool_calls=[call], usage=TokenUsage(tokens_in=10, tokens_out=5),
                stop_reason="tool_use",
            )
        return ProviderResponse(
            messages=[Message(role="assistant", content="done")], tool_calls=[],
            usage=TokenUsage(tokens_in=10, tokens_out=5), stop_reason=self._stop_reason,
        )

    def compute_cost(self, usage) -> float:
        return 0.001


def _task(tmp_path) -> Task:
    (tmp_path / "repo_src").mkdir()
    (tmp_path / "repo_src" / "calc.py").write_text("def subtract(a, b):\n    return a + b\n")
    return Task(
        id="calc-bug", repo_url=str(tmp_path / "repo_src"), setup_commands=[],
        failing_test_command="pytest test_calc.py::test_subtract",
    )


async def test_run_once_records_stop_reason(tmp_path):
    executor = ScriptedExecutor()
    result = await run_once(
        _task(tmp_path), 0, "fake-model", executor, provider=OneEditProvider()
    )

    assert result.error is None
    assert result.passed is True
    assert result.stop_reason == "end_turn"  # was previously computed then discarded
    assert result.tool_call_count == 1
    assert result.tampered_tests == []
    assert executor.torn_down is True


async def test_run_once_flags_max_turns(tmp_path):
    """A failure needs a reason: out of turns is not the same as a wrong fix."""
    result = await run_once(
        _task(tmp_path), 0, "fake-model", ScriptedExecutor(), max_turns=1,
        provider=OneEditProvider(),
    )
    assert result.stop_reason == "max_turns"


async def test_editing_tests_disqualifies_a_passing_run(tmp_path):
    """Exit code 0 is not a pass if the agent rewrote the test to get it."""
    executor = ScriptedExecutor(changed_files="calc.py\ntest_calc.py\n", test_exit_code=0)
    result = await run_once(
        _task(tmp_path), 0, "fake-model", executor, provider=OneEditProvider()
    )

    assert result.tampered_tests == ["test_calc.py"]
    assert result.passed is False


async def test_run_once_reports_harness_errors(tmp_path, monkeypatch):
    """With no provider injected, a construction failure is a run error."""

    def explode(model, system=None):
        raise RuntimeError("no api key")

    monkeypatch.setattr("agentbench.orchestrator.runner.make_provider", explode)
    executor = ScriptedExecutor()
    result = await run_once(_task(tmp_path), 0, "fake-model", executor)

    assert result.passed is False
    assert result.error is not None and "no api key" in result.error
    assert executor.torn_down is True  # teardown still runs on the error path


def test_is_test_path():
    assert _is_test_path("test_calc.py")
    assert _is_test_path("tests/helpers.py")
    assert _is_test_path("pkg/conftest.py")
    assert _is_test_path("a/b_test.py")
    assert _is_test_path("src/__tests__/cart.test.js")
    assert not _is_test_path("calc.py")
    assert not _is_test_path("src/cart.py")
    assert not _is_test_path("testing/util.py")


class NoJunitExecutor(ScriptedExecutor):
    """A suite that never produces a junit report (e.g. not pytest)."""

    async def read_file(self, handle, path) -> str:
        if path.endswith("junit.xml"):
            raise FileNotFoundError(path)
        return await super().read_file(handle, path)


class BadJunitExecutor(ScriptedExecutor):
    async def read_file(self, handle, path) -> str:
        if path.endswith("junit.xml"):
            return "<not valid xml"
        return await super().read_file(handle, path)


async def test_regressions_reported_as_undetected_when_junit_missing(tmp_path):
    """Missing report must not read as 'no regressions'."""
    result = await run_once(
        _task(tmp_path), 0, "fake-model", NoJunitExecutor(), provider=OneEditProvider()
    )
    assert result.error is None
    assert result.regressions is None


async def test_regressions_reported_as_undetected_when_junit_unparseable(tmp_path):
    result = await run_once(
        _task(tmp_path), 0, "fake-model", BadJunitExecutor(), provider=OneEditProvider()
    )
    assert result.error is None
    assert result.regressions is None


async def test_regressions_empty_when_detection_worked_and_found_none(tmp_path):
    """Baseline and after are identical here, so the list is empty, not None."""
    result = await run_once(
        _task(tmp_path), 0, "fake-model", ScriptedExecutor(), provider=OneEditProvider()
    )
    assert result.regressions == []


async def test_stale_junit_report_is_deleted_before_each_pass(tmp_path):
    """Otherwise a failed 'after' pass reads back the baseline's report."""
    executor = ScriptedExecutor()
    seen: list[str] = []
    original = executor.exec

    async def spy(handle, command, timeout_s):
        seen.append(" ".join(command))
        return await original(handle, command, timeout_s)

    executor.exec = spy
    await run_once(_task(tmp_path), 0, "fake-model", executor, provider=OneEditProvider())

    removals = [c for c in seen if "rm -f" in c and "junit" in c]
    assert len(removals) == 2  # once before the baseline pass, once before the after pass
