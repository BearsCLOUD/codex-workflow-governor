"""Built-in and project template normalization boundary."""

from pathlib import Path

from . import engine as _engine

_builtin_workflows_root = _engine._builtin_workflows_root
_scope_root = _engine._scope_root
_template_workflow = _engine._template_workflow
_template_schema = _engine._template_schema
_copy_workflow = _engine._copy_workflow
load_workflow = _engine.load_workflow
resolve_workflow = _engine.resolve_workflow


def builtin_workflow_path(name: str) -> Path:
    """Return one contained built-in workflow JSON path after identifier checks."""
    scope, path = resolve_workflow(f"builtin:{name}", Path.cwd())
    if scope != "builtin":
        raise _engine.ContractError("workflow", "expected a builtin workflow")
    return path


__all__ = [
    "builtin_workflow_path", "load_workflow", "resolve_workflow",
    "_builtin_workflows_root", "_scope_root", "_template_workflow",
    "_template_schema", "_copy_workflow",
]
