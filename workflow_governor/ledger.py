"""SQLite event ledger for workflow drafts, runs, permits, and hook state."""

from __future__ import annotations

import json
import os
import sqlite3
import stat
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .contracts import ContractError, canonical_json, digest_json, validate_identifier


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Ledger:
    """Transactional local state stored outside Git under PLUGIN_DATA."""

    def __init__(self, data_dir: Path | None = None) -> None:
        configured = data_dir or Path(os.environ.get("PLUGIN_DATA", Path.home() / ".codex" / "workflow-governor-data"))
        self.data_dir = configured.expanduser().resolve()
        self._ensure_private_directory(self.data_dir)
        self.path = self.data_dir / "workflow-governor.sqlite3"
        self._ensure_private_file(self.path)
        self._initialize()
        self._secure_database_files()

    @staticmethod
    def _ensure_private_directory(path: Path) -> None:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        if path.is_symlink() or not path.is_dir():
            raise OSError(f"private data path is not a directory: {path}")
        path.chmod(0o700)

    @staticmethod
    def _ensure_private_file(path: Path) -> None:
        if path.is_symlink():
            raise OSError(f"private data file must not be a symlink: {path}")
        flags = os.O_RDWR | os.O_CREAT
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags, 0o600)
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise OSError(f"private data file is not regular: {path}")
            os.fchmod(descriptor, 0o600)
        finally:
            os.close(descriptor)

    def _secure_database_files(self) -> None:
        for path in (self.path, Path(f"{self.path}-wal"), Path(f"{self.path}-shm"), Path(f"{self.path}-journal")):
            if path.exists() or path.is_symlink():
                self._ensure_private_file(path)

    def _ensure_private_parent(self, path: Path) -> Path:
        resolved = path.expanduser().resolve(strict=False)
        if resolved == self.data_dir or self.data_dir not in resolved.parents:
            raise ContractError(str(path), "private data path escapes PLUGIN_DATA")
        relative_parent = resolved.parent.relative_to(self.data_dir)
        current = self.data_dir
        self._ensure_private_directory(current)
        for part in relative_parent.parts:
            current /= part
            self._ensure_private_directory(current)
        return resolved

    def write_private_json(self, path: Path, value: Any) -> None:
        """Atomically write sensitive runtime JSON with private permissions."""

        target = self._ensure_private_parent(path)
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
        temporary = Path(temporary_name)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(value, handle, ensure_ascii=False, sort_keys=True, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
            target.chmod(0o600)
        finally:
            if temporary.exists():
                temporary.unlink()

    def connect(self) -> sqlite3.Connection:
        self._ensure_private_file(self.path)
        connection = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA busy_timeout = 15000")
        self._secure_database_files()
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
            self._secure_database_files()

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
        workflow_id = validate_identifier(workflow_id, "workflow_id")
        repository_key = digest_json({"repository": str(Path(repository).resolve())})[:20]
        path = (self.data_dir / "drafts" / repository_key / workflow_id / "draft.json").resolve(strict=False)
        if self.data_dir not in path.parents:
            raise ContractError("workflow_id", "draft path escapes PLUGIN_DATA")
        return path

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
