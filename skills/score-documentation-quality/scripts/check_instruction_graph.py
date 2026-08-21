#!/usr/bin/env python3
"""Validate an instruction relationship graph without assigning documentation scores."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path, PurePosixPath
from typing import Any


SKILL_DIR = Path(__file__).resolve().parent.parent
SCHEMA_PATH = SKILL_DIR / "references" / "instruction-graph.schema.json"
NODE_TYPES = {"artifact", "rule", "entity", "scope", "source"}
EDGE_TYPES = {
    "owns", "precedes", "applies_to", "overrides", "derives_from", "aliases",
    "references", "routes_to"
}
EDGE_ENDPOINT_TYPES = {
    "owns": ({"artifact", "source"}, {"rule"}),
    "precedes": ({"artifact", "source"}, {"artifact", "source"}),
    "applies_to": ({"rule"}, {"scope"}),
    "overrides": ({"rule"}, {"rule"}),
    "derives_from": ({"artifact", "rule"}, {"artifact", "rule", "source"}),
    "aliases": ({"entity"}, {"entity"}),
    "references": ({"artifact", "rule"}, NODE_TYPES),
    "routes_to": ({"artifact"}, {"artifact"}),
}
ASSERTIONS = {"explicit", "inferred"}
STATUSES = {"candidate", "confirmed", "rejected", "unknown"}
METHODS = {
    "explicit_parse",
    "deterministic",
    "manual_analysis",
    "coreference",
    "entity_linking",
    "embedding",
    "nli",
    "llm",
}
PROBABILISTIC_METHODS = {"coreference", "entity_linking", "embedding", "nli", "llm"}
ADJUDICATIONS = {
    "not_reviewed",
    "deterministic_confirmed",
    "manual_confirmed",
    "manual_rejected",
}
ADJUDICATOR_KINDS = {"none", "human", "independent_agent", "deterministic_checker"}
MODALITIES = {"MUST", "SHOULD", "MAY", "FACT"}
POLARITIES = {"positive", "negative"}
IDENTIFIER_RE = re.compile(r"^[a-z][a-z0-9_.:-]*$")
SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
CONTRACT_FILE_RE = re.compile(r"^[a-z][a-z0-9-]*\.md$")
ROOT_INSTRUCTION_FILE_RE = re.compile(r"^[A-Z][A-Z0-9_-]*\.md$")
ARTIFACT_KINDS = {"root_instruction", "contract", "archive", "supporting"}
CANONICAL_ROOT_FILES = {
    "agents.md": "AGENTS.md",
    "docs.md": "DOCS.md",
    "model.md": "MODEL.md",
    "workflow.md": "WORKFLOW.md",
}
TOPOLOGY_CONFIG_CANDIDATES = (
    ".codex/final-topology.json",
    ".codex/instruction-topology.json",
    "final-topology.json",
)
RESOLVABLE_FINDINGS = {"normalized-rule-conflict", "normalized-modality-drift"}
RESOLUTION_DECISIONS = {"rejected", "resolved"}


def default_topology_config() -> dict[str, Any]:
    """Return the historical AGENTS -> DOCS -> MODEL/WORKFLOW policy."""
    root_paths = list(CANONICAL_ROOT_FILES.values())
    return {
        "name": "default AGENTS.md -> DOCS.md -> MODEL.md/WORKFLOW.md",
        "allowed_root_nodes": root_paths,
        "required_root_nodes": root_paths,
        "required_edges": [
            {"parent": "AGENTS.md", "child": "DOCS.md"},
            {"parent": "DOCS.md", "child": "MODEL.md"},
            {"parent": "DOCS.md", "child": "WORKFLOW.md"},
        ],
        "parent_rules": {
            "AGENTS.md": "none",
            "DOCS.md": "exactly_one",
            "MODEL.md": "exactly_one",
            "WORKFLOW.md": "exactly_one",
        },
        "allow_additional_peer_routes": False,
        "forbidden_edges": [],
    }


class GraphError(ValueError):
    pass


def reject_constant(value: str) -> None:
    raise GraphError(f"Недопустимая JSON-константа: {value}.")


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"), parse_constant=reject_constant)
    except FileNotFoundError as exc:
        raise GraphError(f"Файл не найден: {path}") from exc
    except json.JSONDecodeError as exc:
        raise GraphError(f"Некорректный JSON в {path}: {exc}") from exc


def exact_keys(value: dict[str, Any], required: set[str], allowed: set[str], label: str) -> list[str]:
    errors: list[str] = []
    missing = required - set(value)
    extra = set(value) - allowed
    if missing:
        errors.append(f"{label}: отсутствуют {', '.join(sorted(missing))}.")
    if extra:
        errors.append(f"{label}: неизвестные поля {', '.join(sorted(extra))}.")
    return errors


def valid_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def safe_relative_path(value: Any) -> bool:
    if not valid_text(value):
        return False
    path = PurePosixPath(value)
    return not path.is_absolute() and ".." not in path.parts and value == path.as_posix()


def scope_digest(units: list[dict[str, str]]) -> str:
    normalized = sorted(
        ({"path": item["path"], "content_sha256": item["content_sha256"]} for item in units),
        key=lambda item: item["path"],
    )
    payload = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def validate_evidence(
    value: Any,
    label: str,
    unit_paths: set[str],
    root: Path | None,
) -> tuple[list[str], list[dict[str, Any]]]:
    errors: list[str] = []
    if not isinstance(value, list) or not value:
        return [f"{label} должен быть непустым массивом."], []
    result: list[dict[str, Any]] = []
    signatures: set[tuple[str, str]] = set()
    for index, item in enumerate(value):
        item_label = f"{label}[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{item_label} должен быть объектом.")
            continue
        errors.extend(
            exact_keys(
                item,
                {"path", "locator", "note"},
                {
                    "path", "locator", "note", "start_byte", "end_byte",
                    "evidence_sha256"
                },
                item_label,
            )
        )
        if not all(valid_text(item.get(key)) for key in ("path", "locator", "note")):
            errors.append(f"{item_label} требует path, locator и note.")
            continue
        if item["path"] not in unit_paths:
            errors.append(f"{item_label}.path не входит в scope.units.")
        bound_fields = {"start_byte", "end_byte", "evidence_sha256"}
        present_bound_fields = bound_fields & set(item)
        if present_bound_fields and present_bound_fields != bound_fields:
            errors.append(
                f"{item_label}: start_byte/end_byte/evidence_sha256 задаются вместе."
            )
        evidence_sha256 = item.get("evidence_sha256")
        start_byte = item.get("start_byte")
        end_byte = item.get("end_byte")
        if present_bound_fields == bound_fields:
            if (
                not isinstance(start_byte, int)
                or isinstance(start_byte, bool)
                or not isinstance(end_byte, int)
                or isinstance(end_byte, bool)
                or start_byte < 0
                or end_byte <= start_byte
                or not isinstance(evidence_sha256, str)
                or not SHA256_RE.fullmatch(evidence_sha256)
            ):
                errors.append(f"{item_label}: byte-bound evidence некорректен.")
            elif root is None:
                errors.append(f"{item_label}: byte-bound evidence требует --root.")
            else:
                try:
                    resolved_root = root.expanduser().resolve(strict=True)
                    evidence_path = (resolved_root / item["path"]).resolve(strict=True)
                    evidence_path.relative_to(resolved_root)
                    data = evidence_path.read_bytes()
                    if end_byte > len(data):
                        errors.append(f"{item_label}: byte range выходит за файл.")
                    elif hashlib.sha256(data[start_byte:end_byte]).hexdigest() != evidence_sha256:
                        errors.append(f"{item_label}: evidence_sha256 не совпадает с byte range.")
                except (FileNotFoundError, OSError, ValueError):
                    errors.append(f"{item_label}: byte range нельзя проверить внутри --root.")
        signature = (item["path"].casefold(), item["locator"].casefold())
        if signature in signatures:
            errors.append(f"{item_label} дублирует path + locator.")
        signatures.add(signature)
        normalized = {"path": item["path"], "locator": item["locator"], "note": item["note"]}
        if present_bound_fields == bound_fields:
            normalized.update(
                {
                    "start_byte": start_byte,
                    "end_byte": end_byte,
                    "evidence_sha256": evidence_sha256,
                }
            )
        result.append(normalized)
    return errors, result


def validate_node_provenance(value: Any, label: str, producer: Any) -> list[str]:
    errors: list[str] = []
    required = {"assertion", "status", "method", "adjudication", "adjudicator"}
    if not isinstance(value, dict):
        return [f"{label} должен быть объектом."]
    errors.extend(exact_keys(value, required, required, label))
    assertion = value.get("assertion")
    status = value.get("status")
    method = value.get("method")
    adjudication = value.get("adjudication")
    if assertion not in ASSERTIONS:
        errors.append(f"{label}.assertion неизвестен.")
    if status not in STATUSES:
        errors.append(f"{label}.status неизвестен.")
    if method not in METHODS:
        errors.append(f"{label}.method неизвестен.")
    if adjudication not in ADJUDICATIONS:
        errors.append(f"{label}.adjudication неизвестен.")
    adjudicator = value.get("adjudicator")
    adjudicator_kind = None
    if not isinstance(adjudicator, dict):
        errors.append(f"{label}.adjudicator должен быть объектом.")
    else:
        errors.extend(
            exact_keys(
                adjudicator,
                {"kind", "id", "note"},
                {"kind", "id", "note"},
                f"{label}.adjudicator",
            )
        )
        adjudicator_kind = adjudicator.get("kind")
        if adjudicator_kind not in ADJUDICATOR_KINDS:
            errors.append(f"{label}.adjudicator.kind неизвестен.")
        if not valid_text(adjudicator.get("id")) or not valid_text(adjudicator.get("note")):
            errors.append(f"{label}.adjudicator требует непустые id и note.")
    if status in {"candidate", "unknown"}:
        if adjudication != "not_reviewed" or adjudicator_kind != "none":
            errors.append(f"{label}: candidate/unknown требует not_reviewed и adjudicator.kind=none.")
    if status == "rejected" and adjudication != "manual_rejected":
        errors.append(f"{label}: rejected требует manual_rejected.")
    if status == "confirmed" and adjudication not in {
        "deterministic_confirmed", "manual_confirmed"
    }:
        errors.append(f"{label}: confirmed требует подтверждающую adjudication.")
    if assertion == "inferred" and status == "confirmed":
        errors.append(f"{label}: inferred node не может быть confirmed.")
    if adjudication in {"manual_confirmed", "manual_rejected"} and adjudicator_kind not in {
        "human", "independent_agent"
    }:
        errors.append(f"{label}: manual adjudication требует human/independent_agent.")
    if adjudication == "deterministic_confirmed" and adjudicator_kind != "deterministic_checker":
        errors.append(f"{label}: deterministic_confirmed требует deterministic_checker.")
    if adjudication == "deterministic_confirmed" and (
        assertion != "explicit" or method not in {"explicit_parse", "deterministic"}
    ):
        errors.append(f"{label}: deterministic_confirmed требует explicit deterministic method.")
    if producer == "model_assisted" and status not in {"candidate", "unknown"}:
        errors.append(f"{label}: model_assisted graph допускает только candidate/unknown nodes.")
    if producer == "deterministic" and (
        assertion != "explicit"
        or method not in {"explicit_parse", "deterministic"}
        or adjudication not in {"not_reviewed", "deterministic_confirmed"}
    ):
        errors.append(f"{label}: deterministic producer требует explicit deterministic node.")
    if method in PROBABILISTIC_METHODS and assertion != "inferred":
        errors.append(f"{label}: probabilistic method требует assertion=inferred.")
    return errors


def validate_files(root: Path, units: list[dict[str, str]]) -> list[str]:
    errors: list[str] = []
    lexical_root = root.expanduser().absolute()
    if any(component.is_symlink() for component in (lexical_root, *lexical_root.parents)):
        return ["--root должен быть существующим каталогом без symlink-компонентов."]
    try:
        resolved_root = lexical_root.resolve(strict=True)
    except FileNotFoundError:
        return ["--root должен быть существующим каталогом, не symlink."]
    if not resolved_root.is_dir():
        return ["--root должен быть существующим каталогом, не symlink."]
    for unit in units:
        candidate = resolved_root / unit["path"]
        current = candidate
        while current != resolved_root:
            if current.is_symlink():
                errors.append(f"Scope unit проходит через symlink: {unit['path']}.")
                break
            current = current.parent
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(resolved_root)
        except (FileNotFoundError, ValueError):
            errors.append(f"Scope unit отсутствует или выходит за --root: {unit['path']}.")
            continue
        if not resolved.is_file():
            errors.append(f"Scope unit не является файлом: {unit['path']}.")
            continue
        actual = hashlib.sha256(resolved.read_bytes()).hexdigest()
        if actual != unit["content_sha256"]:
            errors.append(f"Content SHA-256 не совпадает: {unit['path']}.")
    return errors


def find_cycle(edges: list[tuple[str, str]]) -> list[str] | None:
    adjacency: dict[str, list[str]] = defaultdict(list)
    for source, target in edges:
        adjacency[source].append(target)
    state: dict[str, int] = {}
    stack: list[str] = []

    def visit(node: str) -> list[str] | None:
        state[node] = 1
        stack.append(node)
        for target in adjacency[node]:
            if state.get(target, 0) == 0:
                cycle = visit(target)
                if cycle:
                    return cycle
            elif state.get(target) == 1:
                start = stack.index(target)
                return stack[start:] + [target]
        stack.pop()
        state[node] = 2
        return None

    for node in sorted(adjacency):
        if state.get(node, 0) == 0:
            cycle = visit(node)
            if cycle:
                return cycle
    return None


def normalize_claim(claim: dict[str, Any], include_polarity: bool, include_modality: bool) -> tuple[str, ...]:
    fields = ["subject", "action", "object", "condition", "exception"]
    values = [str(claim.get(field) or "").casefold().strip() for field in fields]
    if include_polarity:
        values.append(str(claim.get("polarity") or ""))
    if include_modality:
        values.append(str(claim.get("modality") or ""))
    return tuple(values)


def candidate_fingerprint(code: str, node_ids: list[str], graph: dict[str, Any]) -> str:
    selected = set(node_ids)
    graph_nodes = {node["id"]: node for node in graph["nodes"]}
    incident_edges = [
        edge
        for edge in graph["edges"]
        if edge.get("from") in selected or edge.get("to") in selected
    ]
    context_node_ids = selected | {
        endpoint
        for edge in incident_edges
        for endpoint in (edge.get("from"), edge.get("to"))
        if endpoint in graph_nodes
    }
    relevant_evidence = [graph_nodes[node_id]["source"] for node_id in context_node_ids]
    relevant_evidence.extend(
        evidence for edge in incident_edges for evidence in edge.get("evidence", [])
    )
    unbound_paths = {
        evidence["path"]
        for evidence in relevant_evidence
        if "evidence_sha256" not in evidence
    }
    payload = {
        "code": code,
        "nodes": [graph_nodes[node_id] for node_id in sorted(context_node_ids)],
        "edges": sorted(incident_edges, key=lambda edge: edge["id"]),
        "evidence_hashes": sorted(
            {
                (
                    evidence["path"], evidence["locator"], evidence["start_byte"],
                    evidence["end_byte"], evidence["evidence_sha256"]
                )
                for evidence in relevant_evidence
                if "evidence_sha256" in evidence
            }
        ),
        "units": sorted(
            [unit for unit in graph["scope"]["units"] if unit["path"] in unbound_paths],
            key=lambda unit: unit["path"],
        ),
    }
    normalized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def resolution_has_complete_override(
    code: str,
    group: list[tuple[str, dict[str, Any]]],
    edges: list[dict[str, Any]],
) -> bool:
    field = "polarity" if code == "normalized-rule-conflict" else "modality"
    required_pairs = {
        tuple(sorted((left_id, right_id)))
        for index, (left_id, left_claim) in enumerate(group)
        for right_id, right_claim in group[index + 1 :]
        if left_claim[field] != right_claim[field]
    }
    confirmed_pairs = {
        tuple(sorted((edge["from"], edge["to"])))
        for edge in edges
        if edge.get("type") == "overrides" and edge.get("status") == "confirmed"
    }
    return bool(required_pairs) and required_pairs <= confirmed_pairs


def normalize_entity_name(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold().strip()


def add_finding(
    findings: list[dict[str, Any]],
    code: str,
    severity: str,
    status: str,
    message: str,
    nodes: list[str],
    fingerprint: str | None = None,
) -> None:
    finding = {
        "code": code,
        "severity": severity,
        "status": status,
        "message": message,
        "nodes": nodes,
    }
    if fingerprint is not None:
        finding["candidate_fingerprint"] = fingerprint
    findings.append(finding)


def validate_schema_contract(schema: dict[str, Any]) -> None:
    errors: list[str] = []
    properties = schema.get("properties", {})
    if set(schema.get("required", [])) != {"schema_version", "mode", "producer", "scope", "nodes", "edges"}:
        errors.append("root required fields")
    node_schema = properties.get("nodes", {}).get("items", {})
    node_properties = node_schema.get("properties", {})
    if set(properties.get("mode", {}).get("enum", [])) != {"explicit", "semantic"}:
        errors.append("mode enum")
    if set(properties.get("producer", {}).get("enum", [])) != {
        "manual", "deterministic", "model_assisted"
    }:
        errors.append("producer enum")
    if set(node_schema.get("required", [])) != {"id", "type", "label", "source"}:
        errors.append("node required fields")
    if set(node_properties.get("type", {}).get("enum", [])) != NODE_TYPES:
        errors.append("node types")
    if set(node_properties.get("artifact_kind", {}).get("enum", [])) != ARTIFACT_KINDS:
        errors.append("artifact kinds")
    if node_properties.get("id", {}).get("pattern") != IDENTIFIER_RE.pattern:
        errors.append("node id pattern")
    claim_properties = node_properties.get("claim", {}).get("properties", {})
    if set(claim_properties.get("modality", {}).get("enum", [])) != MODALITIES:
        errors.append("claim modalities")
    if set(claim_properties.get("polarity", {}).get("enum", [])) != POLARITIES:
        errors.append("claim polarities")
    if node_properties.get("aliases", {}).get("uniqueItems") is not True:
        errors.append("aliases.uniqueItems")
    provenance_schema = node_properties.get("provenance", {})
    provenance_properties = provenance_schema.get("properties", {})
    expected_provenance_fields = {
        "assertion", "status", "method", "adjudication", "adjudicator"
    }
    if set(provenance_schema.get("required", [])) != expected_provenance_fields:
        errors.append("node provenance fields")
    for field, expected in {
        "assertion": ASSERTIONS,
        "status": STATUSES,
        "method": METHODS,
        "adjudication": ADJUDICATIONS,
    }.items():
        if set(provenance_properties.get(field, {}).get("enum", [])) != expected:
            errors.append(f"node provenance {field}")
    provenance_adjudicator = provenance_properties.get("adjudicator", {})
    if set(provenance_adjudicator.get("required", [])) != {"kind", "id", "note"}:
        errors.append("node provenance adjudicator fields")
    if set(
        provenance_adjudicator.get("properties", {}).get("kind", {}).get("enum", [])
    ) != ADJUDICATOR_KINDS:
        errors.append("node provenance adjudicator kinds")
    edge_schema = properties.get("edges", {}).get("items", {})
    edge_properties = edge_schema.get("properties", {})
    expected_edge_fields = {
        "id", "type", "from", "to", "assertion", "status", "method", "adjudication",
        "adjudicator", "evidence"
    }
    if set(edge_schema.get("required", [])) != expected_edge_fields:
        errors.append("edge required fields")
    if edge_properties.get("id", {}).get("pattern") != IDENTIFIER_RE.pattern:
        errors.append("edge id pattern")
    expected_enums = {
        "type": EDGE_TYPES,
        "assertion": ASSERTIONS,
        "status": STATUSES,
        "method": METHODS,
        "adjudication": ADJUDICATIONS,
    }
    for field, expected in expected_enums.items():
        if set(edge_properties.get(field, {}).get("enum", [])) != expected:
            errors.append(f"edge {field}")
    adjudicator_schema = edge_properties.get("adjudicator", {})
    if set(adjudicator_schema.get("required", [])) != {"kind", "id", "note"}:
        errors.append("edge adjudicator fields")
    if set(adjudicator_schema.get("properties", {}).get("kind", {}).get("enum", [])) != ADJUDICATOR_KINDS:
        errors.append("edge adjudicator kinds")
    resolution_schema = properties.get("finding_resolutions", {}).get("items", {})
    resolution_properties = resolution_schema.get("properties", {})
    expected_resolution_fields = {
        "id", "finding_code", "nodes", "candidate_fingerprint", "decision", "method",
        "adjudicator", "evidence"
    }
    if set(resolution_schema.get("required", [])) != expected_resolution_fields:
        errors.append("finding resolution fields")
    if set(resolution_properties.get("finding_code", {}).get("enum", [])) != RESOLVABLE_FINDINGS:
        errors.append("finding resolution codes")
    if set(resolution_properties.get("decision", {}).get("enum", [])) != RESOLUTION_DECISIONS:
        errors.append("finding resolution decisions")
    if resolution_properties.get("method", {}).get("const") != "manual_analysis":
        errors.append("finding resolution method")
    if resolution_properties.get("candidate_fingerprint", {}).get("pattern") != SHA256_RE.pattern:
        errors.append("finding resolution fingerprint")
    resolution_adjudicator = resolution_properties.get("adjudicator", {})
    if set(resolution_adjudicator.get("required", [])) != {"kind", "id", "note"}:
        errors.append("finding resolution adjudicator fields")
    if set(resolution_adjudicator.get("properties", {}).get("kind", {}).get("enum", [])) != {
        "human", "independent_agent"
    }:
        errors.append("finding resolution adjudicator kinds")
    evidence_schema = schema.get("$defs", {}).get("evidence", {})
    if set(evidence_schema.get("required", [])) != {"path", "locator", "note"}:
        errors.append("evidence required fields")
    if evidence_schema.get("properties", {}).get("evidence_sha256", {}).get("pattern") != SHA256_RE.pattern:
        errors.append("evidence sha256")
    expected_bound_dependencies = {
        "start_byte": {"end_byte", "evidence_sha256"},
        "end_byte": {"start_byte", "evidence_sha256"},
        "evidence_sha256": {"start_byte", "end_byte"},
    }
    actual_bound_dependencies = {
        field: set(required)
        for field, required in evidence_schema.get("dependentRequired", {}).items()
    }
    if actual_bound_dependencies != expected_bound_dependencies:
        errors.append("evidence byte-bound dependencies")
    if errors:
        raise GraphError("instruction-graph.schema.json не согласован с checker: " + ", ".join(errors) + ".")


def validate_and_check(graph: dict[str, Any], root: Path | None) -> dict[str, Any]:
    errors = exact_keys(
        graph,
        {"schema_version", "mode", "producer", "scope", "nodes", "edges"},
        {
            "schema_version", "mode", "producer", "scope", "nodes", "edges",
            "finding_resolutions"
        },
        "graph",
    )
    if graph.get("schema_version") != "1.0":
        errors.append("schema_version должен быть 1.0.")
    if not isinstance(graph.get("mode"), str) or graph.get("mode") not in {"explicit", "semantic"}:
        errors.append("mode должен быть explicit или semantic.")
    if not isinstance(graph.get("producer"), str) or graph.get("producer") not in {
        "manual", "deterministic", "model_assisted"
    }:
        errors.append("producer неизвестен.")

    scope = graph.get("scope")
    units: list[dict[str, str]] = []
    unit_paths: set[str] = set()
    if not isinstance(scope, dict):
        errors.append("scope должен быть объектом.")
        scope = {}
    else:
        errors.extend(exact_keys(scope, {"digest", "units"}, {"digest", "units"}, "scope"))
        raw_units = scope.get("units")
        if not isinstance(raw_units, list) or not raw_units:
            errors.append("scope.units должен быть непустым массивом.")
        else:
            for index, unit in enumerate(raw_units):
                label = f"scope.units[{index}]"
                if not isinstance(unit, dict):
                    errors.append(f"{label} должен быть объектом.")
                    continue
                errors.extend(exact_keys(unit, {"path", "content_sha256"}, {"path", "content_sha256"}, label))
                path = unit.get("path")
                content_sha256 = unit.get("content_sha256")
                if not safe_relative_path(path):
                    errors.append(f"{label}.path должен быть безопасным относительным POSIX-путём.")
                    continue
                if path in unit_paths:
                    errors.append(f"Дублируется scope unit: {path}.")
                unit_paths.add(path)
                if not isinstance(content_sha256, str) or not SHA256_RE.fullmatch(content_sha256):
                    errors.append(f"{label}.content_sha256 некорректен.")
                    continue
                units.append({"path": path, "content_sha256": content_sha256})
        if units:
            calculated = scope_digest(units)
            if scope.get("digest") != calculated:
                errors.append("scope.digest не соответствует отсортированным units.")
            if root is not None:
                errors.extend(validate_files(root, units))

    raw_nodes = graph.get("nodes")
    nodes: dict[str, dict[str, Any]] = {}
    if not isinstance(raw_nodes, list):
        errors.append("nodes должен быть массивом.")
        raw_nodes = []
    for index, node in enumerate(raw_nodes):
        label = f"nodes[{index}]"
        if not isinstance(node, dict):
            errors.append(f"{label} должен быть объектом.")
            continue
        allowed = {
            "id", "type", "artifact_kind", "label", "canonical_name", "aliases",
            "authority", "claim", "source", "provenance"
        }
        errors.extend(exact_keys(node, {"id", "type", "label", "source"}, allowed, label))
        node_id = node.get("id")
        node_type = node.get("type")
        if not valid_text(node_id) or not IDENTIFIER_RE.fullmatch(node_id):
            errors.append(f"{label}.id некорректен.")
            continue
        if node_id in nodes:
            errors.append(f"Дублируется node id: {node_id}.")
        nodes[node_id] = node
        if not isinstance(node_type, str) or node_type not in NODE_TYPES:
            errors.append(f"{label}.type неизвестен.")
        if isinstance(node_type, str) and node_type in {"artifact", "source"}:
            if not isinstance(node.get("authority"), bool):
                errors.append(f"{label}.authority обязателен для artifact/source.")
        elif "authority" in node:
            errors.append(f"{label}.authority разрешён только для artifact/source.")
        artifact_kind = node.get("artifact_kind")
        if node_type == "artifact":
            if artifact_kind not in ARTIFACT_KINDS:
                errors.append(f"{label}.artifact_kind обязателен для artifact.")
            elif artifact_kind in {"root_instruction", "contract"} and node.get("authority") is not True:
                errors.append(f"{label}: root_instruction/contract требует authority=true.")
            elif artifact_kind == "archive" and node.get("authority") is not False:
                errors.append(f"{label}: archive требует authority=false.")
        elif "artifact_kind" in node:
            errors.append(f"{label}.artifact_kind разрешён только для artifact.")
        if not valid_text(node.get("label")):
            errors.append(f"{label}.label обязателен.")
        evidence_errors, _ = validate_evidence(
            [node.get("source")], f"{label}.source", unit_paths, root
        )
        errors.extend(evidence_errors)
        if node_type == "entity":
            errors.extend(
                validate_node_provenance(
                    node.get("provenance"), f"{label}.provenance", graph.get("producer")
                )
            )
            if not valid_text(node.get("canonical_name")):
                errors.append(f"{label}.canonical_name обязателен для entity.")
            aliases = node.get("aliases", [])
            if not isinstance(aliases, list) or not all(valid_text(item) for item in aliases):
                errors.append(f"{label}.aliases должен быть массивом строк.")
            elif len({normalize_entity_name(item) for item in aliases}) != len(aliases):
                errors.append(f"{label}.aliases содержит дубликаты без учёта регистра.")
        elif "canonical_name" in node or "aliases" in node:
            errors.append(f"{label}: canonical_name/aliases разрешены только entity.")
        if node_type == "rule":
            errors.extend(
                validate_node_provenance(
                    node.get("provenance"), f"{label}.provenance", graph.get("producer")
                )
            )
            claim = node.get("claim")
            required = {"subject", "modality", "polarity", "action", "object", "condition", "exception"}
            if not isinstance(claim, dict):
                errors.append(f"{label}.claim обязателен для rule.")
            else:
                errors.extend(exact_keys(claim, required, required, f"{label}.claim"))
                if not all(valid_text(claim.get(key)) for key in ("subject", "action", "object")):
                    errors.append(f"{label}.claim требует subject, action и object.")
                if (
                    not isinstance(claim.get("modality"), str)
                    or claim.get("modality") not in MODALITIES
                    or not isinstance(claim.get("polarity"), str)
                    or claim.get("polarity") not in POLARITIES
                ):
                    errors.append(f"{label}.claim modality/polarity неизвестны.")
                if claim.get("condition") is not None and not isinstance(claim.get("condition"), str):
                    errors.append(f"{label}.claim.condition должен быть строкой или null.")
                if claim.get("exception") is not None and not isinstance(claim.get("exception"), str):
                    errors.append(f"{label}.claim.exception должен быть строкой или null.")
        elif "claim" in node:
            errors.append(f"{label}.claim разрешён только rule.")

    raw_edges = graph.get("edges")
    edges: list[dict[str, Any]] = []
    edge_ids: set[str] = set()
    if not isinstance(raw_edges, list):
        errors.append("edges должен быть массивом.")
        raw_edges = []
    for index, edge in enumerate(raw_edges):
        label = f"edges[{index}]"
        if not isinstance(edge, dict):
            errors.append(f"{label} должен быть объектом.")
            continue
        required = {
            "id", "type", "from", "to", "assertion", "status", "method", "adjudication",
            "adjudicator", "evidence"
        }
        errors.extend(exact_keys(edge, required, required, label))
        edge_id = edge.get("id")
        if not valid_text(edge_id) or not IDENTIFIER_RE.fullmatch(edge_id):
            errors.append(f"{label}.id некорректен.")
        elif edge_id in edge_ids:
            errors.append(f"Дублируется edge id: {edge_id}.")
        else:
            edge_ids.add(edge_id)
        if not isinstance(edge.get("type"), str) or edge.get("type") not in EDGE_TYPES:
            errors.append(f"{label}.type неизвестен.")
        from_id = edge.get("from")
        to_id = edge.get("to")
        if not valid_text(from_id) or not valid_text(to_id):
            errors.append(f"{label}.from/to должны быть строковыми node ids.")
        elif from_id not in nodes or to_id not in nodes:
            errors.append(f"{label} содержит dangling from/to.")
        elif isinstance(edge.get("type"), str) and edge.get("type") in EDGE_ENDPOINT_TYPES:
            allowed_from, allowed_to = EDGE_ENDPOINT_TYPES[edge["type"]]
            from_type = nodes[edge["from"]].get("type")
            to_type = nodes[edge["to"]].get("type")
            if from_type is not None and to_type is not None and (
                from_type not in allowed_from or to_type not in allowed_to
            ):
                errors.append(
                    f"{label}: {edge['type']} не допускает {from_type} -> {to_type}."
                )
            if edge["from"] == edge["to"] and edge["type"] != "references":
                errors.append(f"{label}: self-edge допустим только для references.")
            if edge["type"] in {"owns", "precedes"}:
                authority_nodes = [nodes[edge["from"]]]
                if edge["type"] == "precedes":
                    authority_nodes.append(nodes[edge["to"]])
                if not all(node.get("authority") is True for node in authority_nodes):
                    errors.append(f"{label}: {edge['type']} требует authority=true.")
            if edge["type"] == "routes_to":
                route_nodes = [nodes[edge["from"]], nodes[edge["to"]]]
                if not all(
                    node.get("authority") is True
                    and node.get("artifact_kind") in {"root_instruction", "contract"}
                    for node in route_nodes
                ):
                    errors.append(
                        f"{label}: routes_to требует authority root_instruction/contract artifacts."
                    )
        if (
            not isinstance(edge.get("assertion"), str)
            or edge.get("assertion") not in ASSERTIONS
            or not isinstance(edge.get("status"), str)
            or edge.get("status") not in STATUSES
        ):
            errors.append(f"{label} содержит неизвестный assertion/status.")
        method = edge.get("method")
        adjudication = edge.get("adjudication")
        adjudicator = edge.get("adjudicator")
        adjudicator_kind = None
        if not isinstance(adjudicator, dict):
            errors.append(f"{label}.adjudicator должен быть объектом.")
        else:
            errors.extend(
                exact_keys(adjudicator, {"kind", "id", "note"}, {"kind", "id", "note"}, f"{label}.adjudicator")
            )
            adjudicator_kind = adjudicator.get("kind")
            if not isinstance(adjudicator_kind, str) or adjudicator_kind not in ADJUDICATOR_KINDS:
                errors.append(f"{label}.adjudicator.kind неизвестен.")
            if not valid_text(adjudicator.get("id")) or not valid_text(adjudicator.get("note")):
                errors.append(f"{label}.adjudicator требует непустые id и note.")
        if (
            not isinstance(method, str)
            or method not in METHODS
            or not isinstance(adjudication, str)
            or adjudication not in ADJUDICATIONS
        ):
            errors.append(f"{label} содержит неизвестный method/adjudication.")
        else:
            status = edge.get("status")
            assertion = edge.get("assertion")
            status_is_known = isinstance(status, str) and status in STATUSES
            if status_is_known and status in {"candidate", "unknown"} and adjudication != "not_reviewed":
                errors.append(f"{label}: candidate/unknown требует not_reviewed.")
            if status_is_known and status in {"candidate", "unknown"} and adjudicator_kind != "none":
                errors.append(f"{label}: candidate/unknown требует adjudicator.kind=none.")
            if status == "rejected" and adjudication != "manual_rejected":
                errors.append(f"{label}: rejected требует manual_rejected.")
            if status == "confirmed" and adjudication not in {
                "deterministic_confirmed", "manual_confirmed"
            }:
                errors.append(f"{label}: confirmed требует подтверждающую adjudication.")
            if assertion == "inferred" and status == "confirmed":
                errors.append(
                    f"{label}: inferred edge не может быть confirmed; создайте новое explicit manual edge."
                )
            if adjudication in {"manual_confirmed", "manual_rejected"} and adjudicator_kind not in {
                "human", "independent_agent"
            }:
                errors.append(f"{label}: manual adjudication требует human/independent_agent.")
            if adjudication == "deterministic_confirmed" and adjudicator_kind != "deterministic_checker":
                errors.append(f"{label}: deterministic_confirmed требует deterministic_checker.")
            if adjudication == "deterministic_confirmed" and (
                assertion != "explicit" or method not in {"explicit_parse", "deterministic"}
            ):
                errors.append(f"{label}: deterministic_confirmed требует explicit deterministic method.")
            if graph.get("producer") == "model_assisted" and status not in {"candidate", "unknown"}:
                errors.append(f"{label}: model_assisted graph допускает только candidate/unknown.")
            if graph.get("producer") == "deterministic" and (
                assertion != "explicit"
                or method not in {"explicit_parse", "deterministic"}
                or adjudication not in {"not_reviewed", "deterministic_confirmed"}
            ):
                errors.append(f"{label}: deterministic producer требует explicit deterministic edge.")
            if method in PROBABILISTIC_METHODS and assertion != "inferred":
                errors.append(f"{label}: probabilistic method требует assertion=inferred.")
        evidence_errors, normalized = validate_evidence(
            edge.get("evidence"), f"{label}.evidence", unit_paths, root
        )
        errors.extend(evidence_errors)
        copied = dict(edge)
        copied["evidence"] = normalized
        edges.append(copied)

    finding_resolutions: dict[tuple[str, tuple[str, ...]], dict[str, Any]] = {}
    resolution_ids: set[str] = set()
    raw_resolutions = graph.get("finding_resolutions", [])
    if not isinstance(raw_resolutions, list):
        errors.append("finding_resolutions должен быть массивом.")
        raw_resolutions = []
    if raw_resolutions and graph.get("producer") != "manual":
        errors.append("finding_resolutions допустимы только в manual graph.")
    for index, resolution in enumerate(raw_resolutions):
        label = f"finding_resolutions[{index}]"
        required = {
            "id", "finding_code", "nodes", "candidate_fingerprint", "decision", "method",
            "adjudicator", "evidence"
        }
        if not isinstance(resolution, dict):
            errors.append(f"{label} должен быть объектом.")
            continue
        errors.extend(exact_keys(resolution, required, required, label))
        resolution_id = resolution.get("id")
        if not valid_text(resolution_id) or not IDENTIFIER_RE.fullmatch(resolution_id):
            errors.append(f"{label}.id некорректен.")
        elif resolution_id in resolution_ids:
            errors.append(f"Дублируется finding resolution id: {resolution_id}.")
        else:
            resolution_ids.add(resolution_id)
        finding_code = resolution.get("finding_code")
        if finding_code not in RESOLVABLE_FINDINGS:
            errors.append(f"{label}.finding_code неизвестен.")
        decision = resolution.get("decision")
        if decision not in RESOLUTION_DECISIONS:
            errors.append(f"{label}.decision неизвестен.")
        fingerprint = resolution.get("candidate_fingerprint")
        if not isinstance(fingerprint, str) or not SHA256_RE.fullmatch(fingerprint):
            errors.append(f"{label}.candidate_fingerprint некорректен.")
        if resolution.get("method") != "manual_analysis":
            errors.append(f"{label}.method должен быть manual_analysis.")
        resolution_nodes = resolution.get("nodes")
        normalized_nodes: tuple[str, ...] = ()
        if (
            not isinstance(resolution_nodes, list)
            or not resolution_nodes
            or not all(valid_text(item) and IDENTIFIER_RE.fullmatch(item) for item in resolution_nodes)
            or len(set(resolution_nodes)) != len(resolution_nodes)
        ):
            errors.append(f"{label}.nodes должен быть непустым уникальным массивом node ids.")
        else:
            normalized_nodes = tuple(sorted(resolution_nodes))
            missing_nodes = set(normalized_nodes) - set(nodes)
            if missing_nodes:
                errors.append(f"{label}.nodes содержит неизвестные ids: {', '.join(sorted(missing_nodes))}.")
        adjudicator = resolution.get("adjudicator")
        if not isinstance(adjudicator, dict):
            errors.append(f"{label}.adjudicator должен быть объектом.")
        else:
            errors.extend(
                exact_keys(
                    adjudicator,
                    {"kind", "id", "note"},
                    {"kind", "id", "note"},
                    f"{label}.adjudicator",
                )
            )
            if adjudicator.get("kind") not in {"human", "independent_agent"}:
                errors.append(f"{label}.adjudicator.kind требует human/independent_agent.")
            if not valid_text(adjudicator.get("id")) or not valid_text(adjudicator.get("note")):
                errors.append(f"{label}.adjudicator требует непустые id и note.")
        evidence_errors, normalized_evidence = validate_evidence(
            resolution.get("evidence"), f"{label}.evidence", unit_paths, root
        )
        errors.extend(evidence_errors)
        if finding_code in RESOLVABLE_FINDINGS and normalized_nodes:
            signature = (finding_code, normalized_nodes)
            if signature in finding_resolutions:
                errors.append(f"{label} дублирует resolution для finding + nodes.")
            copied = dict(resolution)
            copied["nodes"] = list(normalized_nodes)
            copied["evidence"] = normalized_evidence
            finding_resolutions[signature] = copied

    if errors:
        raise GraphError("\n".join(f"- {error}" for error in errors))

    findings: list[dict[str, Any]] = []
    artifact_ids_by_path: dict[str, list[str]] = defaultdict(list)
    for node_id, node in nodes.items():
        if node.get("type") == "artifact":
            artifact_ids_by_path[node["source"]["path"]].append(node_id)
    for path, artifact_ids in sorted(artifact_ids_by_path.items()):
        if len(artifact_ids) > 1:
            add_finding(
                findings,
                "duplicate-artifact-path",
                "error",
                "confirmed",
                f"Один scope path представлен несколькими artifact nodes: {path}.",
                sorted(artifact_ids),
            )
    for unit_path in sorted(unit_paths):
        pure_path = PurePosixPath(unit_path)
        expected_root = CANONICAL_ROOT_FILES.get(pure_path.name.casefold())
        if expected_root is not None and unit_path != expected_root:
            add_finding(
                findings,
                "noncanonical-root-instruction",
                "error",
                "confirmed",
                f"Верхнеуровневая инструкция должна находиться в корне как {expected_root}.",
                [],
            )
        if pure_path.parts and pure_path.parts[0].casefold() == "contracts" and (
            pure_path.parts[0] != "contracts"
            or len(pure_path.parts) != 2
            or not CONTRACT_FILE_RE.fullmatch(pure_path.name)
        ):
            add_finding(
                findings,
                "noncanonical-contract-name",
                "error",
                "confirmed",
                "Контракт должен иметь путь contracts/<lowercase-kebab>.md.",
                [],
            )
    for node_id, node in nodes.items():
        if node.get("type") != "artifact":
            continue
        artifact_path = PurePosixPath(node["source"]["path"])
        expected_root = CANONICAL_ROOT_FILES.get(artifact_path.name.casefold())
        if expected_root is not None and (
            node.get("artifact_kind") != "root_instruction"
            or artifact_path.as_posix() != expected_root
        ):
            add_finding(
                findings,
                "noncanonical-root-instruction",
                "error",
                "confirmed",
                f"{artifact_path.as_posix()} должен быть root_instruction {expected_root}.",
                [node_id],
            )
        if node.get("artifact_kind") == "root_instruction" and (
            len(artifact_path.parts) != 1
            or not ROOT_INSTRUCTION_FILE_RE.fullmatch(artifact_path.name)
        ):
            add_finding(
                findings,
                "noncanonical-root-instruction",
                "error",
                "confirmed",
                "root_instruction должен находиться в корне и иметь UPPERCASE filename.",
                [node_id],
            )
        if node.get("artifact_kind") == "contract" and (
            len(artifact_path.parts) != 2
            or artifact_path.parts[0] != "contracts"
            or not CONTRACT_FILE_RE.fullmatch(artifact_path.name)
        ):
            add_finding(
                findings,
                "noncanonical-contract-name",
                "error",
                "confirmed",
                "contract artifact должен иметь путь contracts/<lowercase-kebab>.md.",
                [node_id],
            )
        if artifact_path.parts and artifact_path.parts[0] == "contracts" and (
            node.get("artifact_kind") != "contract"
        ):
            add_finding(
                findings,
                "noncanonical-contract-name",
                "error",
                "confirmed",
                "Artifact в contracts/ должен иметь artifact_kind=contract.",
                [node_id],
            )
    confirmed_edges = [edge for edge in edges if edge["status"] == "confirmed"]
    owners: dict[str, list[str]] = defaultdict(list)
    scopes: dict[str, list[str]] = defaultdict(list)
    for edge in confirmed_edges:
        if edge["type"] == "owns" and nodes[edge["to"]]["type"] == "rule":
            owners[edge["to"]].append(edge["from"])
        if edge["type"] == "applies_to" and nodes[edge["from"]]["type"] == "rule":
            scopes[edge["from"]].append(edge["to"])
    for node_id, node in nodes.items():
        if node["type"] != "rule":
            continue
        if not owners[node_id]:
            add_finding(findings, "orphan-rule", "warning", "confirmed", "У rule нет confirmed owner.", [node_id])
        elif len(owners[node_id]) > 1:
            add_finding(
                findings,
                "multiple-rule-owners",
                "error",
                "confirmed",
                "У rule несколько confirmed owners: " + ", ".join(sorted(owners[node_id])) + ".",
                [node_id] + sorted(owners[node_id]),
            )
        if not scopes[node_id]:
            add_finding(
                findings,
                "rule-without-scope",
                "warning",
                "confirmed",
                "У rule нет confirmed applies_to scope.",
                [node_id],
            )

    for edge_type, code in (
        ("precedes", "precedence-cycle"),
        ("derives_from", "derivation-cycle"),
        ("overrides", "override-cycle"),
        ("routes_to", "routing-cycle"),
    ):
        cycle = find_cycle([(edge["from"], edge["to"]) for edge in confirmed_edges if edge["type"] == edge_type])
        if cycle:
            add_finding(findings, code, "error", "confirmed", "Обнаружен цикл: " + " -> ".join(cycle), cycle)

    canonical_names: dict[str, list[str]] = defaultdict(list)
    aliases: dict[str, list[str]] = defaultdict(list)
    for node_id, node in nodes.items():
        if node["type"] != "entity":
            continue
        canonical_names[normalize_entity_name(node["canonical_name"])].append(node_id)
        for alias in node.get("aliases", []):
            aliases[normalize_entity_name(alias)].append(node_id)
    for name, ids in canonical_names.items():
        if len(ids) > 1:
            add_finding(findings, "entity-name-collision", "error", "confirmed", f"Canonical name {name!r} назначен нескольким entity.", sorted(ids))
    for alias, ids in aliases.items():
        unique = sorted(set(ids))
        if len(unique) > 1:
            add_finding(findings, "ambiguous-entity-alias", "error", "confirmed", f"Alias {alias!r} неоднозначен.", unique)
        canonical_ids = sorted(set(canonical_names.get(alias, [])) - set(unique))
        if canonical_ids:
            add_finding(
                findings,
                "alias-canonical-collision",
                "error",
                "confirmed",
                f"Alias {alias!r} совпадает с canonical name другой entity.",
                sorted(set(unique) | set(canonical_ids)),
            )

    alias_targets: dict[str, set[str]] = defaultdict(set)
    for edge in confirmed_edges:
        if edge["type"] == "aliases":
            alias_targets[edge["from"]].add(edge["to"])
    for source_id, target_ids in alias_targets.items():
        if len(target_ids) > 1:
            add_finding(
                findings,
                "ambiguous-alias-edge",
                "error",
                "confirmed",
                "Один alias entity подтверждён для нескольких canonical entity.",
                [source_id] + sorted(target_ids),
            )

    rule_groups: dict[tuple[str, ...], list[tuple[str, dict[str, Any]]]] = defaultdict(list)
    for node_id, node in nodes.items():
        if node["type"] == "rule":
            rule_groups[normalize_claim(node["claim"], include_polarity=False, include_modality=False)].append((node_id, node["claim"]))
    used_resolutions: set[tuple[str, tuple[str, ...]]] = set()
    for group in rule_groups.values():
        polarities = {claim["polarity"] for _, claim in group}
        modalities = {claim["modality"] for _, claim in group}
        ids = sorted(node_id for node_id, _ in group)
        if len(polarities) > 1:
            code = "normalized-rule-conflict"
            signature = (code, tuple(ids))
            resolution = finding_resolutions.get(signature)
            fingerprint = candidate_fingerprint(code, ids, graph)
            if resolution and resolution["candidate_fingerprint"] != fingerprint:
                raise GraphError(
                    f"finding resolution {resolution['id']} устарел: candidate_fingerprint изменился."
                )
            if resolution and resolution["decision"] == "resolved" and not resolution_has_complete_override(
                code, group, edges
            ):
                raise GraphError(
                    f"finding resolution {resolution['id']} требует confirmed overrides для каждой различающейся пары."
                )
            status = resolution["decision"] if resolution else "candidate"
            if resolution:
                used_resolutions.add(signature)
            add_finding(
                findings,
                code,
                "warning",
                status,
                "Одинаковый нормализованный frame имеет противоположную polarity."
                + (f" Resolution: {status}." if resolution else ""),
                ids,
                fingerprint,
            )
        if len(modalities) > 1:
            code = "normalized-modality-drift"
            signature = (code, tuple(ids))
            resolution = finding_resolutions.get(signature)
            fingerprint = candidate_fingerprint(code, ids, graph)
            if resolution and resolution["candidate_fingerprint"] != fingerprint:
                raise GraphError(
                    f"finding resolution {resolution['id']} устарел: candidate_fingerprint изменился."
                )
            if resolution and resolution["decision"] == "resolved" and not resolution_has_complete_override(
                code, group, edges
            ):
                raise GraphError(
                    f"finding resolution {resolution['id']} требует confirmed overrides для каждой различающейся пары."
                )
            status = resolution["decision"] if resolution else "candidate"
            if resolution:
                used_resolutions.add(signature)
            add_finding(
                findings,
                code,
                "warning",
                status,
                "Одинаковый нормализованный frame имеет разную modality."
                + (f" Resolution: {status}." if resolution else ""),
                ids,
                fingerprint,
            )

    stale_resolutions = set(finding_resolutions) - used_resolutions
    if stale_resolutions:
        rendered = ", ".join(
            f"{code}({','.join(nodes_)})" for code, nodes_ in sorted(stale_resolutions)
        )
        raise GraphError(f"finding_resolutions не соответствует candidate findings: {rendered}.")

    findings.sort(key=lambda item: (item["status"] != "confirmed", item["severity"] != "error", item["code"], item["nodes"]))
    return {
        "schema_version": "1.0",
        "scope_digest": graph["scope"]["digest"],
        "metrics": {
            "units": len(units),
            "nodes": len(nodes),
            "edges": len(edges),
            "confirmed_findings": sum(item["status"] == "confirmed" for item in findings),
            "candidate_findings": sum(item["status"] == "candidate" for item in findings),
            "resolved_findings": sum(item["status"] == "resolved" for item in findings),
            "rejected_findings": sum(item["status"] == "rejected" for item in findings),
            "unadjudicated_edges": sum(edge["status"] in {"candidate", "unknown"} for edge in edges),
            "unadjudicated_nodes": sum(
                node["type"] in {"rule", "entity"}
                and node["provenance"]["status"] != "confirmed"
                for node in nodes.values()
            ),
        },
        "findings": findings,
        "interpretation": "Графовые findings не меняют score или cap без ручного переноса evidence в review.",
    }


def render_markdown(report: dict[str, Any]) -> str:
    metrics = report["metrics"]
    lines = [
        "# Проверка графа инструкций",
        "",
        f"Узлов: {metrics['nodes']}; рёбер: {metrics['edges']}; "
        f"confirmed findings: {metrics['confirmed_findings']}; candidates: {metrics['candidate_findings']}; "
        f"resolved/rejected: {metrics['resolved_findings']}/{metrics['rejected_findings']}.",
        f"Unadjudicated semantic nodes: {metrics['unadjudicated_nodes']}.",
        "",
        "> Findings не изменяют оценку или cap без ручного adjudication.",
        "",
        "| Статус | Severity | Код | Узлы | Candidate fingerprint | Наблюдение |",
        "|---|---|---|---|---|---|",
    ]
    if not report["findings"]:
        lines.append("| — | — | — | — | — | Структурных findings нет |")
    for finding in report["findings"]:
        message = finding["message"].replace("|", "\\|")
        fingerprint = finding.get("candidate_fingerprint", "—")
        lines.append(
            f"| {finding['status']} | {finding['severity']} | `{finding['code']}` | "
            f"{', '.join(finding['nodes'])} | `{fingerprint}` | {message} |"
        )
    return "\n".join(lines) + "\n"


def _topology_paths(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise GraphError(f"{label} должен быть непустым массивом путей.")
    paths: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not safe_relative_path(item):
            raise GraphError(f"{label}[{index}] должен быть безопасным относительным путём.")
        if item in paths:
            raise GraphError(f"{label} содержит дубликат {item}.")
        paths.append(item)
    return paths


def _topology_edge(value: Any, label: str) -> tuple[str, str]:
    if not isinstance(value, dict):
        raise GraphError(f"{label} должен быть объектом.")
    keys = set(value)
    if keys == {"parent", "child"}:
        source, target = value["parent"], value["child"]
    elif keys == {"from", "to"}:
        source, target = value["from"], value["to"]
    else:
        raise GraphError(f"{label} должен содержать ровно parent/child или from/to.")
    if not isinstance(source, str) or not safe_relative_path(source):
        raise GraphError(f"{label}.parent/from должен быть безопасным относительным путём.")
    if not isinstance(target, str) or not safe_relative_path(target):
        raise GraphError(f"{label}.child/to должен быть безопасным относительным путём.")
    return source, target


def normalize_topology_config(raw: Any | None, source: str = "topology configuration") -> dict[str, Any]:
    """Validate and normalize a repository final-topology policy."""
    implicit_default = raw is None
    if implicit_default:
        raw = default_topology_config()
    if not isinstance(raw, dict):
        raise GraphError(f"{source} должен быть JSON-объектом.")
    allowed_keys = {
        "schema_version", "name", "root_nodes", "allowed_root_nodes", "required_root_nodes",
        "required_edges", "parent_rules", "single_parent", "exactly_one_parent", "no_parent",
        "allow_additional_peer_routes", "forbidden_edges",
    }
    unknown = set(raw) - allowed_keys
    if unknown:
        raise GraphError(f"{source}: неизвестные поля {', '.join(sorted(unknown))}.")
    if raw.get("schema_version", "1.0") != "1.0":
        raise GraphError(f"{source}.schema_version должен быть 1.0.")

    root_nodes = raw.get("root_nodes")
    allowed_value = raw.get("allowed_root_nodes")
    required_value = raw.get("required_root_nodes")
    roots_declared = root_nodes is not None or allowed_value is not None or required_value is not None
    if root_nodes is not None:
        if isinstance(root_nodes, list):
            if allowed_value is not None or required_value is not None:
                raise GraphError(f"{source}: root_nodes нельзя смешивать с top-level root lists.")
            allowed_value = required_value = root_nodes
        elif isinstance(root_nodes, dict):
            if set(root_nodes) - {"allowed", "required"}:
                raise GraphError(f"{source}.root_nodes содержит неизвестные поля.")
            if allowed_value is not None or required_value is not None:
                raise GraphError(f"{source}: root_nodes нельзя смешивать с top-level root lists.")
            allowed_value = root_nodes.get("allowed")
            required_value = root_nodes.get("required")
        else:
            raise GraphError(f"{source}.root_nodes должен быть массивом или объектом.")
    if allowed_value is None and required_value is None:
        defaults = default_topology_config()
        allowed_value = defaults["allowed_root_nodes"]
        required_value = defaults["required_root_nodes"]
    elif allowed_value is None:
        allowed_value = required_value
    elif required_value is None:
        required_value = allowed_value
    allowed_roots = _topology_paths(allowed_value, f"{source}.allowed_root_nodes")
    required_roots = _topology_paths(required_value, f"{source}.required_root_nodes")
    if not set(required_roots) <= set(allowed_roots):
        missing = sorted(set(required_roots) - set(allowed_roots))
        raise GraphError(f"{source}: required_root_nodes не разрешены: {', '.join(missing)}.")

    required_edges: list[tuple[str, str]] = []
    raw_required_edges = raw.get("required_edges", [])
    if not isinstance(raw_required_edges, list):
        raise GraphError(f"{source}.required_edges должен быть массивом.")
    for index, item in enumerate(raw_required_edges):
        edge = _topology_edge(item, f"{source}.required_edges[{index}]")
        if edge in required_edges:
            raise GraphError(f"{source}.required_edges содержит дубликат {edge[0]} -> {edge[1]}.")
        required_edges.append(edge)
    unknown_edge_nodes = {
        path
        for edge in required_edges
        for path in edge
        if path not in allowed_roots
    }
    if unknown_edge_nodes:
        raise GraphError(
            f"{source}.required_edges содержит неразрешённые root nodes: "
            + ", ".join(sorted(unknown_edge_nodes))
            + "."
        )

    parent_rules: dict[str, str] = {}
    raw_parent_rules = raw.get("parent_rules", {})
    if not isinstance(raw_parent_rules, dict):
        raise GraphError(f"{source}.parent_rules должен быть объектом.")
    for path, rule in raw_parent_rules.items():
        if not isinstance(path, str) or not safe_relative_path(path):
            raise GraphError(f"{source}.parent_rules содержит небезопасный путь.")
        if rule not in {"none", "exactly_one", "any"}:
            raise GraphError(f"{source}.parent_rules[{path}] должен быть none, exactly_one или any.")
        parent_rules[path] = rule
    for field, rule in (("single_parent", "exactly_one"), ("exactly_one_parent", "exactly_one"), ("no_parent", "none")):
        if field not in raw:
            continue
        for path in _topology_paths(raw[field], f"{source}.{field}"):
            previous = parent_rules.get(path)
            if previous is not None and previous != rule:
                raise GraphError(f"{source}: противоречивые parent rules для {path}.")
            parent_rules[path] = rule
    unknown_parent_paths = set(parent_rules) - set(allowed_roots)
    if unknown_parent_paths:
        raise GraphError(
            f"{source}.parent_rules содержит неразрешённые root nodes: "
            + ", ".join(sorted(unknown_parent_paths))
            + "."
        )

    additional = raw.get("allow_additional_peer_routes", False)
    if not isinstance(additional, (bool, dict)):
        raise GraphError(f"{source}.allow_additional_peer_routes должен быть boolean или объектом.")
    if isinstance(additional, dict):
        additional_by_source: bool | dict[str, bool] = {}
        for path, value in additional.items():
            if not isinstance(path, str) or not safe_relative_path(path) or not isinstance(value, bool):
                raise GraphError(f"{source}.allow_additional_peer_routes содержит некорректное правило.")
            additional_by_source[path] = value
    else:
        additional_by_source = additional

    forbidden_edges: list[tuple[str, str]] = []
    raw_forbidden_edges = raw.get("forbidden_edges", [])
    if not isinstance(raw_forbidden_edges, list):
        raise GraphError(f"{source}.forbidden_edges должен быть массивом.")
    for index, item in enumerate(raw_forbidden_edges):
        edge = _topology_edge(item, f"{source}.forbidden_edges[{index}]")
        if edge in forbidden_edges:
            raise GraphError(f"{source}.forbidden_edges содержит дубликат {edge[0]} -> {edge[1]}.")
        if edge in required_edges:
            raise GraphError(f"{source}: edge {edge[0]} -> {edge[1]} одновременно required и forbidden.")
        forbidden_edges.append(edge)
    name = raw.get("name", source)
    if not isinstance(name, str) or not name.strip():
        raise GraphError(f"{source}.name должен быть непустой строкой.")
    return {
        "name": name.strip(),
        "allowed_root_nodes": allowed_roots,
        "required_root_nodes": required_roots,
        "required_edges": required_edges,
        "parent_rules": parent_rules,
        "allow_additional_peer_routes": additional_by_source,
        "forbidden_edges": forbidden_edges,
        # The historical default tolerated unrelated authority roots and only
        # constrained the top-level authority root's outgoing routes.  For an
        # explicit policy, top-level sources are derived from required_edges:
        # they are required-edge parents that are not required-edge children.
        "enforce_allowed_root_nodes": not implicit_default and roots_declared,
        "strict_route_sources": (
            {"AGENTS.md"}
            if implicit_default
            else (
                {source_path for source_path, _ in required_edges}
                - {target_path for _, target_path in required_edges}
            )
        ),
    }


def load_topology_config(path: Path | None, root: Path) -> dict[str, Any] | None:
    if path is not None:
        value = load_json(path)
        if not isinstance(value, dict):
            raise GraphError(f"{path} должен быть JSON-объектом.")
        return value
    lexical_root = root.expanduser().absolute()
    try:
        resolved_root = lexical_root.resolve(strict=True)
    except (FileNotFoundError, OSError) as exc:
        raise GraphError("repository topology policy requires an existing --root directory.") from exc
    for relative in TOPOLOGY_CONFIG_CANDIDATES:
        candidate = lexical_root / relative
        try:
            resolved_candidate = candidate.resolve(strict=True)
        except FileNotFoundError:
            # A symlinked directory with no candidate policy is not an
            # attempted policy traversal. Preserve the default behavior.
            continue
        except (OSError, RuntimeError, ValueError) as exc:
            raise GraphError(f"repository topology policy is outside --root: {candidate}.") from exc
        if not resolved_candidate.is_file():
            continue
        current = candidate
        while True:
            if current.is_symlink():
                raise GraphError(
                    f"repository topology policy passes through a symlink: {candidate}."
                )
            if current == lexical_root:
                break
            if current == current.parent:
                raise GraphError(f"repository topology policy is outside --root: {candidate}.")
            current = current.parent
        try:
            resolved_candidate.relative_to(resolved_root)
        except (OSError, RuntimeError, ValueError) as exc:
            raise GraphError(f"repository topology policy is outside --root: {candidate}.") from exc
        value = load_json(resolved_candidate)
        if not isinstance(value, dict):
            raise GraphError(f"{resolved_candidate} должен быть JSON-объектом.")
        return value
    return None


def final_topology_issues(
    graph: dict[str, Any], topology_config: dict[str, Any] | None = None
) -> list[str]:
    config = normalize_topology_config(topology_config)
    allowed_roots = set(config["allowed_root_nodes"])
    required_roots = set(config["required_root_nodes"])
    required_edges = set(config["required_edges"])
    issues: list[str] = []
    unit_paths = {unit["path"] for unit in graph["scope"]["units"]}
    missing_units = required_roots - unit_paths
    if missing_units:
        issues.append(
            f"topology rule required root nodes: missing scope units {', '.join(sorted(missing_units))}"
        )

    artifacts_by_path: dict[str, str] = {}
    artifact_nodes: dict[str, dict[str, Any]] = {}
    authority_root_paths: set[str] = set()
    for node in graph["nodes"]:
        if node.get("type") != "artifact":
            continue
        artifact_nodes[node["id"]] = node
        if node.get("artifact_kind") == "root_instruction" and node.get("authority") is True:
            path = node["source"]["path"]
            if path in artifacts_by_path:
                issues.append(f"topology rule root nodes: duplicate authority artifact {path}")
            artifacts_by_path[path] = node["id"]
            authority_root_paths.add(path)
    missing_artifacts = required_roots - set(artifacts_by_path)
    if missing_artifacts:
        issues.append(
            "topology rule required root nodes: missing authority artifacts "
            + ", ".join(sorted(missing_artifacts))
        )
    unexpected_roots = authority_root_paths - allowed_roots
    if config["enforce_allowed_root_nodes"] and unexpected_roots:
        issues.append(
            "topology rule allowed root nodes: unexpected authority artifacts "
            + ", ".join(sorted(unexpected_roots))
        )

    contract_unit_paths = {
        path
        for path in unit_paths
        if len(PurePosixPath(path).parts) == 2
        and PurePosixPath(path).parts[0] == "contracts"
        and CONTRACT_FILE_RE.fullmatch(PurePosixPath(path).name)
    }
    contract_artifact_paths = {
        node["source"]["path"]
        for node in artifact_nodes.values()
        if node.get("artifact_kind") == "contract"
    }
    missing_contract_artifacts = contract_unit_paths - contract_artifact_paths
    if missing_contract_artifacts:
        issues.append(
            "отсутствуют contract artifact nodes: "
            + ", ".join(sorted(missing_contract_artifacts))
        )

    incoming_routes: dict[str, list[str]] = defaultdict(list)
    route_pairs: list[tuple[str, str, str]] = []
    for edge in graph["edges"]:
        if edge.get("type") != "routes_to" or edge.get("status") != "confirmed":
            continue
        source = artifact_nodes.get(edge.get("from"))
        target = artifact_nodes.get(edge.get("to"))
        if source is not None and target is not None:
            incoming_routes[target["id"]].append(source["id"])
            route_pairs.append((source["source"]["path"], target["source"]["path"], edge["id"]))

    for parent_path, child_path in sorted(required_edges):
        parent_id = artifacts_by_path.get(parent_path)
        child_id = artifacts_by_path.get(child_path)
        actual = [
            (source, target, edge_id)
            for source, target, edge_id in route_pairs
            if target == child_path
        ]
        if parent_id is None or child_id is None:
            continue
        if not any(source == parent_path and target == child_path for source, target, _ in actual):
            actual_edges = sorted(f"{source} -> {target} ({edge_id})" for source, target, edge_id in actual)
            detail = ", ".join(actual_edges) if actual_edges else "none"
            issues.append(
                f"topology rule required edge {parent_path} -> {child_path}: "
                f"missing; actual confirmed incoming edges: {detail}"
            )

    parent_rules: dict[str, str] = config["parent_rules"]
    for target_path, rule in sorted(parent_rules.items()):
        target_id = artifacts_by_path.get(target_path)
        if target_id is None:
            continue
        actual_parent_paths = sorted(
            artifact_nodes[parent_id]["source"]["path"]
            for parent_id in incoming_routes.get(target_id, [])
        )
        actual_parent_edges = sorted(
            f"{edge_source_path} -> {edge_target_path} ({edge_id})"
            for edge_source_path, edge_target_path, edge_id in route_pairs
            if edge_target_path == target_path
        )
        if rule == "none" and actual_parent_paths:
            issues.append(
                f"topology rule parent count {target_path}=none: "
                f"actual confirmed parents {actual_parent_paths}; edges {actual_parent_edges}"
            )
        elif rule == "exactly_one" and len(actual_parent_paths) != 1:
            issues.append(
                f"topology rule parent count {target_path}=exactly_one: "
                f"actual confirmed parents {actual_parent_paths}; edges {actual_parent_edges}"
            )

    allowed_additional = config["allow_additional_peer_routes"]
    strict_route_sources = config["strict_route_sources"]
    for source_path, target_path, edge_id in sorted(route_pairs):
        if (source_path, target_path) in config["forbidden_edges"]:
            issues.append(
                f"topology rule forbidden edge {source_path} -> {target_path}: "
                f"found confirmed routes_to edge {source_path} -> {target_path} ({edge_id})"
            )
        if source_path not in allowed_roots or (source_path, target_path) in required_edges:
            continue
        is_peer_route = target_path in allowed_roots
        if not is_peer_route:
            if source_path in strict_route_sources:
                issues.append(
                    f"topology rule additional routes disallowed for {source_path}: "
                    "allow_additional_peer_routes only permits allowed peer roots; "
                    f"found confirmed edge {source_path} -> {target_path} ({edge_id})"
                )
            continue
        if isinstance(allowed_additional, dict):
            permit = allowed_additional.get(source_path, False)
        else:
            permit = allowed_additional
        if not permit:
            issues.append(
                f"topology rule additional routes disallowed for {source_path}: "
                f"found confirmed edge {source_path} -> {target_path} ({edge_id})"
            )

    for node_id, node in sorted(artifact_nodes.items()):
        if node.get("artifact_kind") != "contract":
            continue
        parents = incoming_routes.get(node_id, [])
        if len(parents) != 1:
            issues.append(
                f"contract {node['source']['path']} требует ровно один confirmed artifact parent; "
                f"найдено {len(parents)}"
            )
        else:
            parent = artifact_nodes[parents[0]]
            if (
                parent.get("authority") is not True
                or parent.get("artifact_kind") not in {"root_instruction", "contract"}
            ):
                issues.append(
                    f"contract {node['source']['path']} имеет неканонического parent "
                    f"{parent['source']['path']}"
                )
    return issues


def migration_gate_failed(report: dict[str, Any]) -> bool:
    return (
        any(
            item["status"] == "confirmed"
            and (
                item["severity"] == "error"
                or item["code"] in {"orphan-rule", "rule-without-scope"}
            )
            for item in report["findings"]
        )
        or any(item["status"] == "candidate" for item in report["findings"])
        or report["metrics"]["unadjudicated_edges"] > 0
        or report["metrics"]["unadjudicated_nodes"] > 0
    )


def write_exclusive(path: Path, content: str, protected: set[Path]) -> None:
    output = path.expanduser().absolute()
    if output in protected or output.resolve(strict=False) in protected:
        raise GraphError("Файл вывода совпадает с входным файлом.")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(output, flags, 0o600)
    except OSError as exc:
        raise GraphError(f"Нельзя создать output без перезаписи: {exc}") from exc
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(content)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Проверить отдельный граф инструкций.")
    parser.add_argument("graph", help="instruction graph JSON")
    parser.add_argument("--root", required=True, help="Корень для обязательной проверки content SHA-256")
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    parser.add_argument("--output", help="Создать новый output; перезапись запрещена")
    parser.add_argument("--fail-on-confirmed", action="store_true", help="Отклонить confirmed error findings")
    parser.add_argument(
        "--migration-gate",
        action="store_true",
        help="Отклонить confirmed errors, orphan/scopeless rules и неразрешённые candidates/edges",
    )
    parser.add_argument(
        "--final-topology-gate",
        action="store_true",
        help="Применить migration gate и проверить настроенную final-topology policy",
    )
    parser.add_argument(
        "--topology-config",
        "--final-topology-config",
        dest="topology_config",
        help=(
            "JSON policy для --final-topology-gate; без него используется ближайший "
            ".codex/final-topology.json или исторический default"
        ),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    graph_path = Path(args.graph).expanduser().resolve()
    try:
        graph = load_json(graph_path)
        schema = load_json(SCHEMA_PATH)
        if not isinstance(graph, dict) or not isinstance(schema, dict):
            raise GraphError("Graph и schema должны быть JSON-объектами.")
        if schema.get("properties", {}).get("schema_version", {}).get("const") != "1.0":
            raise GraphError("instruction-graph.schema.json не согласован с checker.")
        validate_schema_contract(schema)
        report = validate_and_check(graph, Path(args.root))
        if args.final_topology_gate:
            topology_path = Path(args.topology_config).expanduser().resolve() if args.topology_config else None
            topology_config = load_topology_config(topology_path, Path(args.root).expanduser().resolve())
            topology_issues = final_topology_issues(graph, topology_config)
            if topology_issues:
                add_finding(
                    report["findings"],
                    "incomplete-final-topology",
                    "error",
                    "confirmed",
                    "; ".join(topology_issues) + ".",
                    [],
                )
                report["metrics"]["confirmed_findings"] += 1
        output = (
            json.dumps(report, ensure_ascii=False, indent=2) + "\n"
            if args.format == "json"
            else render_markdown(report)
        )
        if args.output:
            write_exclusive(Path(args.output), output, {graph_path, SCHEMA_PATH.resolve()})
        else:
            sys.stdout.write(output)
    except (GraphError, TypeError, KeyError) as exc:
        print(f"Ошибка графа:\n{exc}", file=sys.stderr)
        return 2
    if args.fail_on_confirmed and any(
        item["status"] == "confirmed" and item["severity"] == "error"
        for item in report["findings"]
    ):
        return 4
    if (args.migration_gate or args.final_topology_gate) and migration_gate_failed(report):
        return 4
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
