"""Persistent-loop supervisor, checkpoint, and recovery boundary."""

from . import engine as _engine

_execute_loop_run = _engine._execute_loop_run
_prepare_loop_cycle = _engine._prepare_loop_cycle
_cycle_outputs = _engine._cycle_outputs
_loop_delay = _engine._loop_delay
_loop_control = _engine._loop_control
_wait_for_loop_wake = _engine._wait_for_loop_wake
_claim_loop_instance = _engine._claim_loop_instance
_loop_instance_key = _engine._loop_instance_key
_idempotency_lookup = _engine._idempotency_lookup
_idempotency_commit = _engine._idempotency_commit
_read_loop_events = _engine._read_loop_events
_read_loop_checkpoint = _engine._read_loop_checkpoint
_rebuild_loop_projection = _engine._rebuild_loop_projection

__all__ = [
    "_execute_loop_run", "_prepare_loop_cycle", "_cycle_outputs", "_loop_delay",
    "_loop_control", "_wait_for_loop_wake", "_claim_loop_instance",
    "_loop_instance_key", "_idempotency_lookup", "_idempotency_commit",
    "_read_loop_events", "_read_loop_checkpoint", "_rebuild_loop_projection",
]
