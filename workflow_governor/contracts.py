"""Strict workflow source, lock, dispatch, and result contracts."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable, Mapping


SOURCE_SCHEMA = "workflow-source.v1"
LOCK_SCHEMA = "workflow-lock.v1"
DISPATCH_SCHEMA = "workflow-dispatch.v1"
RESULT_SCHEMA = "workflow-result.v1"

IDENTIFIER = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
VALUE_TYPES = {"string", "integer", "number", "boolean", "object", "array", "null"}
NODE_TYPES = {"task", "gate", "decision", "fanout", "join", "end"}
CONTEXT_MODES = {"parent", "isolated"}
GUARD_KINDS = {"always", "evidence", "input_equals", "output_equals", "retry_available"}
RULE_BINDING_KINDS = {"node", "role"}
RESULT_STATUSES = {"completed", "failed", "blocked", "skipped"}
RUN_MODES = {"guarded", "advisory"}


class ContractError(ValueError):
    """Raised when a workflow contract is invalid."""

    def __init__(self, path: str, message: str) -> None:
        super().__init__(f"{path}: {message}")
        self.path = path
        self.message = message


def canonical_json(value: Any) -> str:
    """Return the canonical JSON representation used by all digests."""

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def digest_text(value: str) -> str:
    return digest_bytes(value.encode("utf-8"))


def load_json(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractError(str(path), str(exc)) from exc
    return _object(value, str(path))


def write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    path.write_text(payload, encoding="utf-8")


def _object(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractError(path, "must be an object")
    return value


def _array(value: Any, path: str, *, nonempty: bool = False) -> list[Any]:
    if not isinstance(value, list):
        raise ContractError(path, "must be an array")
    if nonempty and not value:
        raise ContractError(path, "must not be empty")
    return value


def _string(value: Any, path: str, *, nonempty: bool = True) -> str:
    if not isinstance(value, str):
        raise ContractError(path, "must be a string")
    if nonempty and not value.strip():
        raise ContractError(path, "must not be empty")
    return value


def _boolean(value: Any, path: str) -> bool:
    if not isinstance(value, bool):
        raise ContractError(path, "must be a boolean")
    return value


def _integer(value: Any, path: str, *, minimum: int | None = None) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ContractError(path, "must be an integer")
    if minimum is not None and value < minimum:
        raise ContractError(path, f"must be at least {minimum}")
    return value


def _identifier(value: Any, path: str) -> str:
    result = _string(value, path)
    if not IDENTIFIER.fullmatch(result):
        raise ContractError(path, "must be a lower-case hyphenated identifier")
    return result


def validate_identifier(value: Any, path: str = "identifier") -> str:
    """Validate an externally supplied identifier before using it in a path."""

    return _identifier(value, path)


def workflow_directory(repository: Path, workflow_id: Any) -> Path:
    """Resolve one workflow directory and prove that it stays in the repository."""

    identifier = validate_identifier(workflow_id, "workflow_id")
    root = repository.expanduser().resolve()
    workflows_root = (root / ".codex" / "workflows").resolve(strict=False)
    if workflows_root != root and root not in workflows_root.parents:
        raise ContractError("repository", "workflow root resolves outside the repository")
    candidate = (workflows_root / identifier).resolve(strict=False)
    if candidate == workflows_root or workflows_root not in candidate.parents:
        raise ContractError("workflow_id", "workflow directory resolves outside the workflow root")
    return candidate


def _digest(value: Any, path: str) -> str:
    result = _string(value, path)
    if not SHA256.fullmatch(result):
        raise ContractError(path, "must be a lower-case SHA-256 digest")
    return result


def _relative_path(value: Any, path: str) -> str:
    result = _string(value, path)
    candidate = Path(result)
    if candidate.is_absolute() or ".." in candidate.parts or result.startswith("~"):
        raise ContractError(path, "must be a repository-relative path without parent traversal")
    return candidate.as_posix()


def _enum(value: Any, path: str, allowed: set[str]) -> str:
    result = _string(value, path)
    if result not in allowed:
        raise ContractError(path, f"must be one of {sorted(allowed)}")
    return result


def _keys(
    value: Mapping[str, Any],
    path: str,
    *,
    required: Iterable[str],
    optional: Iterable[str] = (),
) -> None:
    required_set = set(required)
    allowed = required_set | set(optional)
    missing = sorted(required_set - set(value))
    unknown = sorted(set(value) - allowed)
    if missing:
        raise ContractError(path, f"missing fields: {', '.join(missing)}")
    if unknown:
        raise ContractError(path, f"unknown fields: {', '.join(unknown)}")


def _unique(values: list[str], path: str) -> None:
    duplicates = sorted({value for value in values if values.count(value) > 1})
    if duplicates:
        raise ContractError(path, f"duplicate identifiers: {', '.join(duplicates)}")


def _typed_fields(value: Any, path: str) -> dict[str, str]:
    fields = _object(value, path)
    normalized: dict[str, str] = {}
    for name, field_type in fields.items():
        if not isinstance(name, str) or not IDENTIFIER.fullmatch(name):
            raise ContractError(path, f"invalid field identifier: {name!r}")
        normalized[name] = _enum(field_type, f"{path}.{name}", VALUE_TYPES)
    return dict(sorted(normalized.items()))


def validate_typed_values(values: Any, specification: Mapping[str, str], path: str) -> dict[str, Any]:
    actual = _object(values, path)
    if set(actual) != set(specification):
        missing = sorted(set(specification) - set(actual))
        unknown = sorted(set(actual) - set(specification))
        details = []
        if missing:
            details.append(f"missing: {', '.join(missing)}")
        if unknown:
            details.append(f"unknown: {', '.join(unknown)}")
        raise ContractError(path, "; ".join(details))
    python_types: dict[str, tuple[type, ...]] = {
        "string": (str,),
        "integer": (int,),
        "number": (int, float),
        "boolean": (bool,),
        "object": (dict,),
        "array": (list,),
        "null": (type(None),),
    }
    for name, expected in specification.items():
        current = actual[name]
        if expected in {"integer", "number"} and isinstance(current, bool):
            raise ContractError(f"{path}.{name}", f"must be {expected}")
        if not isinstance(current, python_types[expected]):
            raise ContractError(f"{path}.{name}", f"must be {expected}")
    return actual


def decode_source(value: Any) -> dict[str, Any]:
    root = _object(value, "source")
    _keys(
        root,
        "source",
        required=(
            "schema_version",
            "workflow_id",
            "revision",
            "title",
            "description",
            "instruction_rules",
            "role_refs",
            "task_templates",
            "nodes",
            "edges",
            "delegation_policy",
        ),
    )
    if _string(root["schema_version"], "source.schema_version") != SOURCE_SCHEMA:
        raise ContractError("source.schema_version", f"must equal {SOURCE_SCHEMA}")
    workflow_id = _identifier(root["workflow_id"], "source.workflow_id")
    revision = _integer(root["revision"], "source.revision", minimum=1)
    title = _string(root["title"], "source.title")
    description = _string(root["description"], "source.description", nonempty=False)

    rules: list[dict[str, Any]] = []
    for index, raw in enumerate(_array(root["instruction_rules"], "source.instruction_rules")):
        path = f"source.instruction_rules[{index}]"
        item = _object(raw, path)
        _keys(
            item,
            path,
            required=("id", "source_path", "line_start", "line_end", "digest", "category", "applies_to", "bindings"),
            optional=("not_applicable",),
        )
        bindings: list[dict[str, str]] = []
        for binding_index, raw_binding in enumerate(_array(item["bindings"], f"{path}.bindings")):
            binding_path = f"{path}.bindings[{binding_index}]"
            binding = _object(raw_binding, binding_path)
            _keys(binding, binding_path, required=("kind", "id"))
            bindings.append(
                {
                    "kind": _enum(binding["kind"], f"{binding_path}.kind", RULE_BINDING_KINDS),
                    "id": _identifier(binding["id"], f"{binding_path}.id"),
                }
            )
        not_applicable = item.get("not_applicable")
        if bool(bindings) == bool(not_applicable):
            raise ContractError(path, "must declare bindings or one non-empty not_applicable reason")
        line_start = _integer(item["line_start"], f"{path}.line_start", minimum=1)
        line_end = _integer(item["line_end"], f"{path}.line_end", minimum=line_start)
        normalized_rule: dict[str, Any] = {
            "id": _identifier(item["id"], f"{path}.id"),
            "source_path": _relative_path(item["source_path"], f"{path}.source_path"),
            "line_start": line_start,
            "line_end": line_end,
            "digest": _digest(item["digest"], f"{path}.digest"),
            "category": _identifier(item["category"], f"{path}.category"),
            "applies_to": _string(item["applies_to"], f"{path}.applies_to"),
            "bindings": bindings,
        }
        if not_applicable is not None:
            normalized_rule["not_applicable"] = _string(not_applicable, f"{path}.not_applicable")
        rules.append(normalized_rule)
    _unique([rule["id"] for rule in rules], "source.instruction_rules")

    roles: list[dict[str, str]] = []
    for index, raw in enumerate(_array(root["role_refs"], "source.role_refs")):
        path = f"source.role_refs[{index}]"
        item = _object(raw, path)
        _keys(item, path, required=("role_id", "path", "digest"))
        roles.append(
            {
                "role_id": _identifier(item["role_id"], f"{path}.role_id"),
                "path": _relative_path(item["path"], f"{path}.path"),
                "digest": _digest(item["digest"], f"{path}.digest"),
            }
        )
    _unique([role["role_id"] for role in roles], "source.role_refs")

    templates: list[dict[str, Any]] = []
    for index, raw in enumerate(_array(root["task_templates"], "source.task_templates", nonempty=True)):
        path = f"source.task_templates[{index}]"
        item = _object(raw, path)
        _keys(item, path, required=("id", "inputs", "outputs", "allowed_child_templates", "fanout_limit"))
        children = [
            _identifier(child, f"{path}.allowed_child_templates[{child_index}]")
            for child_index, child in enumerate(_array(item["allowed_child_templates"], f"{path}.allowed_child_templates"))
        ]
        _unique(children, f"{path}.allowed_child_templates")
        templates.append(
            {
                "id": _identifier(item["id"], f"{path}.id"),
                "inputs": _typed_fields(item["inputs"], f"{path}.inputs"),
                "outputs": _typed_fields(item["outputs"], f"{path}.outputs"),
                "allowed_child_templates": sorted(children),
                "fanout_limit": _integer(item["fanout_limit"], f"{path}.fanout_limit", minimum=0),
            }
        )
    _unique([template["id"] for template in templates], "source.task_templates")

    nodes: list[dict[str, Any]] = []
    for index, raw in enumerate(_array(root["nodes"], "source.nodes", nonempty=True)):
        path = f"source.nodes[{index}]"
        item = _object(raw, path)
        _keys(
            item,
            path,
            required=("id", "type", "required", "context_mode"),
            optional=("template", "role", "retry_limit"),
        )
        node_type = _enum(item["type"], f"{path}.type", NODE_TYPES)
        node: dict[str, Any] = {
            "id": _identifier(item["id"], f"{path}.id"),
            "type": node_type,
            "required": _boolean(item["required"], f"{path}.required"),
            "context_mode": _enum(item["context_mode"], f"{path}.context_mode", CONTEXT_MODES),
            "retry_limit": _integer(item.get("retry_limit", 0), f"{path}.retry_limit", minimum=0),
        }
        if "template" in item:
            node["template"] = _identifier(item["template"], f"{path}.template")
        if "role" in item:
            node["role"] = _identifier(item["role"], f"{path}.role")
        if node_type in {"task", "fanout"} and ("template" not in node or "role" not in node):
            raise ContractError(path, "task and fanout nodes require template and role")
        if node_type not in {"task", "fanout"} and ("template" in node or "role" in node):
            raise ContractError(path, "only task and fanout nodes may declare template and role")
        if node_type == "end" and node["required"] is False:
            raise ContractError(path, "end nodes must be required")
        nodes.append(node)
    _unique([node["id"] for node in nodes], "source.nodes")

    edges: list[dict[str, Any]] = []
    edge_ids: list[str] = []
    for index, raw in enumerate(_array(root["edges"], "source.edges")):
        path = f"source.edges[{index}]"
        item = _object(raw, path)
        _keys(item, path, required=("id", "from", "to", "order", "guard"))
        guard_path = f"{path}.guard"
        guard = _object(item["guard"], guard_path)
        _keys(guard, guard_path, required=("kind",), optional=("field", "equals", "evidence", "retry_node"))
        kind = _enum(guard["kind"], f"{guard_path}.kind", GUARD_KINDS)
        normalized_guard: dict[str, Any] = {"kind": kind}
        if kind in {"input_equals", "output_equals"}:
            if "field" not in guard or "equals" not in guard:
                raise ContractError(guard_path, f"{kind} requires field and equals")
            normalized_guard["field"] = _identifier(guard["field"], f"{guard_path}.field")
            normalized_guard["equals"] = guard["equals"]
        if kind == "evidence":
            normalized_guard["evidence"] = _string(guard.get("evidence"), f"{guard_path}.evidence")
        if kind == "retry_available":
            normalized_guard["retry_node"] = _identifier(guard.get("retry_node"), f"{guard_path}.retry_node")
        edge_id = _identifier(item["id"], f"{path}.id")
        edge_ids.append(edge_id)
        edges.append(
            {
                "id": edge_id,
                "from": _identifier(item["from"], f"{path}.from"),
                "to": _identifier(item["to"], f"{path}.to"),
                "order": _integer(item["order"], f"{path}.order", minimum=0),
                "guard": normalized_guard,
            }
        )
    _unique(edge_ids, "source.edges")

    policy = _object(root["delegation_policy"], "source.delegation_policy")
    _keys(policy, "source.delegation_policy", required=("max_depth", "allow_cycles", "max_retry"))
    normalized_policy = {
        "max_depth": _integer(policy["max_depth"], "source.delegation_policy.max_depth", minimum=0),
        "allow_cycles": _boolean(policy["allow_cycles"], "source.delegation_policy.allow_cycles"),
        "max_retry": _integer(policy["max_retry"], "source.delegation_policy.max_retry", minimum=0),
    }
    if normalized_policy["max_depth"] > 2:
        raise ContractError("source.delegation_policy.max_depth", "v1 supports a maximum depth of 2")
    if normalized_policy["allow_cycles"]:
        raise ContractError("source.delegation_policy.allow_cycles", "v1 forbids arbitrary cycles")

    return {
        "schema_version": SOURCE_SCHEMA,
        "workflow_id": workflow_id,
        "revision": revision,
        "title": title,
        "description": description,
        "instruction_rules": sorted(rules, key=lambda item: item["id"]),
        "role_refs": sorted(roles, key=lambda item: item["role_id"]),
        "task_templates": sorted(templates, key=lambda item: item["id"]),
        "nodes": sorted(nodes, key=lambda item: item["id"]),
        "edges": sorted(edges, key=lambda item: (item["from"], item["order"], item["id"])),
        "delegation_policy": normalized_policy,
    }


def decode_lock(value: Any, *, verify_digest: bool = True) -> dict[str, Any]:
    root = _object(value, "lock")
    _keys(
        root,
        "lock",
        required=(
            "schema_version",
            "immutable",
            "workflow_id",
            "revision",
            "title",
            "description",
            "source_digest",
            "lock_digest",
            "instruction_rules",
            "role_refs",
            "task_templates",
            "nodes",
            "edges",
            "routes",
            "delegation_policy",
            "coverage",
        ),
    )
    if root.get("schema_version") != LOCK_SCHEMA:
        raise ContractError("lock.schema_version", f"must equal {LOCK_SCHEMA}")
    if root.get("immutable") is not True:
        raise ContractError("lock.immutable", "must be true")
    _identifier(root.get("workflow_id"), "lock.workflow_id")
    _integer(root.get("revision"), "lock.revision", minimum=1)
    _string(root.get("title"), "lock.title")
    _string(root.get("description"), "lock.description", nonempty=False)
    _digest(root.get("source_digest"), "lock.source_digest")
    expected = _digest(root.get("lock_digest"), "lock.lock_digest")
    if verify_digest:
        body = dict(root)
        body.pop("lock_digest")
        observed = digest_json(body)
        if expected != observed:
            raise ContractError("lock.lock_digest", f"expected {observed}, found {expected}")
    for index, raw in enumerate(_array(root.get("instruction_rules"), "lock.instruction_rules")):
        path = f"lock.instruction_rules[{index}]"
        item = _object(raw, path)
        required = {"id", "source_path", "line_start", "line_end", "digest", "observed_digest", "category", "applies_to", "bindings"}
        optional = {"not_applicable"}
        _keys(item, path, required=required, optional=optional)
        _identifier(item["id"], f"{path}.id")
        _relative_path(item["source_path"], f"{path}.source_path")
        start = _integer(item["line_start"], f"{path}.line_start", minimum=1)
        _integer(item["line_end"], f"{path}.line_end", minimum=start)
        _digest(item["digest"], f"{path}.digest")
        _digest(item["observed_digest"], f"{path}.observed_digest")
        _identifier(item["category"], f"{path}.category")
        _string(item["applies_to"], f"{path}.applies_to")
        bindings = _array(item["bindings"], f"{path}.bindings")
        for binding_index, raw_binding in enumerate(bindings):
            binding_path = f"{path}.bindings[{binding_index}]"
            binding = _object(raw_binding, binding_path)
            _keys(binding, binding_path, required=("kind", "id"))
            _enum(binding["kind"], f"{binding_path}.kind", RULE_BINDING_KINDS)
            _identifier(binding["id"], f"{binding_path}.id")
        if bool(bindings) == bool(item.get("not_applicable")):
            raise ContractError(path, "must declare bindings or one non-empty not_applicable reason")
        if "not_applicable" in item:
            _string(item["not_applicable"], f"{path}.not_applicable")
    for index, raw in enumerate(_array(root.get("role_refs"), "lock.role_refs")):
        path = f"lock.role_refs[{index}]"
        item = _object(raw, path)
        _keys(item, path, required=("role_id", "path", "digest", "observed_digest"))
        _identifier(item["role_id"], f"{path}.role_id")
        _relative_path(item["path"], f"{path}.path")
        _digest(item["digest"], f"{path}.digest")
        _digest(item["observed_digest"], f"{path}.observed_digest")
    for index, raw in enumerate(_array(root.get("task_templates"), "lock.task_templates")):
        path = f"lock.task_templates[{index}]"
        item = _object(raw, path)
        _keys(item, path, required=("id", "inputs", "outputs", "allowed_child_templates", "fanout_limit"))
        _identifier(item["id"], f"{path}.id")
        _typed_fields(item["inputs"], f"{path}.inputs")
        _typed_fields(item["outputs"], f"{path}.outputs")
        for child_index, child in enumerate(_array(item["allowed_child_templates"], f"{path}.allowed_child_templates")):
            _identifier(child, f"{path}.allowed_child_templates[{child_index}]")
        _integer(item["fanout_limit"], f"{path}.fanout_limit", minimum=0)
    for index, raw in enumerate(_array(root.get("nodes"), "lock.nodes")):
        path = f"lock.nodes[{index}]"
        item = _object(raw, path)
        _keys(item, path, required=("id", "type", "required", "context_mode", "retry_limit"), optional=("template", "role"))
        _identifier(item["id"], f"{path}.id")
        _enum(item["type"], f"{path}.type", NODE_TYPES)
        _boolean(item["required"], f"{path}.required")
        _enum(item["context_mode"], f"{path}.context_mode", CONTEXT_MODES)
        _integer(item["retry_limit"], f"{path}.retry_limit", minimum=0)
        if "template" in item:
            _identifier(item["template"], f"{path}.template")
        if "role" in item:
            _identifier(item["role"], f"{path}.role")
    for index, raw in enumerate(_array(root.get("edges"), "lock.edges")):
        path = f"lock.edges[{index}]"
        item = _object(raw, path)
        _keys(item, path, required=("id", "from", "to", "order", "guard"))
        for field in ("id", "from", "to"):
            _identifier(item[field], f"{path}.{field}")
        _integer(item["order"], f"{path}.order", minimum=0)
        guard = _object(item["guard"], f"{path}.guard")
        _keys(guard, f"{path}.guard", required=("kind",), optional=("field", "equals", "evidence", "retry_node"))
        _enum(guard["kind"], f"{path}.guard.kind", GUARD_KINDS)
    for index, raw in enumerate(_array(root.get("routes"), "lock.routes")):
        path = f"lock.routes[{index}]"
        item = _object(raw, path)
        _keys(item, path, required=("node_id", "template_id", "role_id", "context_mode", "allowed_child_templates", "fanout_limit", "retry_limit"))
        for field in ("node_id", "template_id", "role_id"):
            _identifier(item[field], f"{path}.{field}")
        _enum(item["context_mode"], f"{path}.context_mode", CONTEXT_MODES)
        for child_index, child in enumerate(_array(item["allowed_child_templates"], f"{path}.allowed_child_templates")):
            _identifier(child, f"{path}.allowed_child_templates[{child_index}]")
        _integer(item["fanout_limit"], f"{path}.fanout_limit", minimum=0)
        _integer(item["retry_limit"], f"{path}.retry_limit", minimum=0)
    policy = _object(root.get("delegation_policy"), "lock.delegation_policy")
    _keys(policy, "lock.delegation_policy", required=("max_depth", "allow_cycles", "max_retry"))
    coverage = _object(root.get("coverage"), "lock.coverage")
    _keys(coverage, "lock.coverage", required=("bound", "not_applicable", "total"))
    return root


def decode_dispatch(value: Any) -> dict[str, Any]:
    root = _object(value, "dispatch")
    _keys(
        root,
        "dispatch",
        required=(
            "schema_version",
            "workflow_id",
            "run_id",
            "node_id",
            "template_id",
            "role_id",
            "depth",
            "context_mode",
            "inputs",
            "allowed_paths",
            "required_outputs",
            "revision",
            "lock_digest",
            "permit_id",
            "spawn_tool",
            "spawn_arguments",
            "spawn_arguments_digest",
        ),
    )
    if root.get("schema_version") != DISPATCH_SCHEMA:
        raise ContractError("dispatch.schema_version", f"must equal {DISPATCH_SCHEMA}")
    for field in ("workflow_id", "node_id", "template_id", "role_id"):
        _identifier(root.get(field), f"dispatch.{field}")
    _string(root.get("run_id"), "dispatch.run_id")
    _integer(root.get("depth"), "dispatch.depth", minimum=0)
    _enum(root.get("context_mode"), "dispatch.context_mode", CONTEXT_MODES)
    _object(root.get("inputs"), "dispatch.inputs")
    paths = _array(root.get("allowed_paths"), "dispatch.allowed_paths")
    for index, path_value in enumerate(paths):
        _relative_path(path_value, f"dispatch.allowed_paths[{index}]")
    required_outputs = _object(root.get("required_outputs"), "dispatch.required_outputs")
    _typed_fields(required_outputs, "dispatch.required_outputs")
    _integer(root.get("revision"), "dispatch.revision", minimum=1)
    _digest(root.get("lock_digest"), "dispatch.lock_digest")
    _string(root.get("permit_id"), "dispatch.permit_id")
    if root.get("spawn_tool") != "Agent":
        raise ContractError("dispatch.spawn_tool", "must equal Agent")
    spawn_arguments = _object(root.get("spawn_arguments"), "dispatch.spawn_arguments")
    expected = _digest(root.get("spawn_arguments_digest"), "dispatch.spawn_arguments_digest")
    observed = digest_json(spawn_arguments)
    if expected != observed:
        raise ContractError("dispatch.spawn_arguments_digest", f"expected {observed}, found {expected}")
    return root


def decode_result(value: Any) -> dict[str, Any]:
    root = _object(value, "result")
    _keys(
        root,
        "result",
        required=(
            "schema_version",
            "workflow_id",
            "run_id",
            "node_id",
            "permit_id",
            "status",
            "outputs",
            "evidence",
            "artifact_digests",
        ),
    )
    if root.get("schema_version") != RESULT_SCHEMA:
        raise ContractError("result.schema_version", f"must equal {RESULT_SCHEMA}")
    _identifier(root.get("workflow_id"), "result.workflow_id")
    _string(root.get("run_id"), "result.run_id")
    _identifier(root.get("node_id"), "result.node_id")
    _string(root.get("permit_id"), "result.permit_id")
    _enum(root.get("status"), "result.status", RESULT_STATUSES)
    _object(root.get("outputs"), "result.outputs")
    evidence = _array(root.get("evidence"), "result.evidence")
    for index, item in enumerate(evidence):
        _string(item, f"result.evidence[{index}]")
    artifacts = _object(root.get("artifact_digests"), "result.artifact_digests")
    for artifact_path, artifact_digest in artifacts.items():
        _relative_path(artifact_path, f"result.artifact_digests.{artifact_path}")
        _digest(artifact_digest, f"result.artifact_digests.{artifact_path}")
    return root
