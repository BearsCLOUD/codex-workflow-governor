"""Workflow contracts, schemas, interpolation, and digest helpers.

The implementation remains centralized in :mod:`workflow_runtime.engine` so
all callers use the same validators and byte-compatible artifact rules.  This
module is the stable import boundary for contract consumers.
"""

from . import engine as _engine

ContractError = _engine.ContractError
EXEC_WORKFLOW_SCHEMA = _engine.EXEC_WORKFLOW_SCHEMA
EXEC_WORKFLOW_SCHEMA_V1 = _engine.EXEC_WORKFLOW_SCHEMA_V1
EXEC_WORKFLOW_SCHEMA_V2 = _engine.EXEC_WORKFLOW_SCHEMA_V2
EXEC_WORKFLOW_SCHEMAS = _engine.EXEC_WORKFLOW_SCHEMAS
AGENT_SPEC_SCHEMA = _engine.AGENT_SPEC_SCHEMA
AGENT_FIELDS = _engine.AGENT_FIELDS
AGENT_PIN_FIELDS = _engine.AGENT_PIN_FIELDS
VALUE_TYPES = _engine.VALUE_TYPES
SANDBOXES = _engine.SANDBOXES
MAX_TASKS = _engine.MAX_TASKS
MAX_FANOUT_ITEMS = _engine.MAX_FANOUT_ITEMS
DEFAULT_MAX_CALLS = _engine.DEFAULT_MAX_CALLS
MAX_CALLS = _engine.MAX_CALLS
MIN_LOOP_INTERVAL_SECONDS = _engine.MIN_LOOP_INTERVAL_SECONDS
MAX_LOOP_INTERVAL_SECONDS = _engine.MAX_LOOP_INTERVAL_SECONDS
MAX_LOOP_CYCLE_SECONDS = _engine.MAX_LOOP_CYCLE_SECONDS
MAX_LOOP_FAILURES = _engine.MAX_LOOP_FAILURES
MAX_LOOP_RETENTION_CYCLES = _engine.MAX_LOOP_RETENTION_CYCLES
LOOP_PERMISSION_NAMES = _engine.LOOP_PERMISSION_NAMES
QUALIFIED_WORKFLOW = _engine.QUALIFIED_WORKFLOW
RUN_IDENTIFIER = _engine.RUN_IDENTIFIER
PLACEHOLDER = _engine.PLACEHOLDER
MUTATION_REQUEST_FIELDS = _engine.MUTATION_REQUEST_FIELDS
MUTATION_REQUEST_UPDATABLE = _engine.MUTATION_REQUEST_UPDATABLE
TERMINAL_RUN_STATUSES = _engine.TERMINAL_RUN_STATUSES
TERMINAL_TASK_STATUSES = _engine.TERMINAL_TASK_STATUSES
digest_json = _engine.digest_json
validate_typed_values = _engine.validate_typed_values
load_workflow = _engine.load_workflow
resolve_workflow = _engine.resolve_workflow
_strict_schema = _engine._strict_schema
_validate_instance = _engine._validate_instance
_execution_plan = _engine._execution_plan
_parse_input_values = _engine._parse_input_values
_workflow_digest = _engine._workflow_digest

__all__ = [
    "AGENT_FIELDS", "AGENT_PIN_FIELDS", "AGENT_SPEC_SCHEMA", "ContractError",
    "DEFAULT_MAX_CALLS", "EXEC_WORKFLOW_SCHEMA", "EXEC_WORKFLOW_SCHEMA_V1",
    "EXEC_WORKFLOW_SCHEMA_V2", "EXEC_WORKFLOW_SCHEMAS", "MAX_CALLS",
    "MAX_FANOUT_ITEMS", "MAX_LOOP_CYCLE_SECONDS", "MAX_LOOP_FAILURES",
    "MAX_LOOP_INTERVAL_SECONDS", "MAX_LOOP_RETENTION_CYCLES", "MAX_TASKS",
    "MUTATION_REQUEST_FIELDS", "MUTATION_REQUEST_UPDATABLE", "PLACEHOLDER",
    "QUALIFIED_WORKFLOW", "RUN_IDENTIFIER", "SANDBOXES", "TERMINAL_RUN_STATUSES",
    "TERMINAL_TASK_STATUSES", "VALUE_TYPES", "LOOP_PERMISSION_NAMES",
    "digest_json", "load_workflow", "resolve_workflow", "validate_typed_values",
    "_strict_schema", "_validate_instance", "_execution_plan", "_parse_input_values",
    "_workflow_digest",
]
