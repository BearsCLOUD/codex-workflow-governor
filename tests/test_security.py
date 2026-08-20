from __future__ import annotations

import json
import stat
import tempfile
import unittest
from pathlib import Path

from workflow_governor.compiler import write_generated_views
from workflow_governor.contracts import ContractError, LOCK_SCHEMA, digest_json, workflow_directory
from workflow_governor.hooks import HookRuntime
from workflow_governor.ledger import Ledger, utc_now
from workflow_governor.mcp_server import MAINTAINER_TOOLS, READER_TOOLS, McpServer
from workflow_governor.runtime import WorkflowRuntime


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def _lock() -> dict[str, object]:
    body: dict[str, object] = {
        "schema_version": LOCK_SCHEMA,
        "immutable": True,
        "workflow_id": "demo",
        "revision": 1,
        "title": "Demo",
        "description": "",
        "source_digest": "0" * 64,
        "instruction_rules": [],
        "role_refs": [],
        "task_templates": [],
        "nodes": [
            {
                "id": "finish",
                "type": "end",
                "required": True,
                "context_mode": "parent",
                "retry_limit": 0,
            }
        ],
        "edges": [],
        "routes": [],
        "delegation_policy": {"max_depth": 0, "allow_cycles": False, "max_retry": 0},
        "coverage": {"bound": 0, "not_applicable": 0, "total": 0},
    }
    return {**body, "lock_digest": digest_json(body)}


class PathSecurityTests(unittest.TestCase):
    def test_runtime_rejects_plain_directories_as_repositories(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            plain = base / "plain"
            plain.mkdir()
            runtime = WorkflowRuntime(Ledger(base / "data"))

            with self.assertRaisesRegex(ContractError, "not a Git worktree"):
                runtime.list_workflows(str(plain))

    def test_workflow_ids_cannot_escape_repository_or_private_data(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            repository = base / "repository"
            repository.mkdir()
            (repository / ".git").mkdir()
            ledger = Ledger(base / "data")
            runtime = WorkflowRuntime(ledger)

            with self.assertRaises(ContractError):
                workflow_directory(repository, "../outside")
            with self.assertRaises(ContractError):
                ledger.draft_path(str(repository), "../outside")
            with self.assertRaises(ContractError):
                runtime.get_workflow(str(repository), "../outside")

    def test_workflow_directory_rejects_symlink_escape(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            repository = base / "repository"
            workflows = repository / ".codex" / "workflows"
            workflows.mkdir(parents=True)
            outside = base / "outside"
            outside.mkdir()
            (workflows / "demo").symlink_to(outside, target_is_directory=True)

            with self.assertRaises(ContractError):
                workflow_directory(repository, "demo")

    def test_generated_writer_rejects_leaf_symlink_escape(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            repository = base / "repository"
            workflow = repository / ".codex" / "workflows" / "demo"
            workflow.mkdir(parents=True)
            (repository / ".git").mkdir()
            outside = base / "sentinel.json"
            outside.write_text("do not overwrite", encoding="utf-8")
            (workflow / "workflow.lock.json").symlink_to(outside)

            with self.assertRaisesRegex(ContractError, "must not be a symlink"):
                write_generated_views(repository, "demo", _lock())
            self.assertEqual(outside.read_text(encoding="utf-8"), "do not overwrite")


class PrivateDataTests(unittest.TestCase):
    def test_ledger_and_drafts_use_private_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            data_dir = Path(temporary) / "data"
            data_dir.mkdir(mode=0o777)
            data_dir.chmod(0o777)
            ledger = Ledger(data_dir)

            self.assertEqual(_mode(data_dir), 0o700)
            self.assertEqual(_mode(ledger.path), 0o600)

            draft = ledger.draft_path(str(Path(temporary) / "repository"), "demo")
            ledger.write_private_json(draft, {"secret": "value"})
            self.assertEqual(_mode(draft), 0o600)
            current = draft.parent
            while current != data_dir:
                self.assertEqual(_mode(current), 0o700)
                current = current.parent

    def test_ledger_refuses_database_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            data_dir = Path(temporary) / "data"
            data_dir.mkdir()
            target = Path(temporary) / "target"
            target.write_text("do not modify", encoding="utf-8")
            (data_dir / "workflow-governor.sqlite3").symlink_to(target)

            with self.assertRaises(OSError):
                Ledger(data_dir)
            self.assertEqual(target.read_text(encoding="utf-8"), "do not modify")


class GuardedStopTests(unittest.TestCase):
    def test_stop_verifies_lock_digest_and_run_authority(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            repository = base / "repository"
            lock_path = repository / ".codex" / "workflows" / "demo" / "workflow.lock.json"
            lock_path.parent.mkdir(parents=True)
            lock = _lock()
            lock_path.write_text(json.dumps(lock), encoding="utf-8")

            ledger = Ledger(base / "data")
            now = utc_now()
            with ledger.transaction() as connection:
                connection.execute(
                    """
                    INSERT INTO runs(
                        run_id, repository, workflow_id, requested_mode, mode, status,
                        lock_digest, revision, session_id, current_node, violation_reason,
                        created_at, updated_at
                    ) VALUES (?, ?, 'demo', 'guarded', 'guarded', 'running', ?, 1, NULL, 'finish', NULL, ?, ?)
                    """,
                    ("run_demo", str(repository.resolve()), lock["lock_digest"], now, now),
                )
                connection.execute(
                    "INSERT INTO node_states(run_id, node_id, status, attempts, result_json, evidence_json) VALUES ('run_demo', 'finish', 'completed', 0, NULL, '[]')"
                )

            hooks = HookRuntime(ledger)
            payload = {"cwd": str(repository), "session_id": "session", "hook_event_name": "Stop"}
            self.assertEqual(hooks.stop(payload), {})

            altered = dict(lock)
            altered["nodes"] = []
            lock_path.write_text(json.dumps(altered), encoding="utf-8")
            self.assertEqual(hooks.stop(payload)["decision"], "block")

            changed_body = dict(lock)
            changed_body.pop("lock_digest")
            changed_body["revision"] = 2
            changed = {**changed_body, "lock_digest": digest_json(changed_body)}
            lock_path.write_text(json.dumps(changed), encoding="utf-8")
            result = hooks.stop(payload)
            self.assertEqual(result["decision"], "block")
            self.assertIn("run authority", result["reason"])


class McpAnnotationTests(unittest.TestCase):
    def test_mutating_tools_are_not_advertised_as_non_destructive(self) -> None:
        server = object.__new__(McpServer)
        server.tools = MAINTAINER_TOOLS
        tools = {item["name"]: item for item in server.tool_list()}

        self.assertTrue(tools["workflow_apply"]["annotations"]["destructiveHint"])
        self.assertTrue(tools["workflow_advance"]["annotations"]["destructiveHint"])
        self.assertFalse(tools["workflow_start_run"]["annotations"]["destructiveHint"])
        self.assertFalse(tools["workflow_advance"]["annotations"]["idempotentHint"])
        self.assertFalse(tools["workflow_apply"]["annotations"]["openWorldHint"])

    def test_malformed_tool_arguments_return_error_without_crashing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            server = object.__new__(McpServer)
            server.surface = "reader"
            server.runtime = WorkflowRuntime(Ledger(Path(temporary) / "data"))
            server.tools = READER_TOOLS
            missing = server.handle(
                {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "workflow_list", "arguments": {}}}
            )
            malformed = server.handle(
                {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": "not-an-object"}
            )

            self.assertTrue(missing["result"]["isError"])
            self.assertTrue(malformed["result"]["isError"])


if __name__ == "__main__":
    unittest.main()
