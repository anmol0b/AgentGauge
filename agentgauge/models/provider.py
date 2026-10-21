"""Provider-facing message/tool schemas (normalized across providers)."""

from typing import Any, Literal

from pydantic import BaseModel

from agentgauge.models.run import TokenUsage

StopReason = Literal["end_turn", "tool_use", "max_tokens", "error"]


class ToolSpec(BaseModel):
    """Provider-neutral tool definition."""

    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema object


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any]
    # Opaque provider data that must be echoed back with this call on the
    # next turn (Gemini's thought_signature rejects the request otherwise).
    # Never interpreted, and kept out of transcripts.
    provider_extra: dict[str, Any] | None = None


class Message(BaseModel):
    role: Literal["user", "assistant", "tool"]
    content: str
    tool_calls: list[ToolCall] = []
    # for role == "tool": which call this result answers
    tool_call_id: str | None = None


class ProviderResponse(BaseModel):
    """One agent turn, normalized regardless of provider-native format."""

    messages: list[Message]  # assistant message(s) produced this turn
    tool_calls: list[ToolCall]
    usage: TokenUsage
    stop_reason: StopReason
