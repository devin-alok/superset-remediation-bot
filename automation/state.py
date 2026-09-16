"""Durable state: an append-only event log plus a small issue -> session table.

Every derived number in the report comes from ``events.jsonl``. The SQLite table
holds only what the orchestrator needs to make decisions (idempotency, nudge
counts), so the two can never disagree about history.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    issue          INTEGER PRIMARY KEY,
    session_id     TEXT NOT NULL,
    session_url    TEXT,
    run_id         TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    status         TEXT NOT NULL,
    nudges         INTEGER NOT NULL DEFAULT 0,
    acus           REAL NOT NULL DEFAULT 0,
    pr_url         TEXT,
    outcome        TEXT,
    finished_at    TEXT
);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Event:
    ts: str
    run_id: str
    issue: int
    event: str
    session_id: str | None = None
    status: str | None = None
    acus: float = 0.0
    pr_url: str | None = None
    outcome: str | None = None
    detail: str | None = None


@dataclass
class SessionRecord:
    issue: int
    session_id: str
    session_url: str | None
    run_id: str
    created_at: str
    status: str
    nudges: int
    acus: float
    pr_url: str | None
    outcome: str | None
    finished_at: str | None


class Store:
    """SQLite for decisions, JSONL for history."""

    def __init__(self, state_dir: Path) -> None:
        self.dir = Path(state_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.dir / "state.db"
        self.events_path = self.dir / "events.jsonl"
        with self._conn() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # -- events ---------------------------------------------------------

    def append(self, event: Event) -> Event:
        with self.events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(event), sort_keys=True) + "\n")
        return event

    def emit(self, run_id: str, issue: int, name: str, **fields: Any) -> Event:
        return self.append(
            Event(ts=utcnow(), run_id=run_id, issue=issue, event=name, **fields)
        )

    def events(self) -> list[dict[str, Any]]:
        if not self.events_path.exists():
            return []
        with self.events_path.open(encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]

    # -- sessions -------------------------------------------------------

    def get(self, issue: int) -> SessionRecord | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM sessions WHERE issue = ?", (issue,)
            ).fetchone()
        return SessionRecord(**dict(row)) if row else None

    def all(self) -> list[SessionRecord]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM sessions ORDER BY issue").fetchall()
        return [SessionRecord(**dict(row)) for row in rows]

    def active_count(self) -> int:
        with self._conn() as conn:
            (count,) = conn.execute(
                "SELECT COUNT(*) FROM sessions WHERE finished_at IS NULL"
            ).fetchone()
        return int(count)

    def create(
        self, issue: int, session_id: str, session_url: str | None, run_id: str
    ) -> SessionRecord:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO sessions (issue, session_id, session_url, run_id,"
                " created_at, status) VALUES (?, ?, ?, ?, ?, ?)",
                (issue, session_id, session_url, run_id, utcnow(), "running"),
            )
        record = self.get(issue)
        assert record is not None
        return record

    def update(self, issue: int, **fields: Any) -> None:
        if not fields:
            return
        assignments = ", ".join(f"{key} = ?" for key in fields)
        with self._conn() as conn:
            conn.execute(
                f"UPDATE sessions SET {assignments} WHERE issue = ?",
                (*fields.values(), issue),
            )


def new_run_id() -> str:
    return uuid.uuid4().hex[:12]
