"""Turn the agent loop's message history into a record worth keeping.

The loop produces the full conversation and `run_once` used to throw it away,
leaving a failed run explainable only by its diff and stop reason. This keeps
what a person actually reads when asking "what did it do" — the sequence of
tool calls and enough of each result to recognise it — at a size that can be
stored for every run.

Every limit below discards information, so every one of them records what it
discarded.
"""

from agentgauge.models.provider import Message
from agentgauge.models.run import ToolCallRecord, Transcript, TurnRecord

MAX_TEXT_CHARS = 500  # assistant prose: enough to see the intent
MAX_ARG_CHARS = 200  # a path, or the opening of a patch
# A pytest failure's useful part is at the *end*, so a head-only excerpt would
# throw away the assertion and keep the collection banner.
OUTPUT_HEAD_CHARS = 200
OUTPUT_TAIL_CHARS = 200
# Whole-transcript budget. At this size a hundred runs is a couple of MB, and
# the report can load every transcript without a second query.
MAX_TRANSCRIPT_CHARS = 16_000
# Turns kept either side when eliding: the first show the approach, the last
# show how it ended.
KEEP_HEAD_TURNS = 2
KEEP_TAIL_TURNS = 4


def compact_transcript(messages: list[Message]) -> Transcript:
    """Summarize a loop's message history.

    Each assistant message opens a turn; the tool results that follow are
    matched to its calls by `tool_call_id`, falling back to order when a
    provider does not echo one back.
    """
    turns: list[TurnRecord] = []
    # tool_call_id -> the record awaiting its output
    awaiting: dict[str, ToolCallRecord] = {}

    for message in messages:
        if message.role == "assistant":
            turn = TurnRecord(
                index=len(turns) + 1,
                text=_clip(message.content, MAX_TEXT_CHARS),
                tool_calls=[
                    ToolCallRecord(
                        name=call.name,
                        arguments={
                            key: _clip(str(value), MAX_ARG_CHARS)
                            for key, value in call.arguments.items()
                        },
                        output_excerpt="",
                        output_chars=0,
                    )
                    for call in message.tool_calls
                ],
            )
            turns.append(turn)
            awaiting = {
                call.id: record
                for call, record in zip(message.tool_calls, turn.tool_calls, strict=True)
                if call.id
            }

        elif message.role == "tool":
            record = awaiting.pop(message.tool_call_id or "", None) or _next_unfilled(turns)
            if record is not None:
                record.output_excerpt = _excerpt(message.content)
                record.output_chars = len(message.content)

        elif message.role == "user" and turns:
            # The only user message the loop appends after the instruction is
            # the no-edit nudge (see agent_loop/loop.py).
            turns[-1].nudged = True

    return _fit(turns)


def _next_unfilled(turns: list[TurnRecord]) -> ToolCallRecord | None:
    """The first call on the latest turn still waiting for its output.

    Used when a tool result carries no id we recognise — rather than dropping
    the output, which is the part worth having.
    """
    if not turns:
        return None
    for record in turns[-1].tool_calls:
        if not record.output_excerpt and record.output_chars == 0:
            return record
    return None


def _fit(turns: list[TurnRecord]) -> Transcript:
    """Drop turns from the middle until the transcript fits its budget."""
    transcript = Transcript(turns=turns, total_turns=len(turns))
    keep = KEEP_HEAD_TURNS + KEEP_TAIL_TURNS

    while len(transcript.turns) > keep and _size(transcript) > MAX_TRANSCRIPT_CHARS:
        del transcript.turns[len(transcript.turns) // 2]
        transcript.elided_turns += 1

    return transcript


def _size(transcript: Transcript) -> int:
    return len(transcript.model_dump_json())


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]}… ({len(text)} chars total)"


def _excerpt(text: str) -> str:
    """Head and tail, so a test failure's assertion survives."""
    if len(text) <= OUTPUT_HEAD_CHARS + OUTPUT_TAIL_CHARS:
        return text
    omitted = len(text) - OUTPUT_HEAD_CHARS - OUTPUT_TAIL_CHARS
    return f"{text[:OUTPUT_HEAD_CHARS]}\n… ({omitted} chars omitted) …\n{text[-OUTPUT_TAIL_CHARS:]}"
