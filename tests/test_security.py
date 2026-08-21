from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sys

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_SCRIPTS = PLUGIN_ROOT / "skills" / "codex-workflows" / "scripts"
if str(PLUGIN_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(PLUGIN_SCRIPTS))

from mcp.server import McpToolError, TOOLS, WorkflowMcpServer, _sanitize  # noqa: E402
from workflow_runtime import engine  # noqa: E402
from workflow_runtime.contracts import ContractError, digest_json, resolve_workflow  # noqa: E402


class PathSecurityTests(unittest.TestCase):
    def test_workflow_reference_rejects_parent_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            (project / ".git").mkdir()
            with self.assertRaises(ContractError):
                resolve_workflow("../outside", project)

    def test_workflow_reference_rejects_symlink_escape(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            project = base / "project"
            project.mkdir()
            (project / ".git").mkdir()
            outside = base / "outside"
            outside.mkdir()
            workflows = project / ".codex" / "exec-workflows"
            workflows.mkdir(parents=True)
            (workflows / "escape").symlink_to(outside, target_is_directory=True)
            with self.assertRaises(ContractError):
                resolve_workflow("project:escape", project, qualified_only=True)

    def test_run_storage_stays_private_and_contained(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            data = Path(temporary) / "data"
            project = Path(temporary) / "project"
            project.mkdir()
            (project / ".git").mkdir()
            with patch.dict(os.environ, {"PLUGIN_DATA": str(data)}):
                root = engine._runs_root(project)
                root.mkdir(parents=True)
                ledger = engine._mutation_database_path(project)
                engine._ensure_private_mutation_database(ledger)
                self.assertEqual(stat.S_IMODE(ledger.stat().st_mode), 0o600)
                self.assertEqual(ledger.resolve().parent, root.resolve())

    def test_mutation_ledger_rejects_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "real.sqlite3"
            target.write_bytes(b"")
            alias = Path(temporary) / "mutation-ledger.sqlite3"
            alias.symlink_to(target)
            with self.assertRaises(ContractError):
                engine._verify_private_mutation_database(alias)


class McpAnnotationTests(unittest.TestCase):
    def test_exact_four_tools_and_mutation_annotations(self) -> None:
        self.assertEqual(
            [tool["name"] for tool in TOOLS],
            ["workflow_plan", "workflow_run", "workflow_status", "workflow_control"],
        )
        by_name = {tool["name"]: tool for tool in TOOLS}
        self.assertTrue(by_name["workflow_plan"]["annotations"]["readOnlyHint"])
        self.assertTrue(by_name["workflow_status"]["annotations"]["readOnlyHint"])
        self.assertTrue(by_name["workflow_run"]["annotations"]["destructiveHint"])
        self.assertTrue(by_name["workflow_control"]["annotations"]["destructiveHint"])

    def test_envelope_and_redaction_are_bounded(self) -> None:
        self.assertEqual(_sanitize("token=sk-secret /home/user/file"), "token=[REDACTED]")
        self.assertEqual(_sanitize("failed at /home/user/file"), "failed at [PATH]")
        response, is_error = WorkflowMcpServer().call_tool("unknown", {})
        self.assertTrue(is_error)
        self.assertFalse(response["ok"])
        self.assertEqual(response["schema_version"], "codex-workflow-mcp-result.v1")

    def test_malformed_arguments_fail_closed(self) -> None:
        server = WorkflowMcpServer()
        with self.assertRaises(McpToolError):
            server._invoke("workflow_plan", {"project_root": "/tmp", "workflow": "bad"})


class ContractDigestTests(unittest.TestCase):
    def test_digest_is_canonical(self) -> None:
        self.assertEqual(digest_json({"b": 2, "a": 1}), digest_json({"a": 1, "b": 2}))


if __name__ == "__main__":
    unittest.main()
