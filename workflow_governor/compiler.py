"""Deterministic compiler and renderer for workflow source graphs."""

from __future__ import annotations

import re
import tomllib
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Iterable

from .contracts import (
    LOCK_SCHEMA,
    ContractError,
    decode_lock,
    decode_source,
    digest_bytes,
    digest_json,
    load_json,
    write_json,
)


RULE_LINE = re.compile(r"^\s*(?:[-*+] |\d+[.)] )\S")


def _repository_path(repository: Path, relative: str) -> Path:
    root = repository.resolve()
    path = (root / relative).resolve()
    if path != root and root not in path.parents:
        raise ContractError(relative, "resolves outside the repository")
    return path


def _line_slice(path: Path, start: int, end: int) -> bytes:
    try:
        lines = path.read_bytes().splitlines(keepends=True)
    except OSError as exc:
        raise ContractError(str(path), str(exc)) from exc
    if end > len(lines):
        raise ContractError(str(path), f"line range {start}-{end} exceeds {len(lines)} lines")
    return b"".join(lines[start - 1 : end])


def discover_rule_lines(path: Path) -> list[int]:
    """Discover one-line Markdown instruction units outside fenced blocks."""

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ContractError(str(path), str(exc)) from exc
    discovered: list[int] = []
    in_fence = False
    in_managed_routing = False
    for number, line in enumerate(lines, 1):
        if line.strip() == "<!-- workflow-governor:start -->":
            in_managed_routing = True
            continue
        if line.strip() == "<!-- workflow-governor:end -->":
            in_managed_routing = False
            continue
        stripped = line.lstrip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            continue
        if not in_fence and not in_managed_routing and RULE_LINE.match(line):
            discovered.append(number)
    return discovered


