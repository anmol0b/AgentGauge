"""Tool definitions exposed to the agent. Each handler is a thin wrapper over
SandboxExecutor — tools never touch Docker or the host filesystem directly.
"""

from typing import Any

from agentbench.models.provider import ToolSpec
from agentbench.sandbox.executor import SandboxExecutor, SandboxHandle

MAX_TOOL_OUTPUT_CHARS = 30_000

TOOLS: list[ToolSpec] = [
    ToolSpec(
        name="read_file",
        description="Read a file from the repository. Returns its full contents.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path relative to repo root"}
            },
            "required": ["path"],
        },
    ),
    ToolSpec(
        name="write_file",
        description="Write (create or overwrite) a file in the repository.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path relative to repo root"},
                "content": {"type": "string", "description": "Full file content"},
            },
            "required": ["path", "content"],
        },
    ),
    ToolSpec(
        name="edit",
        description=(
            "Replace an exact string in a file. old_string must appear exactly once. "
            "Prefer this over write_file for small changes to large files."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path relative to repo root"},
                "old_string": {"type": "string", "description": "Exact text to replace"},
                "new_string": {"type": "string", "description": "Replacement text"},
            },
            "required": ["path", "old_string", "new_string"],
        },
    ),
    ToolSpec(
        name="run_tests",
        description=(
            "Run a shell command in the sandbox (e.g. pytest). "
            "Returns exit code and output."
        ),
        parameters={
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command to run"}
            },
            "required": ["command"],
        },
    ),
]


async def execute_tool(
    name: str,
    args: dict[str, Any],
    executor: SandboxExecutor,
    handle: SandboxHandle,
    exec_timeout_s: int,
) -> str:
    try:
        if name == "read_file":
            return _truncate(await executor.read_file(handle, args["path"]))
        if name == "write_file":
            await executor.write_file(handle, args["path"], args["content"])
            return f"wrote {args['path']}"
        if name == "edit":
            return await _edit(executor, handle, args)
        if name == "run_tests":
            result = await executor.exec(
                handle, ["sh", "-c", args["command"]], timeout_s=exec_timeout_s
            )
            output = f"exit code: {result.exit_code}\n{result.stdout}{result.stderr}"
            if result.timed_out:
                output = f"TIMED OUT after {exec_timeout_s}s\n" + output
            return _truncate(output)
        return f"error: unknown tool {name!r}"
    except Exception as exc:  # tool errors go back to the agent, not the harness
        return f"error: {exc}"


async def _edit(
    executor: SandboxExecutor, handle: SandboxHandle, args: dict[str, Any]
) -> str:
    content = await executor.read_file(handle, args["path"])
    old, new = args["old_string"], args["new_string"]
    count = content.count(old)
    if count == 0:
        # include current content so the agent can self-correct whitespace drift
        hint = (
            f"\nCurrent file content:\n{content}"
            if len(content) <= 2_000
            else ""
        )
        return f"error: old_string not found in file{hint}"
    if count > 1:
        return f"error: old_string appears {count} times; make it more specific"
    await executor.write_file(handle, args["path"], content.replace(old, new, 1))
    return f"edited {args['path']}"


def _truncate(text: str) -> str:
    if len(text) > MAX_TOOL_OUTPUT_CHARS:
        return text[:MAX_TOOL_OUTPUT_CHARS] + "\n... [truncated]"
    return text
