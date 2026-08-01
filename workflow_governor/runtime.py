"""Workflow lifecycle and guarded execution runtime."""

from __future__ import annotations

import json
import os
import re
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .compiler import compile_source, render_mermaid, render_workflow, verify_generated_views
from .contracts import (
    DISPATCH_SCHEMA,
    RESULT_SCHEMA,
    ContractError,
    decode_dispatch,
    decode_lock,
    decode_result,
    decode_source,
    digest_bytes,
    digest_json,
    load_json,
    validate_typed_values,
)
from .ledger import Ledger, utc_now


MANAGED_START = "<!-- workflow-governor:start -->"
MANAGED_END = "<!-- workflow-governor:end -->"
MANAGED_ROUTING = """<!-- workflow-governor:start -->
## Workflow Governor Routing

- Use the `workflow-run` skill with an explicit workflow ID for governed execution.
- Treat a workflow lock as runtime authority only when every recorded digest verifies.
- Treat a guarded run as compliant only when its status is `completed`.
- Follow applicable Git instructions without creating plugin-owned Git policy.
<!-- workflow-governor:end -->
"""


def _repository(value: str) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.is_dir():
        raise ContractError("repository", f"not a directory: {path}")
    return path


def _workflow_dir(repository: Path, workflow_id: str) -> Path:
    return repository / ".codex" / "workflows" / workflow_id


def _lock(repository: Path, workflow_id: str) -> dict[str, Any]:
    lock = load_json(_workflow_dir(repository, workflow_id) / "workflow.lock.json")
    return decode_lock(lock)


def _source(repository: Path, workflow_id: str) -> dict[str, Any]:
    return decode_source(load_json(_workflow_dir(repository, workflow_id) / "workflow.json"))


def _workflow_ids(repository: Path) -> list[str]:
    root = repository / ".codex" / "workflows"
    if not root.is_dir():
        return []
    return sorted(path.parent.name for path in root.glob("*/workflow.json"))


def _managed_agents_text(existing: str) -> str:
    if MANAGED_START in existing or MANAGED_END in existing:
        if existing.count(MANAGED_START) != 1 or existing.count(MANAGED_END) != 1:
            raise ContractError("AGENTS.md", "contains malformed Workflow Governor routing markers")
        start = existing.index(MANAGED_START)
        end = existing.index(MANAGED_END, start) + len(MANAGED_END)
        replacement = MANAGED_ROUTING.rstrip("\n")
        return existing[:start] + replacement + existing[end:]
    if existing and not existing.endswith("\n"):
        existing += "\n"
    if existing:
        existing += "\n"
    return existing + MANAGED_ROUTING


def _atomic_replace(repository: Path, payloads: dict[Path, bytes]) -> None:
    originals: dict[Path, bytes | None] = {}
    staged: dict[Path, Path] = {}
    token = secrets.token_hex(8)
    try:
        for target, payload in payloads.items():
            resolved = target.resolve(strict=False)
            root = repository.resolve()
            if resolved != root and root not in resolved.parents:
                raise ContractError(str(target), "target escapes repository")
            target.parent.mkdir(parents=True, exist_ok=True)
            originals[target] = target.read_bytes() if target.exists() else None
            stage = target.with_name(f".{target.name}.workflow-governor-{token}")
            stage.write_bytes(payload)
            staged[target] = stage
        for target in sorted(staged, key=lambda item: item.as_posix()):
            os.replace(staged[target], target)
    except Exception:
        for target, original in originals.items():
            try:
                if original is None:
                    if target.exists():
                        target.unlink()
                else:
                    target.write_bytes(original)
            except OSError:
                pass
        raise
    finally:
        for stage in staged.values():
            if stage.exists():
                stage.unlink()


