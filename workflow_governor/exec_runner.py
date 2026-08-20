"""Compatibility facade for the self-contained Codex Workflows skill CLI."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


_SCRIPT = Path(__file__).resolve().parent.parent / "skills" / "codex-workflows" / "scripts" / "codex_workflows.py"
_SPEC = importlib.util.spec_from_file_location("workflow_governor._exec_runner_impl", _SCRIPT)
if _SPEC is None or _SPEC.loader is None:
    raise ImportError(f"cannot load Codex Workflows executable from {_SCRIPT}")
_MODULE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _MODULE
_SPEC.loader.exec_module(_MODULE)

EXEC_WORKFLOW_SCHEMA = _MODULE.EXEC_WORKFLOW_SCHEMA
execute_run = _MODULE.execute_run
load_workflow = _MODULE.load_workflow
main = _MODULE.main

__all__ = ["EXEC_WORKFLOW_SCHEMA", "execute_run", "load_workflow", "main"]
