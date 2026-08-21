from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "tests" / "golden"
CLI = ROOT / "scripts" / "codex_workflows.py"


class ContractSnapshotTests(unittest.TestCase):
    def test_root_and_skill_help_match_snapshot(self) -> None:
        expected = (GOLDEN / "cli-help.txt").read_text(encoding="utf-8")
        for launcher in (
            ROOT / "scripts" / "codex_workflows.py",
            ROOT / "skills" / "codex-workflows" / "scripts" / "codex_workflows.py",
        ):
            completed = subprocess.run(
                [sys.executable, str(launcher), "--help"],
                cwd=ROOT,
                check=True,
                text=True,
                stdout=subprocess.PIPE,
            )
            self.assertEqual(completed.stdout, expected)

    def test_tools_list_is_exactly_four_tools(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(ROOT / "mcp" / "server.py")],
            cwd=ROOT,
            input='{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}\n',
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        )
        expected = json.loads((GOLDEN / "mcp-tools-list.json").read_text(encoding="utf-8"))
        actual = json.loads(completed.stdout)
        self.assertEqual(actual, expected)

    def test_workflow_show_and_plan_remain_deterministic(self) -> None:
        show = subprocess.run(
            [
                sys.executable,
                str(CLI),
                "--project-root",
                str(ROOT),
                "workflow",
                "show",
                "builtin:fanout-synthesize",
                "--schemas",
            ],
            cwd=ROOT,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        )
        plan = subprocess.run(
            [
                sys.executable,
                str(CLI),
                "--project-root",
                str(ROOT),
                "plan",
                "builtin:fanout-synthesize",
                "--input",
                'request="summarize"',
                "--input",
                "items=[1,2]",
            ],
            cwd=ROOT,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        )
        self.assertEqual(
            json.loads(show.stdout),
            json.loads((GOLDEN / "workflow-show-fanout-synthesize.json").read_text(encoding="utf-8")),
        )
        self.assertEqual(
            json.loads(plan.stdout),
            json.loads((GOLDEN / "workflow-plan-fanout-synthesize.json").read_text(encoding="utf-8")),
        )

    def test_status_result_snapshot_has_shared_terminal_shape(self) -> None:
        snapshot = json.loads((GOLDEN / "status-result.json").read_text(encoding="utf-8"))
        self.assertEqual(set(snapshot), {"status", "result"})
        self.assertEqual(snapshot["status"]["run_id"], snapshot["result"]["run_id"])
        self.assertEqual(snapshot["status"]["status"], "completed")
        self.assertEqual(snapshot["result"]["status"], "completed")


if __name__ == "__main__":
    unittest.main()