class WorkflowRuntime:
    def __init__(self, ledger: Ledger | None = None) -> None:
        self.ledger = ledger or Ledger()

    def list_workflows(self, repository: str) -> dict[str, Any]:
        root = _repository(repository)
        workflows = []
        for workflow_id in _workflow_ids(root):
            item: dict[str, Any] = {"workflow_id": workflow_id}
            try:
                lock = _lock(root, workflow_id)
                item.update({"revision": lock["revision"], "lock_digest": lock["lock_digest"], "valid_lock": True})
            except (ContractError, OSError) as exc:
                item.update({"valid_lock": False, "error": str(exc)})
            workflows.append(item)
        return {"repository": str(root), "workflows": workflows}

    def get_workflow(self, repository: str, workflow_id: str) -> dict[str, Any]:
        root = _repository(repository)
        return {"repository": str(root), "source": _source(root, workflow_id), "lock": _lock(root, workflow_id)}

    def check_workflow(self, repository: str, workflow_id: str) -> dict[str, Any]:
        root = _repository(repository)
        try:
            errors = verify_generated_views(root, workflow_id)
            return {"ok": not errors, "deterministic_errors": errors}
        except (ContractError, OSError) as exc:
            return {"ok": False, "deterministic_errors": [str(exc)]}

    def analyze_workflow(self, repository: str, workflow_id: str) -> dict[str, Any]:
        root = _repository(repository)
        check = self.check_workflow(str(root), workflow_id)
        recommendations: list[str] = []
        try:
            lock = _lock(root, workflow_id)
            used_roles = {route["role_id"] for route in lock["routes"]}
            declared_roles = {role["role_id"] for role in lock["role_refs"]}
            for role in sorted(declared_roles - used_roles):
                recommendations.append(f"Role {role} is declared but has no route.")
            if not lock["instruction_rules"]:
                recommendations.append("Declare instruction rule coverage before relying on repository governance.")
            for node in lock["nodes"]:
                if node["type"] == "decision":
                    guards = [edge["guard"]["kind"] for edge in lock["edges"] if edge["from"] == node["id"]]
                    if guards and all(kind == "always" for kind in guards):
                        recommendations.append(f"Decision node {node['id']} uses only unconditional routes.")
        except (ContractError, OSError):
            pass
        return {
            "ok": check["ok"],
            "deterministic_errors": check["deterministic_errors"],
            "advisory_recommendations": recommendations,
        }

    def workflow_status(
        self,
        repository: str,
        workflow_id: str | None = None,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        root = str(_repository(repository))
        query = "SELECT * FROM runs WHERE repository = ?"
        values: list[Any] = [root]
        if workflow_id:
            query += " AND workflow_id = ?"
            values.append(workflow_id)
        if run_id:
            query += " AND run_id = ?"
            values.append(run_id)
        query += " ORDER BY created_at DESC LIMIT 100"
        with self.ledger.connect() as connection:
            rows = connection.execute(query, values).fetchall()
            runs = []
            for row in rows:
                node_rows = connection.execute(
                    "SELECT node_id, status, attempts, evidence_json FROM node_states WHERE run_id = ? ORDER BY node_id",
                    (row["run_id"],),
                ).fetchall()
                item = dict(row)
                item["nodes"] = [
                    {
                        "node_id": node["node_id"],
                        "status": node["status"],
                        "attempts": node["attempts"],
                        "evidence": json.loads(node["evidence_json"]),
                    }
                    for node in node_rows
                ]
                runs.append(item)
        return {"repository": root, "runs": runs}

    def export_state(self, repository: str, workflow_id: str | None = None, run_id: str | None = None) -> dict[str, Any]:
        root = str(_repository(repository))
        result: dict[str, Any] = {"repository": root}
        if workflow_id:
            result["workflow"] = self.get_workflow(root, workflow_id)
        if run_id:
            result["status"] = self.workflow_status(root, run_id=run_id)
            result["events"] = self.ledger.export_events(run_id)
        if not workflow_id and not run_id:
            result["workflows"] = self.list_workflows(root)["workflows"]
        return result

    def save_draft(
        self,
        repository: str,
        workflow_id: str,
        source: dict[str, Any],
        role_files: dict[str, str],
    ) -> dict[str, Any]:
        root = _repository(repository)
        normalized = decode_source(source)
        if normalized["workflow_id"] != workflow_id:
            raise ContractError("workflow_id", "does not match source.workflow_id")
        allowed_role_paths = {role["path"]: role for role in normalized["role_refs"]}
        unknown = sorted(set(role_files) - set(allowed_role_paths))
        if unknown:
            raise ContractError("role_files", f"unknown role paths: {', '.join(unknown)}")
        for relative, role in allowed_role_paths.items():
            if relative in role_files:
                observed = digest_bytes(role_files[relative].encode("utf-8"))
            else:
                path = root / relative
                if not path.is_file():
                    raise ContractError("role_files", f"missing content for {relative}")
                observed = digest_bytes(path.read_bytes())
            if observed != role["digest"]:
                raise ContractError(f"role_files.{relative}", f"digest must equal {role['digest']}, observed {observed}")
        draft = {"repository": str(root), "workflow_id": workflow_id, "source": normalized, "role_files": role_files}
        path = self.ledger.draft_path(str(root), workflow_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(draft, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        with self.ledger.transaction() as connection:
            self.ledger.record_event(connection, "draft.saved", {"repository": str(root), "workflow_id": workflow_id, "draft_digest": digest_json(draft)})
        return {"draft_path": str(path), "draft_digest": digest_json(draft), "revision": normalized["revision"]}

    def apply_draft(self, repository: str, workflow_id: str) -> dict[str, Any]:
        root = _repository(repository)
        draft_path = self.ledger.draft_path(str(root), workflow_id)
        try:
            draft = json.loads(draft_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ContractError("draft", str(exc)) from exc
        if draft.get("repository") != str(root) or draft.get("workflow_id") != workflow_id:
            raise ContractError("draft", "repository or workflow identity mismatch")
        source = decode_source(draft.get("source"))
        role_files = draft.get("role_files")
        if not isinstance(role_files, dict) or not all(isinstance(key, str) and isinstance(value, str) for key, value in role_files.items()):
            raise ContractError("draft.role_files", "must be a string map")
        overrides = {path: content.encode("utf-8") for path, content in role_files.items()}
        lock = compile_source(root, source, file_overrides=overrides)

        existing_locks: list[dict[str, Any]] = []
        workflows_root = root / ".codex" / "workflows"
        if workflows_root.is_dir():
            for path in sorted(workflows_root.glob("*/workflow.lock.json")):
                if path.parent.name != workflow_id:
                    existing_locks.append(load_json(path))
        all_locks = existing_locks + [lock]
        workflow_dir = _workflow_dir(root, workflow_id)
        payloads: dict[Path, bytes] = {
            workflow_dir / "workflow.json": (json.dumps(source, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8"),
            workflow_dir / "workflow.lock.json": (json.dumps(lock, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8"),
            workflow_dir / "workflow.mmd": render_mermaid(lock).encode("utf-8"),
            root / "WORKFLOW.md": render_workflow(all_locks).encode("utf-8"),
        }
        for relative, content in role_files.items():
            path = (root / relative).resolve(strict=False)
            if root.resolve() not in path.parents:
                raise ContractError(f"role_files.{relative}", "target escapes repository")
            payloads[path] = content.encode("utf-8")
        agents = root / "AGENTS.md"
        existing_agents = agents.read_text(encoding="utf-8") if agents.exists() else "# Repository Instructions\n"
        payloads[agents] = _managed_agents_text(existing_agents).encode("utf-8")
        _atomic_replace(root, payloads)
        errors = verify_generated_views(root, workflow_id)
        if errors:
            raise ContractError("apply", "; ".join(errors))
        with self.ledger.transaction() as connection:
            self.ledger.record_event(
                connection,
                "workflow.applied",
                {"repository": str(root), "workflow_id": workflow_id, "revision": lock["revision"], "lock_digest": lock["lock_digest"]},
            )
        return {"workflow_id": workflow_id, "revision": lock["revision"], "lock_digest": lock["lock_digest"], "updated_files": [str(path.relative_to(root)) for path in sorted(payloads)]}

    def render(self, repository: str, workflow_id: str) -> dict[str, Any]:
        root = _repository(repository)
        lock = _lock(root, workflow_id)
        all_locks = []
        for path in sorted((root / ".codex" / "workflows").glob("*/workflow.lock.json")):
            all_locks.append(lock if path.parent.name == workflow_id else load_json(path))
        payloads = {
            _workflow_dir(root, workflow_id) / "workflow.mmd": render_mermaid(lock).encode("utf-8"),
            root / "WORKFLOW.md": render_workflow(all_locks).encode("utf-8"),
        }
        _atomic_replace(root, payloads)
        with self.ledger.transaction() as connection:
            self.ledger.record_event(connection, "workflow.rendered", {"repository": str(root), "workflow_id": workflow_id, "lock_digest": lock["lock_digest"]})
        return {"workflow_id": workflow_id, "rendered": [str(path.relative_to(root)) for path in sorted(payloads)]}

    def start_run(
        self,
        repository: str,
        workflow_id: str,
        requested_mode: str,
        native_context_control: bool,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        root = _repository(repository)
        if requested_mode not in {"guarded", "advisory"}:
            raise ContractError("requested_mode", "must be guarded or advisory")
        check = self.check_workflow(str(root), workflow_id)
        if not check["ok"]:
            raise ContractError("workflow", "; ".join(check["deterministic_errors"]))
        lock = _lock(root, workflow_id)
        latest = self.ledger.latest_session(str(root)) if not session_id else None
        resolved_session = session_id or (latest["session_id"] if latest else None)
        heartbeat_fresh = False
        if resolved_session:
            with self.ledger.connect() as connection:
                row = connection.execute(
                    "SELECT seen_at FROM heartbeats WHERE session_id = ? AND repository = ?",
                    (resolved_session, str(root)),
                ).fetchone()
            if row:
                seen = datetime.fromisoformat(row["seen_at"])
                heartbeat_fresh = datetime.now(timezone.utc) - seen <= timedelta(minutes=10)
        mode = "guarded" if requested_mode == "guarded" and native_context_control and heartbeat_fresh else "advisory"
        downgrade_reasons = []
        if requested_mode == "guarded" and not native_context_control:
            downgrade_reasons.append("native context-mode control was not confirmed")
        if requested_mode == "guarded" and not heartbeat_fresh:
            downgrade_reasons.append("no recent trusted hook heartbeat was found")
        starts = {node["id"]: 0 for node in lock["nodes"]}
        for edge in lock["edges"]:
            if edge["guard"]["kind"] != "retry_available":
                starts[edge["to"]] += 1
        start_nodes = sorted(node_id for node_id, incoming in starts.items() if incoming == 0)
        run_id = f"run_{secrets.token_hex(12)}"
        now = utc_now()
        with self.ledger.transaction() as connection:
            connection.execute(
                "INSERT INTO runs VALUES (?, ?, ?, ?, ?, 'running', ?, ?, ?, ?, NULL, ?, ?)",
                (run_id, str(root), workflow_id, requested_mode, mode, lock["lock_digest"], lock["revision"], resolved_session, start_nodes[0], now, now),
            )
            for node in lock["nodes"]:
                status = "ready" if node["id"] == start_nodes[0] else "pending"
                connection.execute(
                    "INSERT INTO node_states(run_id, node_id, status, attempts, result_json, evidence_json) VALUES (?, ?, ?, 0, NULL, '[]')",
                    (run_id, node["id"], status),
                )
            self.ledger.record_event(
                connection,
                "run.started",
                {"repository": str(root), "workflow_id": workflow_id, "requested_mode": requested_mode, "mode": mode, "downgrade_reasons": downgrade_reasons},
                run_id=run_id,
                event_key=f"run-started:{run_id}",
            )
        return {"run_id": run_id, "mode": mode, "status": "running", "current_node": start_nodes[0], "downgrade_reasons": downgrade_reasons}

    def _run_and_lock(self, connection: sqlite3.Connection, run_id: str) -> tuple[sqlite3.Row, dict[str, Any]]:
        run = connection.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        if not run:
            raise ContractError("run_id", "unknown run")
        if run["status"] != "running":
            raise ContractError("run_id", f"run is {run['status']}")
        lock = _lock(Path(run["repository"]), run["workflow_id"])
        if lock["lock_digest"] != run["lock_digest"] or lock["revision"] != run["revision"]:
            raise ContractError("run", "workflow lock changed after run start")
        return run, lock

    def prepare_dispatch(
        self,
        run_id: str,
        node_id: str,
        inputs: dict[str, Any],
        allowed_paths: list[str],
        depth: int,
        template_id: str | None = None,
        role_id: str | None = None,
        context_mode: str | None = None,
        required_outputs: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        with self.ledger.transaction() as connection:
            run, lock = self._run_and_lock(connection, run_id)
            if depth > lock["delegation_policy"]["max_depth"]:
                if run["mode"] == "guarded":
                    raise ContractError("depth", "exceeds delegation policy")
            node = next((item for item in lock["nodes"] if item["id"] == node_id), None)
            unverified: list[str] = []
            if node and node["type"] in {"task", "fanout"}:
                state = connection.execute("SELECT * FROM node_states WHERE run_id = ? AND node_id = ?", (run_id, node_id)).fetchone()
                if node_id != run["current_node"] or state["status"] not in {"ready", "failed"}:
                    raise ContractError("node_id", "node is not ready for dispatch")
                selected_template = template_id or node["template"]
                selected_role = role_id or node["role"]
                selected_context = context_mode or node["context_mode"]
                if selected_template != node["template"]:
                    unverified.append("template is not the route template")
                if selected_role != node["role"]:
                    unverified.append("role is not the route role")
                if selected_context != node["context_mode"]:
                    unverified.append("context mode is not the route context mode")
            else:
                if node is not None:
                    unverified.append("node is not a task or fanout node")
                else:
                    unverified.append("node is not declared")
                selected_template = template_id
                selected_role = role_id
                selected_context = context_mode
            if depth > lock["delegation_policy"]["max_depth"]:
                unverified.append("depth exceeds delegation policy")
            if run["mode"] == "guarded" and unverified:
                raise ContractError("dispatch", "; ".join(unverified))
            if not selected_template or not re.fullmatch(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*", selected_template):
                raise ContractError("template_id", "advisory dispatch requires a lower-case hyphenated template ID")
            if not selected_role or not re.fullmatch(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*", selected_role):
                raise ContractError("role_id", "advisory dispatch requires a lower-case hyphenated role ID")
            if selected_context not in {"parent", "isolated"}:
                raise ContractError("context_mode", "must be parent or isolated")
            template = next((item for item in lock["task_templates"] if item["id"] == selected_template), None)
            if template:
                normalized_inputs = validate_typed_values(inputs, template["inputs"], "inputs")
                output_specification = template["outputs"]
            else:
                if run["mode"] == "guarded":
                    raise ContractError("template_id", "template is not declared")
                if not isinstance(inputs, dict):
                    raise ContractError("inputs", "must be an object")
                if not isinstance(required_outputs, dict) or not all(
                    isinstance(name, str) and isinstance(value, str) and value in {"string", "integer", "number", "boolean", "object", "array", "null"}
                    for name, value in required_outputs.items()
                ):
                    raise ContractError("required_outputs", "unregistered advisory templates require a typed output map")
                normalized_inputs = inputs
                output_specification = required_outputs
            if not isinstance(allowed_paths, list) or not all(isinstance(path, str) for path in allowed_paths):
                raise ContractError("allowed_paths", "must be an array of repository-relative paths")
            for path in allowed_paths:
                candidate = Path(path)
                if candidate.is_absolute() or ".." in candidate.parts:
                    raise ContractError("allowed_paths", f"invalid path {path}")
            permit_id = f"permit_{secrets.token_hex(16)}"
            packet_base = {
                "schema_version": DISPATCH_SCHEMA,
                "workflow_id": run["workflow_id"],
                "run_id": run_id,
                "node_id": node_id,
                "template_id": selected_template,
                "role_id": selected_role,
                "depth": depth,
                "context_mode": selected_context,
                "inputs": normalized_inputs,
                "allowed_paths": allowed_paths,
                "required_outputs": output_specification,
                "revision": lock["revision"],
                "lock_digest": lock["lock_digest"],
                "permit_id": permit_id,
            }
            message = (
                "Execute only this Workflow Governor dispatch packet. Follow the installed role profile and applicable repository instructions. "
                "Do not use Git unless applicable repository instructions require it. Submit a workflow-result.v1 packet through workflow_submit_result before stopping.\n\n"
                + json.dumps(packet_base, ensure_ascii=False, sort_keys=True, indent=2)
            )
            task_token = re.sub(r"[^a-z0-9_]+", "_", f"{run['workflow_id']}_{node_id}_{permit_id[-8:]}").strip("_")[:64]
            spawn_arguments: dict[str, Any] = {
                "task_name": task_token,
                "message": message,
                "agent_type": selected_role,
                "fork_turns": "all" if selected_context == "parent" else "none",
            }
            packet = {
                **packet_base,
                "spawn_tool": "Agent",
                "spawn_arguments": spawn_arguments,
                "spawn_arguments_digest": digest_json(spawn_arguments),
            }
            decode_dispatch(packet)
            now = datetime.now(timezone.utc)
            expires = (now + timedelta(minutes=5)).isoformat(timespec="seconds")
            connection.execute(
                "INSERT INTO permits VALUES (?, ?, ?, ?, ?, ?, 'prepared', NULL, NULL, ?, ?, NULL, NULL)",
                (permit_id, run_id, node_id, selected_role, packet["spawn_arguments_digest"], json.dumps(packet, sort_keys=True), now.isoformat(timespec="seconds"), expires),
            )
            if node and node["type"] in {"task", "fanout"}:
                connection.execute(
                    "UPDATE node_states SET attempts = attempts + 1, status = 'dispatch-prepared' WHERE run_id = ? AND node_id = ?",
                    (run_id, node_id),
                )
            else:
                connection.execute(
                    "INSERT OR REPLACE INTO node_states(run_id, node_id, status, attempts, result_json, evidence_json) VALUES (?, ?, 'dispatch-prepared', 1, NULL, '[]')",
                    (run_id, node_id),
                )
            self.ledger.record_event(connection, "dispatch.prepared", {"permit_id": permit_id, "node_id": node_id, "arguments_digest": packet["spawn_arguments_digest"], "unverified": unverified}, run_id=run_id, event_key=f"permit-prepared:{permit_id}")
        return {"mode": run["mode"], "unverified": unverified, "dispatch_packet": packet, "spawn_arguments": spawn_arguments}

    def submit_result(self, result: dict[str, Any]) -> dict[str, Any]:
        packet = decode_result(result)
        with self.ledger.transaction() as connection:
            permit = connection.execute("SELECT * FROM permits WHERE permit_id = ?", (packet["permit_id"],)).fetchone()
            if not permit:
                raise ContractError("result.permit_id", "unknown permit")
            run, lock = self._run_and_lock(connection, packet["run_id"])
            if permit["run_id"] != packet["run_id"] or permit["node_id"] != packet["node_id"] or run["workflow_id"] != packet["workflow_id"]:
                raise ContractError("result", "packet identity does not match permit")
            if permit["status"] not in {"consumed", "bound"}:
                if run["mode"] == "guarded":
                    raise ContractError("result.permit_id", f"permit is {permit['status']}, not consumed")
            dispatch = decode_dispatch(json.loads(permit["dispatch_json"]))
            validate_typed_values(packet["outputs"], dispatch["required_outputs"], "result.outputs")
            root = Path(run["repository"])
            for relative, expected in packet["artifact_digests"].items():
                path = (root / relative).resolve(strict=False)
                if root.resolve() not in path.parents or not path.is_file():
                    raise ContractError(f"result.artifact_digests.{relative}", "artifact is missing or outside repository")
                observed = digest_bytes(path.read_bytes())
                if observed != expected:
                    raise ContractError(f"result.artifact_digests.{relative}", f"expected {expected}, observed {observed}")
            state_status = "completed" if packet["status"] == "completed" else packet["status"]
            connection.execute(
                "UPDATE permits SET status = 'result-recorded', result_at = ? WHERE permit_id = ?",
                (utc_now(), packet["permit_id"]),
            )
            connection.execute(
                "UPDATE node_states SET status = ?, result_json = ?, evidence_json = ? WHERE run_id = ? AND node_id = ?",
                (state_status, json.dumps(packet, sort_keys=True), json.dumps(packet["evidence"], sort_keys=True), packet["run_id"], packet["node_id"]),
            )
            self.ledger.record_event(connection, "result.submitted", packet, run_id=packet["run_id"], event_key=f"result:{packet['permit_id']}")
        return {"accepted": True, "run_id": packet["run_id"], "node_id": packet["node_id"], "status": packet["status"]}

    @staticmethod
    def _guard_matches(guard: dict[str, Any], inputs: dict[str, Any], outputs: dict[str, Any], evidence: list[str], attempts: int, retry_limit: int) -> bool:
        kind = guard["kind"]
        if kind == "always":
            return True
        if kind == "evidence":
            return guard["evidence"] in evidence
        if kind == "input_equals":
            return inputs.get(guard["field"]) == guard["equals"]
        if kind == "output_equals":
            return outputs.get(guard["field"]) == guard["equals"]
        if kind == "retry_available":
            return attempts <= retry_limit
        return False

    def advance(self, run_id: str, evidence: list[str], skip_nodes: list[dict[str, str]]) -> dict[str, Any]:
        if not isinstance(evidence, list) or not all(isinstance(item, str) and item for item in evidence):
            raise ContractError("evidence", "must contain non-empty strings")
        if not isinstance(skip_nodes, list):
            raise ContractError("skip_nodes", "must be an array")
        with self.ledger.transaction() as connection:
            run, lock = self._run_and_lock(connection, run_id)
            current_id = run["current_node"]
            node = next(item for item in lock["nodes"] if item["id"] == current_id)
            state = connection.execute("SELECT * FROM node_states WHERE run_id = ? AND node_id = ?", (run_id, current_id)).fetchone()
            if node["type"] in {"task", "fanout"} and state["status"] != "completed":
                raise ContractError("run", f"current node result is {state['status']}")
            if node["type"] not in {"task", "fanout", "end"}:
                if not evidence:
                    raise ContractError("evidence", f"{node['type']} node advancement requires evidence")
                connection.execute(
                    "UPDATE node_states SET status = 'completed', evidence_json = ? WHERE run_id = ? AND node_id = ?",
                    (json.dumps(evidence, sort_keys=True), run_id, current_id),
                )
            if node["type"] == "end":
                required = {item["id"] for item in lock["nodes"] if item["required"]}
                rows = connection.execute("SELECT node_id, status FROM node_states WHERE run_id = ?", (run_id,)).fetchall()
                statuses = {row["node_id"]: row["status"] for row in rows}
                incomplete = sorted(node_id for node_id in required if statuses.get(node_id) not in {"completed", "skipped"} and node_id != current_id)
                if incomplete:
                    raise ContractError("run", f"required nodes incomplete: {', '.join(incomplete)}")
                connection.execute("UPDATE node_states SET status = 'completed' WHERE run_id = ? AND node_id = ?", (run_id, current_id))
                connection.execute("UPDATE runs SET status = 'completed', updated_at = ? WHERE run_id = ?", (utc_now(), run_id))
                self.ledger.record_event(connection, "run.completed", {"workflow_id": run["workflow_id"]}, run_id=run_id, event_key=f"run-completed:{run_id}")
                return {"run_id": run_id, "status": "completed", "mode": run["mode"]}

            result = json.loads(state["result_json"]) if state["result_json"] else {"outputs": {}, "evidence": []}
            permit = connection.execute(
                "SELECT dispatch_json FROM permits WHERE run_id = ? AND node_id = ? ORDER BY created_at DESC LIMIT 1",
                (run_id, current_id),
            ).fetchone()
            inputs = json.loads(permit["dispatch_json"])["inputs"] if permit else {}
            attempts = state["attempts"]
            outgoing = sorted((edge for edge in lock["edges"] if edge["from"] == current_id), key=lambda item: (item["order"], item["id"]))
            matches = [edge for edge in outgoing if self._guard_matches(edge["guard"], inputs, result.get("outputs", {}), evidence + result.get("evidence", []), attempts, node["retry_limit"])]
            if not matches:
                raise ContractError("run", "no outgoing guard matched supplied evidence and result")
            selected = matches[0]
            if skip_nodes and selected["guard"]["kind"] == "always":
                raise ContractError("skip_nodes", "skips require a declared conditional guard")
            known_nodes = {item["id"]: item for item in lock["nodes"]}
            for index, skip in enumerate(skip_nodes):
                if not isinstance(skip, dict) or set(skip) != {"node_id", "evidence"}:
                    raise ContractError(f"skip_nodes[{index}]", "must contain node_id and evidence")
                skip_id = skip["node_id"]
                if skip_id not in known_nodes or not isinstance(skip["evidence"], str) or not skip["evidence"]:
                    raise ContractError(f"skip_nodes[{index}]", "must identify a declared node with evidence")
                connection.execute(
                    "UPDATE node_states SET status = 'skipped', evidence_json = ? WHERE run_id = ? AND node_id = ? AND status = 'pending'",
                    (json.dumps([skip["evidence"]]), run_id, skip_id),
                )
            connection.execute("UPDATE node_states SET status = 'ready' WHERE run_id = ? AND node_id = ?", (run_id, selected["to"]))
            connection.execute("UPDATE runs SET current_node = ?, updated_at = ? WHERE run_id = ?", (selected["to"], utc_now(), run_id))
            self.ledger.record_event(connection, "run.advanced", {"from": current_id, "to": selected["to"], "edge_id": selected["id"], "evidence": evidence, "skips": skip_nodes}, run_id=run_id)
            return {"run_id": run_id, "status": "running", "current_node": selected["to"], "selected_edge": selected["id"]}
