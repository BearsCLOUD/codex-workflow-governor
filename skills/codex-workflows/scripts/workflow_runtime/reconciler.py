"""Shared terminal-output and event reconciliation boundary."""

from . import engine as _engine

_terminal_output_reconciliation = _engine._terminal_output_reconciliation
_event_output_state = _engine._event_output_state
_output_state = _engine._output_state
_refresh_event_metadata = _engine._refresh_event_metadata
_failure_code = _engine._failure_code
_terminal_grace_seconds = _engine._terminal_grace_seconds

__all__ = [
    "_terminal_output_reconciliation", "_event_output_state", "_output_state",
    "_refresh_event_metadata", "_failure_code", "_terminal_grace_seconds",
]