def _validate_rule_coverage(repository: Path, rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_path: dict[str, list[dict[str, Any]]] = defaultdict(list)
    verified: list[dict[str, Any]] = []
    for rule in rules:
        path = _repository_path(repository, rule["source_path"])
        observed = digest_bytes(_line_slice(path, rule["line_start"], rule["line_end"]))
        if observed != rule["digest"]:
            raise ContractError(
                f"instruction_rules.{rule['id']}.digest",
                f"source drift: expected {rule['digest']}, observed {observed}",
            )
        by_path[rule["source_path"]].append(rule)
        item = dict(rule)
        item["observed_digest"] = observed
        verified.append(item)

    for relative, path_rules in by_path.items():
        path = _repository_path(repository, relative)
        if path.name != "AGENTS.md" and "contracts" not in path.parts:
            continue
        covered: set[int] = set()
        for rule in path_rules:
            covered.update(range(rule["line_start"], rule["line_end"] + 1))
        missing = [line for line in discover_rule_lines(path) if line not in covered]
        if missing:
            joined = ", ".join(str(line) for line in missing)
            raise ContractError(relative, f"instruction rule units lack coverage at lines {joined}")
    return sorted(verified, key=lambda item: item["id"])


def _validate_roles(
    repository: Path,
    roles: list[dict[str, str]],
    file_overrides: dict[str, bytes] | None = None,
) -> list[dict[str, str]]:
    verified: list[dict[str, str]] = []
    for role in roles:
        path = _repository_path(repository, role["path"])
        if file_overrides and role["path"] in file_overrides:
            payload = file_overrides[role["path"]]
        else:
            try:
                payload = path.read_bytes()
            except OSError as exc:
                raise ContractError(role["path"], str(exc)) from exc
        observed = digest_bytes(payload)
        if observed != role["digest"]:
            raise ContractError(
                f"role_refs.{role['role_id']}.digest",
                f"role drift: expected {role['digest']}, observed {observed}",
            )
        try:
            role_toml = tomllib.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
            raise ContractError(role["path"], f"invalid role TOML: {exc}") from exc
        instructions = role_toml.get("developer_instructions")
        if not isinstance(instructions, str) or not instructions.strip():
            raise ContractError(role["path"], "role TOML must contain non-empty developer_instructions")
        verified.append({**role, "observed_digest": observed})
    return sorted(verified, key=lambda item: item["role_id"])


def _validate_bindings(source: dict[str, Any]) -> None:
    nodes = {node["id"] for node in source["nodes"]}
    roles = {role["role_id"] for role in source["role_refs"]}
    for rule in source["instruction_rules"]:
        for binding in rule["bindings"]:
            targets = nodes if binding["kind"] == "node" else roles
            if binding["id"] not in targets:
                raise ContractError(
                    f"instruction_rules.{rule['id']}.bindings",
                    f"unknown {binding['kind']} {binding['id']}",
                )


def _validate_graph(source: dict[str, Any]) -> None:
    nodes = {node["id"]: node for node in source["nodes"]}
    templates = {template["id"]: template for template in source["task_templates"]}
    roles = {role["role_id"] for role in source["role_refs"]}
    if not any(node["type"] == "end" for node in nodes.values()):
        raise ContractError("source.nodes", "must contain at least one end node")
    for node in nodes.values():
        template = node.get("template")
        role = node.get("role")
        if template is not None and template not in templates:
            raise ContractError(f"nodes.{node['id']}.template", f"unknown template {template}")
        if role is not None and role not in roles:
            raise ContractError(f"nodes.{node['id']}.role", f"unknown role {role}")
        if node["retry_limit"] > source["delegation_policy"]["max_retry"]:
            raise ContractError(f"nodes.{node['id']}.retry_limit", "exceeds delegation_policy.max_retry")
    for template in templates.values():
        for child in template["allowed_child_templates"]:
            if child not in templates:
                raise ContractError(
                    f"task_templates.{template['id']}.allowed_child_templates",
                    f"unknown template {child}",
                )
        if len(template["allowed_child_templates"]) > template["fanout_limit"] and template["fanout_limit"] != 0:
            raise ContractError(
                f"task_templates.{template['id']}.fanout_limit",
                "is smaller than the number of declared child templates",
            )

    incoming: dict[str, int] = {node_id: 0 for node_id in nodes}
    outgoing: dict[str, list[str]] = defaultdict(list)
    retry_edges: set[tuple[str, str]] = set()
    seen_orders: set[tuple[str, int]] = set()
    for edge in source["edges"]:
        if edge["from"] not in nodes or edge["to"] not in nodes:
            raise ContractError(f"edges.{edge['id']}", "references an unknown node")
        order_key = (edge["from"], edge["order"])
        if order_key in seen_orders:
            raise ContractError(f"edges.{edge['id']}.order", "must be unique for each source node")
        seen_orders.add(order_key)
        if nodes[edge["from"]]["type"] == "end":
            raise ContractError(f"edges.{edge['id']}", "end nodes cannot have outgoing edges")
        if edge["guard"]["kind"] == "retry_available":
            retry_node = edge["guard"]["retry_node"]
            if retry_node not in nodes or nodes[retry_node]["retry_limit"] == 0:
                raise ContractError(f"edges.{edge['id']}.guard.retry_node", "must reference a retry-enabled node")
            retry_edges.add((edge["from"], edge["to"]))
            continue
        outgoing[edge["from"]].append(edge["to"])
        incoming[edge["to"]] += 1

    starts = sorted(node_id for node_id, count in incoming.items() if count == 0)
    if len(starts) != 1:
        raise ContractError("source.nodes", f"must have exactly one start node, found {starts}")
    queue: deque[str] = deque(starts)
    visited: list[str] = []
    counts = dict(incoming)
    while queue:
        current = queue.popleft()
        visited.append(current)
        for target in sorted(outgoing[current]):
            counts[target] -= 1
            if counts[target] == 0:
                queue.append(target)
    if len(visited) != len(nodes):
        raise ContractError("source.edges", "contains an undeclared cycle")


def _routes(source: dict[str, Any]) -> list[dict[str, Any]]:
    nodes = {node["id"]: node for node in source["nodes"]}
    templates = {template["id"]: template for template in source["task_templates"]}
    routes: list[dict[str, Any]] = []
    for node in source["nodes"]:
        if node["type"] not in {"task", "fanout"}:
            continue
        template = templates[node["template"]]
        routes.append(
            {
                "node_id": node["id"],
                "template_id": node["template"],
                "role_id": node["role"],
                "context_mode": node["context_mode"],
                "allowed_child_templates": template["allowed_child_templates"],
                "fanout_limit": template["fanout_limit"],
                "retry_limit": node["retry_limit"],
            }
        )
    return sorted(routes, key=lambda item: item["node_id"])


def compile_source(
    repository: Path,
    source_value: dict[str, Any],
    *,
    file_overrides: dict[str, bytes] | None = None,
) -> dict[str, Any]:
    source = decode_source(source_value)
    _validate_bindings(source)
    _validate_graph(source)
    rules = _validate_rule_coverage(repository, source["instruction_rules"])
    roles = _validate_roles(repository, source["role_refs"], file_overrides)
    bound = sum(1 for rule in rules if rule["bindings"])
    not_applicable = len(rules) - bound
    body: dict[str, Any] = {
        "schema_version": LOCK_SCHEMA,
        "immutable": True,
        "workflow_id": source["workflow_id"],
        "revision": source["revision"],
        "title": source["title"],
        "description": source["description"],
        "source_digest": digest_json(source),
        "instruction_rules": rules,
        "role_refs": roles,
        "task_templates": source["task_templates"],
        "nodes": source["nodes"],
        "edges": source["edges"],
        "routes": _routes(source),
        "delegation_policy": source["delegation_policy"],
        "coverage": {"bound": bound, "not_applicable": not_applicable, "total": len(rules)},
    }
    lock = {**body, "lock_digest": digest_json(body)}
    decode_lock(lock)
    return lock


def compile_file(repository: Path, source_path: Path) -> dict[str, Any]:
    return compile_source(repository, load_json(source_path))


def render_mermaid(lock_value: dict[str, Any]) -> str:
    lock = decode_lock(lock_value)
    lines = ["flowchart TD"]
    shape = {
        "task": ('["', '"]'),
        "gate": ('{{"', '"}}'),
        "decision": ('{"', '"}'),
        "fanout": ('[["', '"]]'),
        "join": ('(["', '"])'),
        "end": ('(("', '"))'),
    }
    for node in sorted(lock["nodes"], key=lambda item: item["id"]):
        prefix, suffix = shape[node["type"]]
        label = f"{node['id']}\\n{node['type']}"
        lines.append(f"  {node['id']}{prefix}{label}{suffix}")
    for edge in lock["edges"]:
        guard = edge["guard"]["kind"]
        lines.append(f"  {edge['from']} -->|{edge['id']}: {guard}| {edge['to']}")
    return "\n".join(lines) + "\n"


def render_workflow(lock_values: Iterable[dict[str, Any]]) -> str:
    locks = sorted((decode_lock(value) for value in lock_values), key=lambda item: item["workflow_id"])
    lines = ["# Workflow Index", "", "This file is generated from workflow lock files. Do not edit it directly.", ""]
    for lock in locks:
        lines.extend(
            [
                f"## {lock['title']}",
                "",
                f"- Workflow ID: `{lock['workflow_id']}`",
                f"- Revision: `{lock['revision']}`",
                f"- Lock digest: `{lock['lock_digest']}`",
                f"- Rule coverage: `{lock['coverage']['bound']}` bound, `{lock['coverage']['not_applicable']}` not applicable, `{lock['coverage']['total']}` total",
                "",
                lock["description"] or "No description.",
                "",
                "### Graph",
                "",
                "```mermaid",
                render_mermaid(lock).rstrip(),
                "```",
                "",
                "### Roles",
                "",
            ]
        )
        if lock["role_refs"]:
            for role in lock["role_refs"]:
                lines.append(f"- `{role['role_id']}`: `{role['path']}` (`{role['observed_digest']}`)")
        else:
            lines.append("- No delegated roles.")
        lines.extend(["", "### Stages", ""])
        for node in lock["nodes"]:
            required = "required" if node["required"] else "optional"
            template = f", template `{node['template']}`" if "template" in node else ""
            role = f", role `{node['role']}`" if "role" in node else ""
            lines.append(f"- `{node['id']}`: {node['type']}, {required}, context `{node['context_mode']}`{template}{role}")
        lines.extend(["", "### Instruction Coverage", ""])
        if lock["instruction_rules"]:
            for rule in lock["instruction_rules"]:
                if rule["bindings"]:
                    coverage = ", ".join(f"{item['kind']} `{item['id']}`" for item in rule["bindings"])
                else:
                    coverage = f"not applicable: {rule['not_applicable']}"
                lines.append(
                    f"- `{rule['id']}`: `{rule['source_path']}:{rule['line_start']}-{rule['line_end']}`; {coverage}"
                )
        else:
            lines.append("- No instruction rules declared.")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_generated_views(repository: Path, workflow_id: str, lock: dict[str, Any]) -> None:
    workflow_dir = repository / ".codex" / "workflows" / workflow_id
    write_json(workflow_dir / "workflow.lock.json", lock)
    (workflow_dir / "workflow.mmd").write_text(render_mermaid(lock), encoding="utf-8")
    locks = []
    for path in sorted((repository / ".codex" / "workflows").glob("*/workflow.lock.json")):
        if path == workflow_dir / "workflow.lock.json":
            locks.append(lock)
        else:
            locks.append(load_json(path))
    (repository / "WORKFLOW.md").write_text(render_workflow(locks), encoding="utf-8")


def verify_generated_views(repository: Path, workflow_id: str) -> list[str]:
    workflow_dir = repository / ".codex" / "workflows" / workflow_id
    source = load_json(workflow_dir / "workflow.json")
    expected_lock = compile_source(repository, source)
    actual_lock = load_json(workflow_dir / "workflow.lock.json")
    decode_lock(actual_lock)
    errors: list[str] = []
    if actual_lock != expected_lock:
        errors.append("workflow.lock.json differs from deterministic compilation")
    if (workflow_dir / "workflow.mmd").read_text(encoding="utf-8") != render_mermaid(expected_lock):
        errors.append("workflow.mmd differs from deterministic rendering")
    all_locks = []
    for path in sorted((repository / ".codex" / "workflows").glob("*/workflow.lock.json")):
        all_locks.append(expected_lock if path == workflow_dir / "workflow.lock.json" else load_json(path))
    if (repository / "WORKFLOW.md").read_text(encoding="utf-8") != render_workflow(all_locks):
        errors.append("WORKFLOW.md differs from deterministic rendering")
    return errors
