"""Codex lifecycle hook enforcement for guarded workflow runs."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .contracts import digest_json
from .ledger import Ledger, utc_now


class HookRuntime:
    def __init__(self, ledger: Ledger | None = None) -> None:
        self.ledger = ledger or Ledger()

    @staticmethod
    def _repository_matches(repository: str, cwd: str) -> bool:
        root = Path(repository).resolve()
        current = Path(cwd).resolve()
        return current == root or root in current.parents

    @staticmethod
    def _repository_for_cwd(cwd: str) -> str:
        current = Path(cwd).resolve()
        for candidate in (current, *current.parents):
            if (candidate / ".git").exists() or (candidate / ".codex" / "workflows").exists():
                return str(candidate)
        return str(current)

    def _active_runs(self, connection: Any, payload: dict[str, Any]) -> list[Any]:
        rows = connection.execute(
            "SELECT * FROM runs WHERE status = 'running' AND (session_id = ? OR session_id IS NULL) ORDER BY created_at",
            (payload.get("session_id"),),
        ).fetchall()
        return [row for row in rows if self._repository_matches(row["repository"], payload.get("cwd", "/"))]

    def _violate(self, connection: Any, run: Any, reason: str, payload: dict[str, Any]) -> None:
        connection.execute(
            "UPDATE runs SET status = 'violated', violation_reason = ?, updated_at = ? WHERE run_id = ?",
            (reason, utc_now(), run["run_id"]),
        )
        self.ledger.record_event(connection, "run.violated", {"reason": reason, "hook": payload.get("hook_event_name")}, run_id=run["run_id"])

    @staticmethod
    def _deny(reason: str) -> dict[str, Any]:
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        }

    def session_start(self, payload: dict[str, Any]) -> dict[str, Any]:
        session_id = payload.get("session_id")
        cwd = payload.get("cwd")
        if isinstance(session_id, str) and isinstance(cwd, str):
            self.ledger.heartbeat(session_id, self._repository_for_cwd(cwd))
        return {
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": "Workflow Governor hooks are active. Guarded compliance still requires a verified lock and permit-bound dispatch.",
            }
        }

    def pre_tool_use(self, payload: dict[str, Any]) -> dict[str, Any]:
        if payload.get("tool_name") != "Agent":
            return {}
        tool_input = payload.get("tool_input")
        if not isinstance(tool_input, dict):
            return self._deny("Workflow Governor could not inspect Agent arguments.")
        arguments_digest = digest_json(tool_input)
        with self.ledger.transaction() as connection:
            runs = self._active_runs(connection, payload)
            guarded = [run for run in runs if run["mode"] == "guarded"]
            permits = connection.execute(
                """
                SELECT permits.*, runs.mode, runs.status AS run_status, runs.session_id, runs.repository
                FROM permits JOIN runs ON runs.run_id = permits.run_id
                WHERE permits.arguments_digest = ? AND permits.status = 'prepared' AND runs.status = 'running'
                ORDER BY permits.created_at
                """,
                (arguments_digest,),
            ).fetchall()
            permits = [
                permit
                for permit in permits
                if self._repository_matches(permit["repository"], payload.get("cwd", "/"))
                and (permit["session_id"] in {None, payload.get("session_id")})
            ]
            if len(permits) != 1:
                if guarded:
                    reason = "Agent dispatch lacks one exact pending Workflow Governor permit."
                    for run in guarded:
                        self._violate(connection, run, reason, payload)
                    return self._deny(reason)
                for run in runs:
                    self.ledger.record_event(
                        connection,
                        "dispatch.unverified",
                        {"arguments_digest": arguments_digest, "reason": "no unique permit"},
                        run_id=run["run_id"],
                    )
                return {}
            permit = permits[0]
            if datetime.fromisoformat(permit["expires_at"]) < datetime.now(timezone.utc):
                connection.execute("UPDATE permits SET status = 'expired' WHERE permit_id = ?", (permit["permit_id"],))
                run = next((item for item in guarded if item["run_id"] == permit["run_id"]), None)
                reason = "Workflow Governor dispatch permit expired."
                if run:
                    self._violate(connection, run, reason, payload)
                    return self._deny(reason)
                return {}
            connection.execute(
                "UPDATE permits SET status = 'consumed', tool_use_id = ?, consumed_at = ? WHERE permit_id = ? AND status = 'prepared'",
                (payload.get("tool_use_id"), utc_now(), permit["permit_id"]),
            )
            self.ledger.record_event(
                connection,
                "permit.consumed",
                {"permit_id": permit["permit_id"], "tool_use_id": payload.get("tool_use_id")},
                run_id=permit["run_id"],
                event_key=f"permit-consumed:{permit['permit_id']}",
            )
        return {}

    def subagent_start(self, payload: dict[str, Any]) -> dict[str, Any]:
        agent_id = payload.get("agent_id")
        agent_type = payload.get("agent_type")
        with self.ledger.transaction() as connection:
            runs = self._active_runs(connection, payload)
            guarded = [run for run in runs if run["mode"] == "guarded"]
            candidates = connection.execute(
                """
                SELECT permits.*, runs.repository, runs.session_id
                FROM permits JOIN runs ON runs.run_id = permits.run_id
                WHERE permits.status = 'consumed' AND permits.agent_id IS NULL AND permits.role_id = ? AND runs.status = 'running'
                ORDER BY permits.consumed_at
                """,
                (agent_type,),
            ).fetchall()
            candidates = [
                permit
                for permit in candidates
                if self._repository_matches(permit["repository"], payload.get("cwd", "/"))
                and permit["session_id"] in {None, payload.get("session_id")}
            ]
            if len(candidates) != 1:
                if guarded:
                    reason = "SubagentStart could not bind the agent to one consumed permit."
                    for run in guarded:
                        self._violate(connection, run, reason, payload)
                    return {"systemMessage": reason}
                for run in runs:
                    self.ledger.record_event(connection, "subagent.unverified", {"agent_id": agent_id, "agent_type": agent_type}, run_id=run["run_id"])
                return {}
            permit = candidates[0]
            connection.execute("UPDATE permits SET status = 'bound', agent_id = ? WHERE permit_id = ?", (agent_id, permit["permit_id"]))
            self.ledger.record_event(connection, "subagent.bound", {"permit_id": permit["permit_id"], "agent_id": agent_id, "agent_type": agent_type}, run_id=permit["run_id"], event_key=f"agent-bound:{permit['permit_id']}:{agent_id}")
            dispatch = json.loads(permit["dispatch_json"])
        return {
            "hookSpecificOutput": {
                "hookEventName": "SubagentStart",
                "additionalContext": (
                    f"Workflow Governor bound this agent to permit {permit['permit_id']} for node {permit['node_id']}. "
                    f"Submit workflow-result.v1 with required outputs {json.dumps(dispatch['required_outputs'], sort_keys=True)} before stopping."
                ),
            }
        }

    def subagent_stop(self, payload: dict[str, Any]) -> dict[str, Any]:
        agent_id = payload.get("agent_id")
        with self.ledger.connect() as connection:
            permit = connection.execute(
                """
                SELECT permits.*, runs.mode, runs.status AS run_status
                FROM permits JOIN runs ON runs.run_id = permits.run_id
                WHERE permits.agent_id = ? ORDER BY permits.created_at DESC LIMIT 1
                """,
                (agent_id,),
            ).fetchone()
        if permit and permit["mode"] == "guarded" and permit["run_status"] == "running" and permit["status"] != "result-recorded":
            return {"decision": "block", "reason": f"Submit the required workflow-result.v1 packet for permit {permit['permit_id']} before stopping."}
        return {}

    def stop(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self.ledger.connect() as connection:
            runs = [run for run in self._active_runs(connection, payload) if run["mode"] == "guarded"]
            for run in runs:
                lock_path = Path(run["repository"]) / ".codex" / "workflows" / run["workflow_id"] / "workflow.lock.json"
                try:
                    lock = json.loads(lock_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    return {"decision": "block", "reason": f"Guarded run {run['run_id']} cannot finish because its lock is unavailable."}
                required = {node["id"] for node in lock.get("nodes", []) if node.get("required")}
                rows = connection.execute("SELECT node_id, status FROM node_states WHERE run_id = ?", (run["run_id"],)).fetchall()
                statuses = {row["node_id"]: row["status"] for row in rows}
                incomplete = sorted(node_id for node_id in required if statuses.get(node_id) not in {"completed", "skipped"})
                if incomplete:
                    return {"decision": "block", "reason": f"Guarded run {run['run_id']} has incomplete required nodes: {', '.join(incomplete)}."}
        return {}

    def handle(self, payload: dict[str, Any]) -> dict[str, Any]:
        event = payload.get("hook_event_name")
        handlers = {
            "SessionStart": self.session_start,
            "PreToolUse": self.pre_tool_use,
            "SubagentStart": self.subagent_start,
            "SubagentStop": self.subagent_stop,
            "Stop": self.stop,
        }
        handler = handlers.get(event)
        return handler(payload) if handler else {}
