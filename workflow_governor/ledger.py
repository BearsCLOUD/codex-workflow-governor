"""SQLite event ledger for workflow drafts, runs, permits, and hook state."""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .contracts import canonical_json, digest_json


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Ledger:
    """Transactional local state stored outside Git under PLUGIN_DATA."""

    def __init__(self, data_dir: Path | None = None) -> None:
        configured = data_dir or Path(os.environ.get("PLUGIN_DATA", Path.home() / ".codex" / "workflow-governor-data"))
        self.data_dir = configured.expanduser().resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.data_dir / "workflow-governor.sqlite3"
        self._initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA busy_timeout = 15000")
        return connection

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_key TEXT NOT NULL UNIQUE,
                    run_id TEXT,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS heartbeats (
                    session_id TEXT NOT NULL,
                    repository TEXT NOT NULL,
                    seen_at TEXT NOT NULL,
                    PRIMARY KEY (session_id, repository)
                );
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    repository TEXT NOT NULL,
                    workflow_id TEXT NOT NULL,
                    requested_mode TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    status TEXT NOT NULL,
                    lock_digest TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    session_id TEXT,
                    current_node TEXT NOT NULL,
                    violation_reason TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS runs_repository_status ON runs(repository, status);
                CREATE TABLE IF NOT EXISTS node_states (
                    run_id TEXT NOT NULL,
                    node_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    result_json TEXT,
                    evidence_json TEXT NOT NULL DEFAULT '[]',
                    PRIMARY KEY (run_id, node_id),
                    FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS permits (
                    permit_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    node_id TEXT NOT NULL,
                    role_id TEXT NOT NULL,
                    arguments_digest TEXT NOT NULL,
                    dispatch_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    agent_id TEXT,
                    tool_use_id TEXT,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    consumed_at TEXT,
                    result_at TEXT,
                    FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS permits_pending_digest ON permits(arguments_digest, status);
                """
            )

    @staticmethod
    def record_event(
        connection: sqlite3.Connection,
        event_type: str,
        payload: dict[str, Any],
        *,
        run_id: str | None = None,
        event_key: str | None = None,
    ) -> None:
        key = event_key or digest_json({"event_type": event_type, "run_id": run_id, "payload": payload})
        connection.execute(
            "INSERT OR IGNORE INTO events(event_key, run_id, event_type, payload_json, created_at) VALUES (?, ?, ?, ?, ?)",
            (key, run_id, event_type, canonical_json(payload), utc_now()),
        )

    def heartbeat(self, session_id: str, repository: str) -> None:
        repository = str(Path(repository).resolve())
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO heartbeats(session_id, repository, seen_at) VALUES (?, ?, ?)
                ON CONFLICT(session_id, repository) DO UPDATE SET seen_at = excluded.seen_at
                """,
                (session_id, repository, utc_now()),
            )
            self.record_event(
                connection,
                "hook.heartbeat",
                {"session_id": session_id, "repository": repository},
                event_key=f"heartbeat:{session_id}:{repository}:{utc_now()[:16]}",
            )

    def latest_session(self, repository: str) -> sqlite3.Row | None:
        repository = str(Path(repository).resolve())
        with self.connect() as connection:
            return connection.execute(
                "SELECT session_id, repository, seen_at FROM heartbeats WHERE repository = ? ORDER BY seen_at DESC LIMIT 1",
                (repository,),
            ).fetchone()

    def draft_path(self, repository: str, workflow_id: str) -> Path:
        repository_key = digest_json({"repository": str(Path(repository).resolve())})[:20]
        return self.data_dir / "drafts" / repository_key / workflow_id / "draft.json"

    def export_events(self, run_id: str, limit: int = 500) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT sequence, event_type, payload_json, created_at FROM events WHERE run_id = ? ORDER BY sequence LIMIT ?",
                (run_id, min(max(limit, 1), 2000)),
            ).fetchall()
        return [
            {
                "sequence": row["sequence"],
                "event_type": row["event_type"],
                "payload": json.loads(row["payload_json"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]
