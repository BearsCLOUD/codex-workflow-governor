"""MCP wire-contract boundary.

The executable facade re-exports these values from the single implementation
module so tool schemas, limits, envelopes, and request-id validation cannot
drift between protocol and dispatch paths.
"""

from . import runtime as _runtime

RESULT_SCHEMA = _runtime.RESULT_SCHEMA
MAX_FRAME_BYTES = _runtime.MAX_FRAME_BYTES
MAX_RESPONSE_BYTES = _runtime.MAX_RESPONSE_BYTES
MAX_STDOUT_BYTES = _runtime.MAX_STDOUT_BYTES
MAX_STDERR_BYTES = _runtime.MAX_STDERR_BYTES
MAX_DEPTH = _runtime.MAX_DEPTH
MAX_KEYS = _runtime.MAX_KEYS
MAX_ARRAY_ITEMS = _runtime.MAX_ARRAY_ITEMS
MAX_STRING_BYTES = _runtime.MAX_STRING_BYTES
TOOLS = _runtime.TOOLS
TOOL_NAMES = _runtime.TOOL_NAMES
McpToolError = _runtime.McpToolError
_measure = _runtime._measure
_valid_jsonrpc_id = _runtime._valid_jsonrpc_id
_bounded_response = _runtime._bounded_response
_sanitize = _runtime._sanitize
_success = _runtime._success
_failure = _runtime._failure

__all__ = [
    "RESULT_SCHEMA", "MAX_FRAME_BYTES", "MAX_RESPONSE_BYTES", "MAX_STDOUT_BYTES",
    "MAX_STDERR_BYTES", "MAX_DEPTH", "MAX_KEYS", "MAX_ARRAY_ITEMS",
    "MAX_STRING_BYTES", "TOOLS", "TOOL_NAMES", "McpToolError", "_measure",
    "_valid_jsonrpc_id", "_bounded_response", "_sanitize", "_success", "_failure",
]
