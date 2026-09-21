import sqlite3
from typing import Any

from agentbench.models.run import RunResult
from agentbench.storage.db import RunStore


def _result(**overrides):
    base: dict[str, Any] = dict(
        task_id="t1",
        run_index=0,
        model="claude-sonnet-4-5",
        passed=True,
        tokens_in=100,
        tokens_out=50,
        cost_usd=0.01,
        latency_s=12.0,
        tool_call_count=3,
        diff="+fix",
        regressions=["pkg::test_broke"],
        error=None,
        stop_reason="end_turn",
        tampered_tests=[],
    )
    return RunResult(**(base | overrides))


def test_round_trip(tmp_path):
    store = RunStore(tmp_path / "runs.db")
    store.insert_many([_result(), _result(run_index=1, passed=False, error="boom")])
    store.close()

    reopened = RunStore(tmp_path / "runs.db")
    runs = reopened.all_runs()
    reopened.close()

    assert len(runs) == 2
    assert runs[0] == _result()
    assert runs[1].passed is False
    assert runs[1].error == "boom"
    assert runs[1].regressions == ["pkg::test_broke"]


def test_new_fields_round_trip(tmp_path):
    store = RunStore(tmp_path / "runs.db")
    store.insert(_result(stop_reason="max_turns", tampered_tests=["tests/test_a.py"]))
    runs = store.all_runs()
    store.close()
    assert runs[0].stop_reason == "max_turns"
    assert runs[0].tampered_tests == ["tests/test_a.py"]


def test_run_sets_are_isolated(tmp_path):
    """Two CLI invocations must not blend into one pass rate."""
    store = RunStore(tmp_path / "runs.db")
    store.insert_many([_result(), _result(run_index=1)], run_set_id="aaa")
    store.insert_many([_result(passed=False)], run_set_id="bbb")

    first, second, everything = (
        store.runs_for_set("aaa"),
        store.runs_for_set("bbb"),
        store.all_runs(),
    )
    store.close()

    assert len(first) == 2 and all(r.run_set_id == "aaa" for r in first)
    assert len(second) == 1 and second[0].passed is False
    assert len(everything) == 3  # history is still queryable


def test_migrates_v1_database(tmp_path):
    """v1 rows are the only real evidence this tool has produced — keep them."""
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute(
        """CREATE TABLE runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            task_id TEXT NOT NULL, run_index INTEGER NOT NULL, model TEXT NOT NULL,
            passed INTEGER NOT NULL, tokens_in INTEGER NOT NULL,
            tokens_out INTEGER NOT NULL, cost_usd REAL NOT NULL,
            latency_s REAL NOT NULL, tool_call_count INTEGER NOT NULL,
            diff TEXT NOT NULL, regressions TEXT NOT NULL, error TEXT)"""
    )
    conn.execute(
        "INSERT INTO runs (task_id, run_index, model, passed, tokens_in, tokens_out,"
        " cost_usd, latency_s, tool_call_count, diff, regressions, error)"
        " VALUES ('old-task', 0, 'ollama/x', 1, 10, 5, 0.0, 9.9, 4, '', '[]', NULL)"
    )
    conn.commit()
    conn.close()

    store = RunStore(path)
    runs = store.all_runs()
    store.insert(_result())  # new schema still writable afterwards
    total = len(store.all_runs())
    store.close()

    assert len(runs) == 1
    assert runs[0].task_id == "old-task"
    assert runs[0].stop_reason is None  # unknown, not fabricated
    assert runs[0].tampered_tests is None  # never checked, not 'checked and clean'
    assert runs[0].run_set_id == ""
    assert total == 2


def test_unchecked_tampering_survives_round_trip(tmp_path):
    """None (never checked) must not collapse into [] (checked, found nothing)."""
    store = RunStore(tmp_path / "runs.db")
    store.insert(_result(run_index=0, tampered_tests=None))
    store.insert(_result(run_index=1, tampered_tests=[]))
    runs = store.all_runs()
    store.close()
    assert runs[0].tampered_tests is None
    assert runs[1].tampered_tests == []
