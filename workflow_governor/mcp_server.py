"""Minimal stdlib Model Context Protocol server for Workflow Governor."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Callable

from .contracts import ContractError
from .runtime import WorkflowRuntime


JsonObject = dict[str, Any]


def _schema(properties: JsonObject, required: list[str]) -> JsonObject:
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


STRING = {"type": "string"}
BOOLEAN = {"type": "boolean"}
OBJECT = {"type": "object"}


READER_TOOLS = [
    ("workflow_list", "List compiled workflows in one repository.", _schema({"repository": STRING}, ["repository"]), True),
    ("workflow_get", "Get one strict workflow source and lock.", _schema({"repository": STRING, "workflow_id": STRING}, ["repository", "workflow_id"]), True),
    ("workflow_check", "Check source, lock, role, instruction, Mermaid, and index digests.", _schema({"repository": STRING, "workflow_id": STRING}, ["repository", "workflow_id"]), True),
    ("workflow_analyze", "Separate deterministic workflow errors from advisory recommendations.", _schema({"repository": STRING, "workflow_id": STRING}, ["repository", "workflow_id"]), True),
    ("workflow_status", "Read workflow run and node status.", _schema({"repository": STRING, "workflow_id": STRING, "run_id": STRING}, ["repository"]), True),
    ("workflow_export", "Export a bounded workflow or run ledger snapshot.", _schema({"repository": STRING, "workflow_id": STRING, "run_id": STRING}, ["repository"]), True),
]


MAINTAINER_TOOLS = [
    (
        "workflow_draft",
        "Save a validated workflow draft outside the repository.",
        _schema(
            {
                "repository": STRING,
                "workflow_id": STRING,
                "source": OBJECT,
                "role_files": {"type": "object", "additionalProperties": {"type": "string"}},
            },
            ["repository", "workflow_id", "source", "role_files"],
        ),
        False,
    ),
    ("workflow_apply", "Atomically apply a saved draft and generated routing views.", _schema({"repository": STRING, "workflow_id": STRING}, ["repository", "workflow_id"]), False),
    (
        "workflow_start_run",
        "Start a guarded or advisory workflow run; guarded mode requires a recent hook heartbeat and native context control.",
        _schema(
            {
                "repository": STRING,
                "workflow_id": STRING,
                "requested_mode": {"type": "string", "enum": ["guarded", "advisory"]},
                "native_context_control": BOOLEAN,
                "session_id": STRING,
            },
            ["repository", "workflow_id", "requested_mode", "native_context_control"],
        ),
        False,
    ),
    (
        "workflow_prepare_dispatch",
        "Issue a one-time permit and exact Agent spawn arguments for a ready node.",
        _schema(
            {
                "run_id": STRING,
                "node_id": STRING,
                "inputs": OBJECT,
                "allowed_paths": {"type": "array", "items": STRING},
                "depth": {"type": "integer", "minimum": 0, "maximum": 2},
                "template_id": STRING,
                "role_id": STRING,
                "context_mode": {"type": "string", "enum": ["parent", "isolated"]},
                "required_outputs": {"type": "object", "additionalProperties": {"type": "string"}},
            },
            ["run_id", "node_id", "inputs", "allowed_paths", "depth"],
        ),
        False,
    ),
    ("workflow_submit_result", "Validate and register a typed workflow-result.v1 packet.", _schema({"result": OBJECT}, ["result"]), False),
    (
        "workflow_advance",
        "Advance through the first matching deterministic edge and record declared skips.",
        _schema(
            {
                "run_id": STRING,
                "evidence": {"type": "array", "items": STRING},
                "skip_nodes": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"node_id": STRING, "evidence": STRING},
                        "required": ["node_id", "evidence"],
                        "additionalProperties": False,
                    },
                },
            },
            ["run_id", "evidence", "skip_nodes"],
        ),
        False,
    ),
    ("workflow_render", "Regenerate Mermaid and WORKFLOW.md only from verified locks.", _schema({"repository": STRING, "workflow_id": STRING}, ["repository", "workflow_id"]), False),
]


DESTRUCTIVE_TOOLS = {
    "workflow_draft",
    "workflow_apply",
    "workflow_prepare_dispatch",
    "workflow_submit_result",
    "workflow_advance",
    "workflow_render",
}
IDEMPOTENT_WRITE_TOOLS = {"workflow_draft", "workflow_apply", "workflow_render"}


class McpServer:
    def __init__(self, surface: str) -> None:
        if surface not in {"reader", "maintainer"}:
            raise ValueError("surface must be reader or maintainer")
        self.surface = surface
        self.runtime = WorkflowRuntime()
        self.tools = READER_TOOLS if surface == "reader" else MAINTAINER_TOOLS

    def tool_list(self) -> list[JsonObject]:
        return [
            {
                "name": name,
                "description": description,
                "inputSchema": schema,
                "annotations": {
                    "readOnlyHint": read_only,
                    "destructiveHint": name in DESTRUCTIVE_TOOLS,
                    "idempotentHint": read_only or name in IDEMPOTENT_WRITE_TOOLS,
                    "openWorldHint": False,
                },
            }
            for name, description, schema, read_only in self.tools
        ]

    def call(self, name: str, arguments: JsonObject) -> Any:
        handlers: dict[str, Callable[..., Any]] = {
            "workflow_list": self.runtime.list_workflows,
            "workflow_get": self.runtime.get_workflow,
            "workflow_check": self.runtime.check_workflow,
            "workflow_analyze": self.runtime.analyze_workflow,
            "workflow_status": self.runtime.workflow_status,
            "workflow_export": self.runtime.export_state,
            "workflow_draft": self.runtime.save_draft,
            "workflow_apply": self.runtime.apply_draft,
            "workflow_start_run": self.runtime.start_run,
            "workflow_prepare_dispatch": self.runtime.prepare_dispatch,
            "workflow_submit_result": self.runtime.submit_result,
            "workflow_advance": self.runtime.advance,
            "workflow_render": self.runtime.render,
        }
        allowed = {tool[0] for tool in self.tools}
        if name not in allowed or name not in handlers:
            raise ContractError("tool", f"unknown tool on {self.surface} surface: {name}")
        if not isinstance(arguments, dict):
            raise ContractError("arguments", "must be an object")
        if name == "workflow_submit_result":
            return handlers[name](arguments.get("result"))
        return handlers[name](**arguments)

    def handle(self, request: JsonObject) -> JsonObject | None:
        request_id = request.get("id")
        method = request.get("method")
        if request_id is None:
            return None
        try:
            if method == "initialize":
                result = {
                    "protocolVersion": request.get("params", {}).get("protocolVersion", "2025-06-18"),
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": f"codex-workflow-governor-{self.surface}", "version": "0.4.0"},
                }
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": self.tool_list()}
            elif method == "tools/call":
                params = request.get("params") or {}
                if not isinstance(params, dict):
                    raise ContractError("params", "must be an object")
                value = self.call(params.get("name"), params.get("arguments") or {})
                text = json.dumps(value, ensure_ascii=False, sort_keys=True)
                result = {"content": [{"type": "text", "text": text}], "structuredContent": value, "isError": False}
            else:
                return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": f"Method not found: {method}"}}
            return {"jsonrpc": "2.0", "id": request_id, "result": result}
        except (ContractError, OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            if method == "tools/call":
                value = {"error": str(exc)}
                return {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {"content": [{"type": "text", "text": json.dumps(value, sort_keys=True)}], "structuredContent": value, "isError": True},
                }
            return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32602, "message": str(exc)}}

    def serve(self) -> None:
        for line in sys.stdin:
            if not line.strip():
                continue
            try:
                request = json.loads(line)
                if not isinstance(request, dict):
                    raise ValueError("request must be an object")
                response = self.handle(request)
            except (json.JSONDecodeError, ValueError) as exc:
                response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": str(exc)}}
            if response is not None:
                sys.stdout.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
                sys.stdout.flush()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--surface", choices=("reader", "maintainer"), required=True)
    args = parser.parse_args(argv)
    McpServer(args.surface).serve()
    return 0
