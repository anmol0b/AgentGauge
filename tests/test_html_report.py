from typing import Any

from agentbench.models.run import RunResult
from agentbench.reporting.aggregate import aggregate
from agentbench.reporting.html_report import render_html


def _run(**overrides):
    base: dict[str, Any] = dict(
        task_id="t1",
        run_index=0,
        model="m1",
        passed=True,
        tokens_in=100,
        tokens_out=50,
        cost_usd=0.01,
        latency_s=10.0,
        tool_call_count=3,
        diff="--- a/calc.py",
        regressions=[],
    )
    return RunResult(**(base | overrides))


def test_report_escapes_model_authored_content(tmp_path):
    """Diffs are written by the model under test — untrusted template input."""
    runs = [_run(diff="</pre><script>alert(1)</script>")]
    out = render_html(aggregate(runs), tmp_path / "r.html")
    html = out.read_text()
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_report_surfaces_new_signals(tmp_path):
    runs = [
        _run(run_index=0, stop_reason="max_turns", passed=False),
        _run(run_index=1, passed=False, tampered_tests=["test_calc.py"]),
        _run(run_index=2, passed=False, error="DockerException: boom"),
    ]
    html = render_html(aggregate(runs), tmp_path / "r.html", concurrency=4).read_text()
    assert "max_turns" in html
    assert "test_calc.py" in html
    assert "errored" in html
    assert "concurrently" in html  # latency caveat disclosed


def test_report_renders_na_when_every_run_errored(tmp_path):
    report = aggregate([_run(passed=False, error="boom")])
    html = render_html(report, tmp_path / "r.html").read_text()
    # never present a harness outage as a 0% score
    assert report.variance[0].pass_rate is None
    assert '<span class="muted">n/a</span>' in html
    assert "100%" in html  # shown as a 100% error rate instead


def test_unchecked_runs_are_labelled_not_checked(tmp_path):
    clean = render_html(aggregate([_run(tampered_tests=[])]), tmp_path / "a.html").read_text()
    unchecked = render_html(aggregate([_run(tampered_tests=None)]), tmp_path / "b.html").read_text()
    cell = '<td class="muted">not checked</td>'
    assert cell not in clean
    assert cell in unchecked


def test_report_shows_a_model_leaderboard_with_intervals(tmp_path):
    runs = [_run(model="m1", run_index=i, passed=i < 2) for i in range(3)]
    runs += [_run(model="m2", run_index=i, passed=False) for i in range(3)]
    html = render_html(aggregate(runs), tmp_path / "r.html").read_text()
    assert "<h2>Models</h2>" in html
    assert "95% CI" in html
    assert "m1" in html and "m2" in html


def test_report_breaks_down_by_difficulty(tmp_path):
    runs = [
        _run(run_index=0, difficulty="easy", passed=True),
        _run(run_index=1, difficulty="hard", passed=False),
    ]
    html = render_html(aggregate(runs), tmp_path / "r.html").read_text()
    assert "By difficulty" in html
    assert "hard" in html


def test_difficulty_section_omitted_when_unknown(tmp_path):
    html = render_html(aggregate([_run()]), tmp_path / "r.html").read_text()
    assert "By difficulty" not in html


def _repo_run(repo_id, task_id, **overrides):
    run = _run(task_id=task_id, **overrides)
    run.repo_id = repo_id
    return run


def test_report_has_a_repositories_section(tmp_path):
    runs = [_repo_run("alpha", "a1"), _repo_run("beta", "b1", run_index=1)]
    html = render_html(aggregate(runs), tmp_path / "r.html").read_text()
    assert "<h2>Repositories</h2>" in html
    assert "alpha" in html and "beta" in html


def test_repositories_section_omitted_when_no_run_has_a_repo(tmp_path):
    html = render_html(aggregate([_run()]), tmp_path / "r.html").read_text()
    assert "<h2>Repositories</h2>" not in html


