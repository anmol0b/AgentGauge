"""SQLite storage for run history. Stores run metadata only — never API keys."""

import json
import sqlite3
from pathlib import Path

from agentbench.models.run import RunResult

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    run_set_id TEXT NOT NULL DEFAULT '',
    task_id TEXT NOT NULL,
    run_index INTEGER NOT NULL,
    model TEXT NOT NULL,
    passed INTEGER NOT NULL,
    tokens_in INTEGER NOT NULL,
    tokens_out INTEGER NOT NULL,
    cost_usd REAL,
    latency_s REAL NOT NULL,
    tool_call_count INTEGER NOT NULL,
    diff TEXT NOT NULL,
    regressions TEXT,
    error TEXT,
    stop_reason TEXT,
    tampered_tests TEXT
);
"""

# Columns added after v1 shipped. Databases written by the original schema are
# migrated in place on open rather than discarded — the existing run history is
# the only real evidence this tool has produced.
_MIGRATIONS = {
    "run_set_id": "TEXT NOT NULL DEFAULT ''",
    "stop_reason": "TEXT",
    "tampered_tests": "TEXT",
}

_COLUMNS = (
    "task_id, run_index, model, passed, tokens_in, tokens_out, cost_usd, "
    "latency_s, tool_call_count, diff, regressions, error, stop_reason, "
    "tampered_tests, run_set_id"
)


def _dump_or_null(value: list[str] | None) -> str | None:
    """Preserve the unknown/empty distinction (None vs []) through SQL."""
    return None if value is None else json.dumps(value)


class RunStore:
    def __init__(self, db_path: Path | str = "agentbench.db") -> None:
        self._conn = sqlite3.connect(str(db_path))
        self._conn.execute(_SCHEMA)
        self._migrate()
        self._conn.commit()

    def _migrate(self) -> None:
        columns = self._conn.execute("PRAGMA table_info(runs)").fetchall()
        existing = {row[1] for row in columns}
        for column, spec in _MIGRATIONS.items():
            if column not in existing:
                self._conn.execute(f"ALTER TABLE runs ADD COLUMN {column} {spec}")
        relaxed = {"cost_usd", "regressions"}
        if any(row[1] in relaxed and row[3] for row in columns):
            self._rebuild_for_nullable_columns()

    def _rebuild_for_nullable_columns(self) -> None:
        """Drop NOT NULL on cost_usd and regressions — SQLite needs a rebuild.

        Both columns now carry a real "unknown": an unpriced model records no
        cost rather than a guess, and a suite whose junit report is missing
        records no regression list rather than an empty one.
        """
        self._conn.execute("ALTER TABLE runs RENAME TO runs_old")
        self._conn.execute(_SCHEMA)
        self._conn.execute(
            f"INSERT INTO runs (created_at, {_COLUMNS}) "
            f"SELECT created_at, {_COLUMNS} FROM runs_old"
        )
        self._conn.execute("DROP TABLE runs_old")

    def insert(self, result: RunResult, run_set_id: str = "") -> None:
        self._conn.execute(
            f"INSERT INTO runs ({_COLUMNS}) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                result.task_id, result.run_index, result.model, int(result.passed),
                result.tokens_in, result.tokens_out, result.cost_usd,
                result.latency_s, result.tool_call_count, result.diff,
                _dump_or_null(result.regressions), result.error, result.stop_reason,
                _dump_or_null(result.tampered_tests),
                run_set_id or result.run_set_id,
            ),
        )
        self._conn.commit()

    def insert_many(self, results: list[RunResult], run_set_id: str = "") -> None:
        for result in results:
            self.insert(result, run_set_id)

    def all_runs(self) -> list[RunResult]:
        return self._select("SELECT {cols} FROM runs ORDER BY id")

    def runs_for_set(self, run_set_id: str) -> list[RunResult]:
        """Only the runs from one CLI invocation.

        Reporting over `all_runs()` silently blends separate benchmark sessions
        into one pass rate, so the CLI reports on a single set by default.
        """
        return self._select(
            "SELECT {cols} FROM runs WHERE run_set_id = ? ORDER BY id", (run_set_id,)
        )

    def _select(self, sql: str, params: tuple = ()) -> list[RunResult]:
        rows = self._conn.execute(sql.format(cols=_COLUMNS), params).fetchall()
        return [
            RunResult(
                task_id=r[0], run_index=r[1], model=r[2], passed=bool(r[3]),
                tokens_in=r[4], tokens_out=r[5], cost_usd=r[6], latency_s=r[7],
                tool_call_count=r[8], diff=r[9],
                regressions=json.loads(r[10]) if r[10] is not None else None,
                error=r[11], stop_reason=r[12],
                tampered_tests=json.loads(r[13]) if r[13] is not None else None,
                run_set_id=r[14] or "",
            )
            for r in rows
        ]

    def close(self) -> None:
        self._conn.close()
