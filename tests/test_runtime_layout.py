from __future__ import annotations

import importlib
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "codex-workflows" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


class RuntimeLayoutTests(unittest.TestCase):
    def test_public_launchers_are_thin_and_use_one_api_boundary(self) -> None:
        root = (ROOT / "scripts" / "codex_workflows.py").read_text(encoding="utf-8")
        skill = (SCRIPTS / "codex_workflows.py").read_text(encoding="utf-8")
        self.assertLessEqual(len(root.splitlines()), 30)
        self.assertLessEqual(len(skill.splitlines()), 20)
        self.assertIn("workflow_runtime.api", root)
        self.assertIn("workflow_runtime.api", skill)

    def test_runtime_subsystems_share_contract_and_reconciler_modules(self) -> None:
        contracts = importlib.import_module("workflow_runtime.contracts")
        resolution = importlib.import_module("workflow_runtime.resolution")
        reconciler = importlib.import_module("workflow_runtime.reconciler")
        self.assertIs(contracts.ContractError, resolution.ContractError)
        self.assertTrue(callable(contracts.load_workflow))
        self.assertTrue(callable(reconciler._terminal_output_reconciliation))

    def test_historical_backend_and_optional_wrappers_are_absent(self) -> None:
        self.assertFalse((ROOT / ("workflow" + "_governor")).exists())
        self.assertFalse((ROOT / "scripts" / ("workflow_" + "compile.py")).exists())
        self.assertFalse((ROOT / "scripts" / ("workflow_" + "mcp.py")).exists())
        source = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (
                ROOT / "scripts" / "codex_workflows.py",
                SCRIPTS / "codex_workflows.py",
                ROOT / "mcp" / "server.py",
            )
        )
        self.assertNotIn("_" + "exec_runner_impl", source)


if __name__ == "__main__":
    unittest.main()
