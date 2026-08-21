"""Private implementation boundary for the self-contained workflow skill.

The public surface is the skill CLI.  The modules in this package are kept
behind that launcher so the runtime can be tested and evolved without a
second, historical backend package.
"""

from . import engine
from . import api

__version__ = "0.10.0"
from .engine import (
    ContractError,
    EXEC_WORKFLOW_SCHEMA,
    EXEC_WORKFLOW_SCHEMA_V1,
    EXEC_WORKFLOW_SCHEMA_V2,
    EXEC_WORKFLOW_SCHEMAS,
    build_parser,
    digest_json,
    execute_run,
    load_workflow,
    main,
    resolve_workflow,
    validate_typed_values,
)

__all__ = [
    "ContractError",
    "EXEC_WORKFLOW_SCHEMA",
    "EXEC_WORKFLOW_SCHEMA_V1",
    "EXEC_WORKFLOW_SCHEMA_V2",
    "EXEC_WORKFLOW_SCHEMAS",
    "build_parser",
    "digest_json",
    "engine",
    "api",
    "execute_run",
    "load_workflow",
    "main",
    "resolve_workflow",
    "validate_typed_values",
    "__version__",
]
