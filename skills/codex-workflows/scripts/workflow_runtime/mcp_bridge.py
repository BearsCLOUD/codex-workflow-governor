"""Hidden CLI bridge used by the four-tool local MCP adapter."""

from . import engine as _engine

_mcp_run_request = _engine._mcp_run_request
_mcp_control_request = _engine._mcp_control_request
_mcp_lookup_request = _engine._mcp_lookup_request
_mcp_status_summary = _engine._mcp_status_summary
_mcp_result_metadata = _engine._mcp_result_metadata
_mcp_normalized_run_request = _engine._mcp_normalized_run_request
_mutation_lookup = _engine._mutation_lookup
_reserve_mutation_request = _engine._reserve_mutation_request
_update_mutation_request = _engine._update_mutation_request

__all__ = [
    "_mcp_run_request", "_mcp_control_request", "_mcp_lookup_request",
    "_mcp_status_summary", "_mcp_result_metadata", "_mcp_normalized_run_request",
    "_mutation_lookup", "_reserve_mutation_request", "_update_mutation_request",
]
