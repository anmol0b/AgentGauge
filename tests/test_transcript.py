"""Summarizing a loop's message history.

A pure function over a message list, so none of this needs an executor, a
provider or a container. Every limit here discards information, and each test
below is really asking the same question: *does it say what it dropped?*
"""

from agentbench.agent_loop.transcript import (
    MAX_ARG_CHARS,
    MAX_TEXT_CHARS,
    MAX_TRANSCRIPT_CHARS,
    OUTPUT_HEAD_CHARS,
    OUTPUT_TAIL_CHARS,
    compact_transcript,
)
from agentbench.models.provider import Message, ToolCall


def assistant(text="", calls=()):
    return Message(role="assistant", content=text, tool_calls=list(calls))


def tool(content, call_id=None):
    return Message(role="tool", content=content, tool_call_id=call_id)


def call(name="read_file", call_id="c1", **arguments):
    return ToolCall(id=call_id, name=name, arguments=arguments or {"path": "calc.py"})


def test_an_empty_history_is_an_empty_transcript():
    transcript = compact_transcript([])
    assert transcript.turns == []
    assert transcript.total_turns == 0


def test_a_turn_records_its_prose_and_calls():
    transcript = compact_transcript(
        [assistant("Reading the file first.", [call(name="read_file", path="calc.py")])]
    )

    assert transcript.total_turns == 1
    turn = transcript.turns[0]
    assert turn.index == 1
    assert turn.text == "Reading the file first."
    assert [c.name for c in turn.tool_calls] == ["read_file"]
    assert turn.tool_calls[0].arguments == {"path": "calc.py"}


def test_tool_results_are_matched_to_their_call_by_id():
    messages = [
        assistant("", [call(call_id="a", name="read_file"), call(call_id="b", name="run_tests")]),
        tool("exit code: 1", call_id="b"),
        tool("def subtract(a, b):", call_id="a"),
    ]
    by_name = {c.name: c for c in compact_transcript(messages).turns[0].tool_calls}

    assert by_name["read_file"].output_excerpt == "def subtract(a, b):"
    assert by_name["run_tests"].output_excerpt == "exit code: 1"


def test_a_result_with_an_unknown_id_falls_back_to_order():
    """Output is the part worth keeping, so an id we do not recognise must not
    cost us the result."""
    messages = [assistant("", [call(call_id="a")]), tool("contents", call_id="mismatched")]

    assert compact_transcript(messages).turns[0].tool_calls[0].output_excerpt == "contents"


def test_a_turn_with_no_tool_calls_is_still_a_turn():
    transcript = compact_transcript([assistant("The fix is to change a+b to a-b.")])
    assert transcript.total_turns == 1
    assert transcript.turns[0].tool_calls == []


def test_a_stray_tool_result_is_ignored_rather_than_crashing():
    assert compact_transcript([tool("orphan")]).turns == []


def test_a_nudge_is_recorded_against_the_turn_it_followed():
    """ "Described the fix and had to be prodded" explains a run that otherwise
    just looks slow."""
    messages = [
        Message(role="user", content="fix the failing test"),  # the instruction
        assistant("The fix is to change a+b to a-b."),
        Message(role="user", content="You have not modified any files yet."),
        assistant("", [call(name="edit")]),
    ]
    turns = compact_transcript(messages).turns

    assert turns[0].nudged is True
    assert turns[1].nudged is False


def test_the_opening_instruction_is_not_mistaken_for_a_nudge():
    messages = [Message(role="user", content="fix it"), assistant("on it")]
    assert compact_transcript(messages).turns[0].nudged is False


# --- clipping: each limit has to admit what it dropped --------------------


def test_long_prose_is_clipped_and_says_how_long_it_was():
    text = "x" * (MAX_TEXT_CHARS + 500)
    clipped = compact_transcript([assistant(text)]).turns[0].text

    assert len(clipped) < len(text)
    assert str(len(text)) in clipped  # the true length survives


def test_long_arguments_are_clipped():
    huge = "y" * (MAX_ARG_CHARS + 100)
    arguments = (
        compact_transcript([assistant("", [call(name="write_file", content=huge)])])
        .turns[0]
        .tool_calls[0]
        .arguments
    )

    assert len(arguments["content"]) < len(huge)
    assert str(len(huge)) in arguments["content"]


def test_output_keeps_its_tail_because_that_is_where_failures_are():
    body = "collected 5 items\n" + "." * 5000 + "\nE   assert 3 == 6"
    record = (
        compact_transcript([assistant("", [call(name="run_tests")]), tool(body, call_id="c1")])
        .turns[0]
        .tool_calls[0]
    )

    assert "collected 5 items" in record.output_excerpt  # head
    assert "assert 3 == 6" in record.output_excerpt  # tail — the useful part
    assert "omitted" in record.output_excerpt
    assert record.output_chars == len(body)  # true size recorded separately


def test_short_output_is_kept_whole():
    body = "exit code: 0"
    record = (
        compact_transcript([assistant("", [call()]), tool(body, call_id="c1")])
        .turns[0]
        .tool_calls[0]
    )

    assert record.output_excerpt == body
    assert "omitted" not in record.output_excerpt


def test_an_output_at_the_boundary_is_not_clipped():
    body = "z" * (OUTPUT_HEAD_CHARS + OUTPUT_TAIL_CHARS)
    record = (
        compact_transcript([assistant("", [call()]), tool(body, call_id="c1")])
        .turns[0]
        .tool_calls[0]
    )

    assert record.output_excerpt == body


# --- the whole-transcript budget ------------------------------------------


def _long_run(turns: int) -> list[Message]:
    messages: list[Message] = []
    for i in range(turns):
        messages.append(assistant("t" * MAX_TEXT_CHARS, [call(call_id=f"c{i}", name="run_tests")]))
        messages.append(tool("o" * 1000, call_id=f"c{i}"))
    return messages


def test_a_long_run_is_capped_and_says_how_many_turns_it_dropped():
    transcript = compact_transcript(_long_run(40))

    assert transcript.total_turns == 40
    assert transcript.elided_turns > 0
    assert len(transcript.turns) == 40 - transcript.elided_turns
    assert len(transcript.model_dump_json()) <= MAX_TRANSCRIPT_CHARS


def test_elision_keeps_the_start_and_the_end():
    """The first turns show the approach, the last show how it ended."""
    transcript = compact_transcript(_long_run(40))
    kept = [turn.index for turn in transcript.turns]

    assert kept[0] == 1
    assert kept[-1] == 40
    assert kept == sorted(kept)  # order preserved, only the middle removed


def test_a_short_run_is_left_alone():
    transcript = compact_transcript(_long_run(3))
    assert transcript.elided_turns == 0
    assert [turn.index for turn in transcript.turns] == [1, 2, 3]
