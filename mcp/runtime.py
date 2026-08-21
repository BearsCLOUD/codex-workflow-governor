#!/usr/bin/env python3
"""Bounded local stdio MCP adapter for the public codex-exec workflow CLI."""

from __future__ import annotations

import argparse
import contextlib
import json
import math
import os
import re
import selectors
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Mapping

if __package__:
    from . import redaction, root_auth
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from mcp import redaction, root_auth


RESULT_SCHEMA = "codex-workflow-mcp-result.v1"
MAX_FRAME_BYTES = 1024 * 1024
MAX_STDOUT_BYTES = 2 * 1024 * 1024
MAX_STDERR_BYTES = 64 * 1024
MAX_RESPONSE_BYTES = 1024 * 1024
MAX_JSONRPC_INTEGER_ID = (1 << 53) - 1
MAX_ERROR_BYTES = 2048
MAX_DEPTH = 32
MAX_KEYS = 4096
MAX_ARRAY_ITEMS = 10_000
MAX_STRING_BYTES = 256 * 1024
QUALIFIED = re.compile(r"^(project|user|builtin):[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
RUN_ID = re.compile(r"^exec_[0-9]{8}T[0-9]{6}Z_[0-9a-f]{8}$")
UUID4 = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
SUPPORTED_PROTOCOL_VERSIONS = {"2024-11-05", "2025-06-18"}
DEFAULT_PROTOCOL_VERSION = "2025-06-18"
ABSOLUTE_PATH = re.compile(r"(?<![A-Za-z0-9_.-])/(?:[^\s:]+/)*[^\s:,;]*")


class McpToolError(ValueError):
    def __init__(self, code: str, message: str, *, retryable: bool = False, data: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.data = data


def _object_schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


STRING = {"type": "string"}
OBJECT = {"type": "object"}
INTEGER = {"type": "integer"}
BOOLEAN = {"type": "boolean"}

TOOLS = [
    {
        "name": "workflow_plan",
        "description": "Validate one qualified local workflow and return its bounded deterministic plan.",
        "inputSchema": _object_schema(
            {
                "project_root": STRING,
                "workflow": STRING,
                "inputs": OBJECT,
                "max_parallel": INTEGER,
                "max_calls": INTEGER,
            },
            ["project_root", "workflow"],
        ),
        "annotations": {
            "title": "Plan local workflow",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    },
    {
        "name": "workflow_run",
        "description": "Start one qualified workflow detached under a globally unique mutation request UUIDv4.",
        "inputSchema": _object_schema(
            {
                "project_root": STRING,
                "workflow": STRING,
                "request_id": STRING,
                "inputs": OBJECT,
                "max_parallel": INTEGER,
                "max_calls": INTEGER,
                "allow_workspace_write": BOOLEAN,
                "allow_danger_full_access": BOOLEAN,
            },
            ["project_root", "workflow", "request_id"],
        ),
        "annotations": {
            "title": "Run local workflow",
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    },
    {
        "name": "workflow_status",
        "description": "Read one bounded persisted run summary by run ID or mutation request ID.",
        "inputSchema": _object_schema(
            {"project_root": STRING, "run_id": STRING, "request_id": STRING},
            ["project_root"],
        ),
        "annotations": {
            "title": "Read workflow status",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    },
    {
        "name": "workflow_control",
        "description": "Request pause, resume, or cancel under a globally unique mutation request UUIDv4.",
        "inputSchema": _object_schema(
            {
                "project_root": STRING,
                "run_id": STRING,
                "request_id": STRING,
                "action": {"type": "string", "enum": ["pause", "resume", "cancel"]},
            },
            ["project_root", "run_id", "request_id", "action"],
        ),
        "annotations": {
            "title": "Control local workflow",
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    },
]

TOOL_NAMES = {tool["name"] for tool in TOOLS}


def _sanitize(value: str) -> str:
    value = redaction.redact_text(value)
    value = ABSOLUTE_PATH.sub("[PATH]", value)
    encoded = value.encode("utf-8", errors="replace")[:MAX_ERROR_BYTES]
    return encoded.decode("utf-8", errors="replace").strip() or "local workflow operation failed"


def _measure(value: Any, *, depth: int = 0, counters: dict[str, int] | None = None) -> None:
    if counters is None:
        counters = {"keys": 0, "items": 0}
    if depth > MAX_DEPTH:
        raise McpToolError("invalid_arguments", "JSON nesting exceeds the supported limit")
    if isinstance(value, str):
        if len(value.encode("utf-8")) > MAX_STRING_BYTES:
            raise McpToolError("invalid_arguments", "a string exceeds the supported size limit")
    elif isinstance(value, dict):
        counters["keys"] += len(value)
        if counters["keys"] > MAX_KEYS:
            raise McpToolError("invalid_arguments", "object key count exceeds the supported limit")
        for key, item in value.items():
            if not isinstance(key, str):
                raise McpToolError("invalid_arguments", "object keys must be strings")
            _measure(key, depth=depth + 1, counters=counters)
            _measure(item, depth=depth + 1, counters=counters)
    elif isinstance(value, list):
        counters["items"] += len(value)
        if counters["items"] > MAX_ARRAY_ITEMS:
            raise McpToolError("invalid_arguments", "array item count exceeds the supported limit")
        for item in value:
            _measure(item, depth=depth + 1, counters=counters)
    elif isinstance(value, float) and not math.isfinite(value):
        raise McpToolError("invalid_arguments", "numbers must be finite JSON values")
    elif value is not None and not isinstance(value, (bool, int, float)):
        raise McpToolError("invalid_arguments", "arguments must contain JSON values only")


def _valid_jsonrpc_id(value: Any) -> bool:
    if value is None or isinstance(value, str):
        return True
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return abs(value) <= MAX_JSONRPC_INTEGER_ID
    return (
        isinstance(value, float)
        and math.isfinite(value)
        and abs(value) <= MAX_JSONRPC_INTEGER_ID
    )


def _bounded_response(response: dict[str, Any]) -> dict[str, Any]:
    encoded = json.dumps(
        response,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    if len(encoded) <= MAX_RESPONSE_BYTES:
        return response
    return {
        "jsonrpc": "2.0",
        "id": None,
        "error": {"code": -32603, "message": "Response exceeds 1 MiB"},
    }


def _require_keys(arguments: Mapping[str, Any], required: set[str], optional: set[str]) -> None:
    missing = sorted(required - set(arguments))
    unknown = sorted(set(arguments) - required - optional)
    if missing:
        raise McpToolError("invalid_arguments", f"missing fields: {', '.join(missing)}")
    if unknown:
        raise McpToolError("invalid_arguments", f"unknown fields: {', '.join(unknown)}")


def _string(arguments: Mapping[str, Any], name: str) -> str:
    value = arguments.get(name)
    if not isinstance(value, str) or not value:
        raise McpToolError("invalid_arguments", f"{name} must be a non-empty string")
    return value


def _optional_integer(arguments: Mapping[str, Any], name: str, minimum: int, maximum: int) -> int | None:
    value = arguments.get(name)
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
        raise McpToolError("invalid_arguments", f"{name} must be from {minimum} to {maximum}")
    return value


def _validate_common(arguments: Mapping[str, Any]) -> tuple[str, str | None, dict[str, Any]]:
    project = _string(arguments, "project_root")
    if not Path(project).is_absolute():
        raise McpToolError("invalid_arguments", "project_root must be an absolute canonical path")
    workflow = arguments.get("workflow")
    if workflow is not None and (not isinstance(workflow, str) or QUALIFIED.fullmatch(workflow) is None):
        raise McpToolError("invalid_arguments", "workflow must be a qualified project:, user:, or builtin: reference")
    inputs = arguments.get("inputs", {})
    if not isinstance(inputs, dict):
        raise McpToolError("invalid_arguments", "inputs must be a JSON object")
    encoded = json.dumps(inputs, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(encoded) > MAX_FRAME_BYTES:
        raise McpToolError("invalid_arguments", "inputs exceed the 1 MiB limit")
    return project, workflow, inputs


def _child_environment() -> dict[str, str]:
    allowed = {
        "HOME", "CODEX_HOME", "PATH", "LANG", "LC_ALL", "TERM", "TMPDIR",
        "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
        "http_proxy", "https_proxy", "all_proxy", "no_proxy",
        "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE",
    }
    return {name: value for name, value in os.environ.items() if name in allowed}


def _terminate(process: subprocess.Popen[bytes]) -> None:
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=2)


def _run_bounded(
    command: list[str],
    *,
    cwd: Path,
    timeout: float,
) -> tuple[int, bytes, bytes]:
    process = subprocess.Popen(
        command,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_child_environment(),
        start_new_session=True,
        close_fds=True,
    )
    assert process.stdout is not None and process.stderr is not None
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ, "stdout")
    selector.register(process.stderr, selectors.EVENT_READ, "stderr")
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    deadline = time.monotonic() + timeout
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _terminate(process)
                raise McpToolError("cli_timeout", "local workflow CLI timed out", retryable=True)
            for key, _ in selector.select(min(0.1, remaining)):
                chunk = os.read(key.fileobj.fileno(), 8192)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                target = buffers[key.data]
                target.extend(chunk)
                limit = MAX_STDOUT_BYTES if key.data == "stdout" else MAX_STDERR_BYTES
                if len(target) > limit:
                    _terminate(process)
                    raise McpToolError("cli_output_limit", f"local workflow CLI {key.data} exceeded its limit")
        return process.wait(timeout=max(0.1, deadline - time.monotonic())), bytes(buffers["stdout"]), bytes(buffers["stderr"])
    finally:
        selector.close()
        process.stdout.close()
        process.stderr.close()


def _parse_cli_json(code: int, stdout: bytes, stderr: bytes, *, request_id: str | None = None) -> dict[str, Any]:
    if code:
        message = _sanitize(stderr.decode("utf-8", errors="replace"))
        error_code = "cli_error"
        if "not authorized" in message or "identity has drifted" in message:
            error_code = "unauthorized_root"
        raise McpToolError(error_code, message, data={"request_id": request_id} if request_id else None)
    try:
        value = json.loads(stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise McpToolError("cli_protocol_error", "local workflow CLI returned malformed JSON") from exc
    if not isinstance(value, dict):
        raise McpToolError("cli_protocol_error", "local workflow CLI returned a non-object result")
    return value


def _plan_summary(value: Mapping[str, Any]) -> dict[str, Any]:
    tasks = []
    for raw in value.get("tasks", []):
        if not isinstance(raw, dict):
            raise McpToolError("cli_protocol_error", "plan task is malformed")
        tasks.append({
            name: raw.get(name)
            for name in (
                "id", "depends_on", "foreach", "fanout_items", "max_items", "planned_calls",
                "sandbox", "cwd", "model", "reasoning_effort", "agent", "agent_sha256",
                "write_isolation", "timeout_seconds", "retries",
            )
        })
    loop = value.get("loop")
    loop_summary = None
    if isinstance(loop, dict):
        loop_summary = {
            "mode": loop.get("mode"),
            "persistent": True,
            "effective_max_calls_per_cycle": loop.get("effective_max_calls_per_cycle"),
            "planned_calls_per_cycle": loop.get("planned_calls_per_cycle"),
            "cost_model": loop.get("cost_model"),
        }
    return {
        "workflow_id": value.get("workflow_id"),
        "workflow_scope": value.get("scope"),
        "workflow_digest": value.get("workflow_digest"),
        "run_kind": "persistent" if loop_summary else "finite",
        "task_count": len(tasks),
        "task_order": [item["id"] for item in tasks],
        "max_parallel": value.get("max_parallel"),
        "max_calls": value.get("max_calls"),
        "planned_calls": value.get("planned_calls"),
        "loop": loop_summary,
        "tasks": tasks,
        "validation_warnings": [],
    }


def _success(tool: str, data: Mapping[str, Any], request_id: str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"schema_version": RESULT_SCHEMA, "ok": True, "tool": tool, "data": dict(data)}
    if request_id is not None:
        result["request_id"] = request_id
    return result


def _failure(tool: str, error: McpToolError, request_id: str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "schema_version": RESULT_SCHEMA,
        "ok": False,
        "tool": tool,
        "error": {"code": error.code, "message": _sanitize(error.message), "retryable": error.retryable},
    }
    if request_id is not None:
        result["request_id"] = request_id
    if error.data is not None:
        result["data"] = error.data
    return result


class WorkflowMcpServer:
    def __init__(self) -> None:
        self.plugin_root = Path(__file__).resolve().parent.parent
        self.cli = self.plugin_root / "scripts" / "codex_workflows.py"
        self.python = Path(sys.executable).resolve(strict=True)
        resolved_codex = shutil.which("codex")
        self.codex = Path(resolved_codex).resolve(strict=True) if resolved_codex else None

    def tool_list(self) -> list[dict[str, Any]]:
        return TOOLS

    def _invoke(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        _measure(arguments)
        request_id = arguments.get("request_id") if isinstance(arguments.get("request_id"), str) else None
        if name == "workflow_plan":
            _require_keys(arguments, {"project_root", "workflow"}, {"inputs", "max_parallel", "max_calls"})
        elif name == "workflow_run":
            _require_keys(
                arguments,
                {"project_root", "workflow", "request_id"},
                {"inputs", "max_parallel", "max_calls", "allow_workspace_write", "allow_danger_full_access"},
            )
            if UUID4.fullmatch(_string(arguments, "request_id")) is None:
                raise McpToolError("invalid_arguments", "request_id must be a canonical lower-case UUIDv4")
        elif name == "workflow_status":
            _require_keys(arguments, {"project_root"}, {"run_id", "request_id"})
            if ("run_id" in arguments) == ("request_id" in arguments):
                raise McpToolError("invalid_arguments", "status requires exactly one of run_id or request_id")
        elif name == "workflow_control":
            _require_keys(arguments, {"project_root", "run_id", "request_id", "action"}, set())
            if UUID4.fullmatch(_string(arguments, "request_id")) is None:
                raise McpToolError("invalid_arguments", "request_id must be a canonical lower-case UUIDv4")
            if arguments.get("action") not in {"pause", "resume", "cancel"}:
                raise McpToolError("invalid_arguments", "action must be pause, resume, or cancel")
        else:
            raise McpToolError("unknown_tool", f"unknown tool {name!r}")

        project, workflow, inputs = _validate_common(arguments)
        max_parallel = _optional_integer(arguments, "max_parallel", 1, 128)
        max_calls = _optional_integer(arguments, "max_calls", 1, 100_000)
        if name in {"workflow_status", "workflow_control"}:
            run_id = arguments.get("run_id")
            if run_id is not None and (not isinstance(run_id, str) or RUN_ID.fullmatch(run_id) is None):
                raise McpToolError("invalid_arguments", "run_id has an invalid format")

        try:
            authorized = root_auth.open_authorized(project)
        except root_auth.RootAuthorizationError as exc:
            raise McpToolError("unauthorized_root", "project root is not currently authorized for local MCP") from exc
        with authorized:
            authorized.verify()
            temporary: Path | None = None
            command = [
                str(self.python), str(self.cli),
                "--project-root", str(authorized.project),
                "--mcp-expected-root-identity", authorized.identity_sha256,
            ]
            timeout = 30.0
            try:
                if name in {"workflow_plan", "workflow_run"}:
                    command.append("--mcp-qualified-only")
                    descriptor, temporary_name = tempfile.mkstemp(prefix="workflow-mcp-inputs.", suffix=".json")
                    temporary = Path(temporary_name)
                    os.fchmod(descriptor, 0o600)
                    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                        json.dump(inputs, handle, ensure_ascii=False, sort_keys=True, allow_nan=False)
                        handle.write("\n")
                    command.extend(["plan" if name == "workflow_plan" else "run", str(workflow), "--inputs", str(temporary)])
                    if max_parallel is not None:
                        command.extend(["--max-parallel", str(max_parallel)])
                    if max_calls is not None:
                        command.extend(["--max-calls", str(max_calls)])
                if name == "workflow_run":
                    if self.codex is None:
                        raise McpToolError("codex_unavailable", "Codex CLI executable was not found")
                    command.extend(["--codex-bin", str(self.codex), "--detach", "--mcp-json", "--mcp-request-id", str(request_id)])
                    if arguments.get("allow_workspace_write") is True:
                        command.append("--allow-workspace-write")
                    elif arguments.get("allow_workspace_write") not in {None, False}:
                        raise McpToolError("invalid_arguments", "allow_workspace_write must be boolean")
                    if arguments.get("allow_danger_full_access") is True:
                        command.append("--allow-danger-full-access")
                    elif arguments.get("allow_danger_full_access") not in {None, False}:
                        raise McpToolError("invalid_arguments", "allow_danger_full_access must be boolean")
                    timeout = 60.0
                elif name == "workflow_status":
                    command.extend(["status", "--mcp-json"])
                    if arguments.get("run_id"):
                        command.append(str(arguments["run_id"]))
                    else:
                        command.extend(["--mcp-request-id", str(arguments["request_id"])])
                elif name == "workflow_control":
                    command.extend([
                        str(arguments["action"]), str(arguments["run_id"]), "--mcp-json",
                        "--mcp-request-id", str(request_id),
                    ])
                code, stdout, stderr = _run_bounded(command, cwd=authorized.project, timeout=timeout)
                value = _parse_cli_json(code, stdout, stderr, request_id=request_id)
                authorized.verify()
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
        data = _plan_summary(value) if name == "workflow_plan" else value
        return _success(name, data, request_id=request_id if name in {"workflow_run", "workflow_control"} else None)

    def call_tool(self, name: str, arguments: Any) -> tuple[dict[str, Any], bool]:
        if name not in TOOL_NAMES:
            error = McpToolError("unknown_tool", f"unknown tool {name!r}")
            return _failure(name, error), True
        if not isinstance(arguments, dict):
            error = McpToolError("invalid_arguments", "tool arguments must be an object")
            return _failure(name, error), True
        request_id = arguments.get("request_id") if isinstance(arguments.get("request_id"), str) else None
        try:
            return self._invoke(name, arguments), False
        except McpToolError as exc:
            return _failure(name, exc, request_id=request_id), True
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            error = McpToolError("local_error", _sanitize(str(exc)))
            return _failure(name, error, request_id=request_id), True

    def handle(self, request: Any) -> dict[str, Any] | None:
        if not isinstance(request, dict) or request.get("jsonrpc") != "2.0":
            return _bounded_response({"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid Request"}})
        method = request.get("method")
        request_id = request.get("id")
        if "id" in request and not _valid_jsonrpc_id(request_id):
            return _bounded_response({"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid Request"}})
        if "id" not in request:
            return None
        if method == "initialize":
            params = request.get("params") if isinstance(request.get("params"), dict) else {}
            requested_protocol = params.get("protocolVersion")
            protocol = (
                requested_protocol
                if isinstance(requested_protocol, str) and requested_protocol in SUPPORTED_PROTOCOL_VERSIONS
                else DEFAULT_PROTOCOL_VERSION
            )
            response = {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "protocolVersion": protocol,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "codex-workflow-governor-local", "version": "1.0.0"},
                },
            }
            return _bounded_response(response)
        if method == "ping":
            return _bounded_response({"jsonrpc": "2.0", "id": request_id, "result": {}})
        if method == "tools/list":
            params = request.get("params")
            if params not in (None, {}) and not isinstance(params, dict):
                return _bounded_response({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32602, "message": "Invalid params"}})
            return _bounded_response({"jsonrpc": "2.0", "id": request_id, "result": {"tools": self.tool_list()}})
        if method == "tools/call":
            params = request.get("params")
            if not isinstance(params, dict) or not isinstance(params.get("name"), str):
                return _bounded_response({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32602, "message": "Invalid params"}})
            result, is_error = self.call_tool(params["name"], params.get("arguments", {}))
            text = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            if len(text.encode("utf-8")) > MAX_RESPONSE_BYTES:
                result = _failure(params["name"], McpToolError("response_limit", "tool response exceeded the 1 MiB limit"))
                text = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                is_error = True
            response = {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "content": [{"type": "text", "text": text}],
                    "structuredContent": result,
                    "isError": is_error,
                },
            }
            encoded_response = json.dumps(
                response,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            if len(encoded_response) > MAX_RESPONSE_BYTES:
                result = _failure(
                    params["name"],
                    McpToolError("response_limit", "complete MCP response exceeded the 1 MiB limit"),
                )
                text = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                response = {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "content": [{"type": "text", "text": text}],
                        "structuredContent": result,
                        "isError": True,
                    },
                }
            return _bounded_response(response)
        return _bounded_response({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "Method not found"}})

    def serve(self) -> int:
        stream = sys.stdin.buffer
        while True:
            line = stream.readline(MAX_FRAME_BYTES + 2)
            if not line:
                return 0
            if len(line) > MAX_FRAME_BYTES:
                while line and not line.endswith(b"\n"):
                    line = stream.readline(MAX_FRAME_BYTES + 2)
                response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Request exceeds 1 MiB"}}
            else:
                try:
                    request = json.loads(line.decode("utf-8"))
                    _measure(request)
                    response = self.handle(request)
                except (UnicodeDecodeError, ValueError):
                    response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
                except McpToolError as exc:
                    response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": _sanitize(exc.message)}}
            if response is not None:
                response = _bounded_response(response)
                payload = json.dumps(response, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
                sys.stdout.write(payload + "\n")
                sys.stdout.flush()


def _root_command(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="workflow-mcp-roots")
    subparsers = parser.add_subparsers(dest="command", required=True)
    authorize = subparsers.add_parser("authorize")
    authorize.add_argument("project_root")
    revoke = subparsers.add_parser("revoke")
    revoke.add_argument("project_root")
    subparsers.add_parser("list")
    args = parser.parse_args(argv)
    try:
        if args.command == "authorize":
            print(json.dumps(root_auth.authorize(args.project_root), ensure_ascii=False, sort_keys=True))
        elif args.command == "revoke":
            print(json.dumps({"removed": root_auth.revoke(args.project_root)}, sort_keys=True))
        else:
            print(json.dumps({"roots": root_auth.list_authorized()}, ensure_ascii=False, sort_keys=True))
    except root_auth.RootAuthorizationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


def main(argv: list[str] | None = None) -> int:
    os.umask(0o077)
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] == "roots":
        return _root_command(arguments[1:])
    if arguments:
        print("error: local MCP server accepts no command arguments", file=sys.stderr)
        return 2
    return WorkflowMcpServer().serve()


if __name__ == "__main__":
    raise SystemExit(main())