def test_report_refuses_to_rank_repos_run_against_different_models(tmp_path):
    """A repo tested only with a weak model looks hostile to agents when the
    difference is the model."""
    mixed = [
        _repo_run("alpha", "a1", model="m1"),
        _repo_run("beta", "b1", model="m2", run_index=1),
    ]
    html = render_html(aggregate(mixed), tmp_path / "r.html").read_text()
    assert "not comparable to each other" in html

    same = [
        _repo_run("alpha", "a1", model="m1"),
        _repo_run("beta", "b1", model="m1", run_index=1),
    ]
    html = render_html(aggregate(same), tmp_path / "r.html").read_text()
    assert "not comparable to each other" not in html


def test_difficulty_mix_is_readable(tmp_path):
    """It used to render as the literal text 'easy&times;1'."""
    runs = [
        _repo_run("alpha", "a1", difficulty="easy"),
        _repo_run("alpha", "a2", difficulty="hard", run_index=1),
        _repo_run("alpha", "a3", difficulty="hard", run_index=2),
    ]
    html = render_html(aggregate(runs), tmp_path / "r.html").read_text()
    assert "1 easy, 2 hard" in html
    assert "&amp;times;" not in html


def _transcript(**overrides):
    from agentbench.models.run import ToolCallRecord, Transcript, TurnRecord

    base: dict[str, Any] = dict(
        turns=[
            TurnRecord(
                index=1,
                text="Reading the file.",
                tool_calls=[
                    ToolCallRecord(
                        name="read_file",
                        arguments={"path": "calc.py"},
                        output_excerpt="def subtract(a, b):",
                        output_chars=4200,
                    )
                ],
            )
        ],
        total_turns=1,
    )
    return Transcript(**(base | overrides))


def test_report_shows_what_the_agent_did(tmp_path):
    html = render_html(aggregate([_run(transcript=_transcript())]), tmp_path / "r.html").read_text()

    assert "<h2>What the agent did</h2>" in html
    assert "read_file" in html
    assert "path=calc.py" in html
    assert "Reading the file." in html


def test_report_states_the_true_output_size_beside_an_excerpt(tmp_path):
    """The excerpt is 19 chars of a 4200-char result; hiding that would make
    the excerpt look like the whole thing."""
    html = render_html(aggregate([_run(transcript=_transcript())]), tmp_path / "r.html").read_text()
    assert "4200 chars of output" in html


def test_report_says_how_many_turns_were_dropped(tmp_path):
    html = render_html(
        aggregate([_run(transcript=_transcript(total_turns=40, elided_turns=34))]),
        tmp_path / "r.html",
    ).read_text()

    assert "40 turns in total" in html
    assert "34 dropped from the middle" in html


def test_report_distinguishes_not_recorded_from_empty(tmp_path):
    from agentbench.models.run import Transcript

    missing = render_html(aggregate([_run(transcript=None)]), tmp_path / "a.html").read_text()
    empty = render_html(aggregate([_run(transcript=Transcript())]), tmp_path / "b.html").read_text()

    assert "Transcript not recorded" in missing
    assert "No turns recorded" in empty
    assert "Transcript not recorded" not in empty


def test_a_nudged_turn_is_flagged(tmp_path):
    from agentbench.models.run import TurnRecord

    transcript = _transcript(turns=[TurnRecord(index=1, text="The fix is…", nudged=True)])
    html = render_html(aggregate([_run(transcript=transcript)]), tmp_path / "r.html").read_text()

    assert "Nudged" in html


def test_transcript_content_is_escaped(tmp_path):
    """Every string in a transcript was written by the model under test."""
    from agentbench.models.run import ToolCallRecord, TurnRecord

    transcript = _transcript(
        turns=[
            TurnRecord(
                index=1,
                text="</pre><script>alert(1)</script>",
                tool_calls=[
                    ToolCallRecord(
                        name="run_tests",
                        arguments={"command": "<img onerror=x>"},
                        output_excerpt="</pre><script>alert(2)</script>",
                        output_chars=30,
                    )
                ],
            )
        ]
    )
    html = render_html(aggregate([_run(transcript=transcript)]), tmp_path / "r.html").read_text()

    assert "<script>" not in html
    assert "&lt;script&gt;" in html
