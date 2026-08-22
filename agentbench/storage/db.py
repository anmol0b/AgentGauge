"""SQLite storage for run history. Stores run metadata only — never API keys."""

import json
import sqlite3
from pathlib import Path

from agentbench.models.run import RunResult

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    task_id TEXT NOT NULL,
    run_index INTEGER NOT NULL,
    model TEXT NOT NULL,
    passed INTEGER NOT NULL,
    tokens_in INTEGER NOT NULL,
    tokens_out INTEGER NOT NULL,
    cost_usd REAL NOT NULL,
    latency_s REAL NOT NULL,
    tool_call_count INTEGER NOT NULL,
    diff TEXT NOT NULL,
    regressions TEXT NOT NULL,
    error TEXT
);
"""


class RunStore:
    def __init__(self, db_path: Path | str = "agentbench.db") -> None:
        self._conn = sqlite3.connect(str(db_path))
        self._conn.execute(_SCHEMA)
        self._conn.commit()

    def insert(self, result: RunResult) -> None:
        self._conn.execute(
            """INSERT INTO runs
               (task_id, run_index, model, passed, tokens_in, tokens_out,
                cost_usd, latency_s, tool_call_count, diff, regressions, error)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                result.task_id, result.run_index, result.model, int(result.passed),
                result.tokens_in, result.tokens_out, result.cost_usd,
                result.latency_s, result.tool_call_count, result.diff,
                json.dumps(result.regressions), result.error,
            ),
        )
        self._conn.commit()

    def insert_many(self, results: list[RunResult]) -> None:
        for result in results:
            self.insert(result)

    def all_runs(self) -> list[RunResult]:
        rows = self._conn.execute(
            """SELECT task_id, run_index, model, passed, tokens_in, tokens_out,
                      cost_usd, latency_s, tool_call_count, diff, regressions, error
               FROM runs ORDER BY id"""
        ).fetchall()
        return [
            RunResult(
                task_id=r[0], run_index=r[1], model=r[2], passed=bool(r[3]),
                tokens_in=r[4], tokens_out=r[5], cost_usd=r[6], latency_s=r[7],
                tool_call_count=r[8], diff=r[9], regressions=json.loads(r[10]),
                error=r[11],
            )
            for r in rows
        ]

    def close(self) -> None:
        self._conn.close()
