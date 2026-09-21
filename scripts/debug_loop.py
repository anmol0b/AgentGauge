"""Debug: run the agent loop against a live model with an in-memory executor,
printing every turn. No Docker needed.

Usage: .venv/bin/python scripts/debug_loop.py ollama/agentbench-qwen
"""

import asyncio
import json
import sys
from pathlib import Path

from agentbench.agent_loop.loop import DEFAULT_SYSTEM_PROMPT, run_agent_loop
from agentbench.providers import make_provider
from agentbench.sandbox.executor import ExecResult, SandboxHandle

FIXTURES = Path(__file__).parent.parent / "agentbench/tasks/fixtures/calc_repo"
CALC = (FIXTURES / "calc.py").read_text()
TEST = (FIXTURES / "test_calc.py").read_text()


class PrintingExecutor:
    def __init__(self) -> None:
        self.files = {"calc.py": CALC, "test_calc.py": TEST}

    async def start(self, repo_path: Path, image: str) -> SandboxHandle:
        return SandboxHandle(id="debug")

    async def exec(self, handle: SandboxHandle, command: list[str], timeout_s: int) -> ExecResult:
        print(f"  [exec] {' '.join(command)[:100]}")
        return ExecResult(exit_code=1, stdout="FAILED test_calc.py::test_subtract", stderr="")

    async def read_file(self, handle: SandboxHandle, path: str) -> str:
        print(f"  [read_file] {path}")
        if path not in self.files:
            raise FileNotFoundError(path)
        return self.files[path]

    async def write_file(self, handle: SandboxHandle, path: str, content: str) -> None:
        print(f"  [write_file] {path} ({len(content)} chars)")
        self.files[path] = content

    async def teardown(self, handle: SandboxHandle) -> None:
        pass


async def main() -> None:
    model = sys.argv[1] if len(sys.argv) > 1 else "ollama/agentbench-qwen"
    provider = make_provider(model, system=DEFAULT_SYSTEM_PROMPT)
    executor = PrintingExecutor()
    result = await run_agent_loop(
        provider,
        executor,
        await executor.start(Path("."), "debug"),
        instruction=(
            "This repository has a failing test. The failing test command is:\n\n"
            "  pytest test_calc.py::test_subtract\n\n"
            "Repository files:\ncalc.py\ntest_calc.py\n\n"
            "Run the failing test, read the relevant source file with read_file, "
            "find the root cause, and apply the fix with the edit or write_file tool. "
            "Then re-run the failing test to verify it passes."
        ),
        max_turns=6,
    )
    print(f"\nstop={result.stop_reason} tool_calls={result.tool_call_count}")
    print("--- message history ---")
    for m in result.messages:
        if m.role == "assistant" and m.tool_calls:
            for c in m.tool_calls:
                print(f"[{m.role}] TOOL {c.name}: {json.dumps(c.arguments)[:200]}")
        else:
            print(f"[{m.role}] {m.content[:200]}")
    print("--- calc.py after ---")
    print(executor.files["calc.py"])


if __name__ == "__main__":
    asyncio.run(main())
