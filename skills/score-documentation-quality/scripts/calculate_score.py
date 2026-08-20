#!/usr/bin/env python3
"""Validate an evidence-backed review and calculate a bounded 0–100 score."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
from pathlib import Path, PurePosixPath
from typing import Any


SKILL_DIR = Path(__file__).resolve().parent.parent
RUBRIC_PATH = SKILL_DIR / "references" / "rubric.json"
SCHEMA_PATH = SKILL_DIR / "references" / "review.schema.json"
STATUSES = {"met", "partial", "unmet", "unknown"}
EVIDENCE_KINDS = {"supports", "gap", "conflict", "unavailable"}
ROLES = {"authority", "core", "supporting"}
PROFILES = {"instruction", "runbook", "specification", "guide", "reference"}
PLACEHOLDERS = {
    "PATH",
    "VERSION",
    "PROFILE",
    "PURPOSE",
    "HEADING_OR_LINE",
    "SOURCE_URL_OR_PATH",
}
IDENTIFIER_RE = re.compile(r"^[a-z][a-z0-9_-]*$")
SHA256_RE = re.compile(r"^[a-f0-9]{64}$")


class ReviewError(ValueError):
    pass


def reject_json_constant(value: str) -> None:
    raise ReviewError(f"JSON-константа {value} не является конечным числом.")


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"), parse_constant=reject_json_constant)
    except FileNotFoundError as exc:
        raise ReviewError(f"Файл не найден: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ReviewError(f"Некорректный JSON в {path}: {exc}") from exc


def valid_text(value: Any) -> bool:
    return (
        isinstance(value, str)
        and bool(value.strip())
        and value.strip().upper() not in PLACEHOLDERS
    )


def require_exact_keys(value: dict[str, Any], required: set[str], label: str) -> list[str]:
    errors: list[str] = []
    missing = required - set(value)
    extra = set(value) - required
    if missing:
        errors.append(f"{label}: отсутствуют поля {', '.join(sorted(missing))}.")
    if extra:
        errors.append(f"{label}: неизвестные поля {', '.join(sorted(extra))}.")
    return errors


def safe_relative_path(value: Any) -> bool:
    if not valid_text(value):
        return False
    path = PurePosixPath(value)
    return not path.is_absolute() and ".." not in path.parts and value == path.as_posix()


def scope_digest(scope: dict[str, Any]) -> str:
    payload = {
        "purpose": scope["purpose"],
        "version": scope["version"],
        "complete": scope["complete"],
        "units": sorted(scope["units"], key=lambda item: item["path"]),
        "excluded": sorted(scope["excluded"]),
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def path_has_symlink_component(path: Path) -> bool:
    return any(component.is_symlink() for component in (path, *path.parents))


def resolved_scope_root(value: str | Path) -> Path:
    lexical = Path(value).expanduser().absolute()
    if path_has_symlink_component(lexical):
        raise ReviewError("--root не может проходить через symlink.")
    try:
        root = lexical.resolve(strict=True)
    except FileNotFoundError as exc:
        raise ReviewError(f"--root не найден: {lexical}") from exc
    if not root.is_dir():
        raise ReviewError("--root должен быть каталогом.")
    return root


def resolve_scope_file(root: Path, relative_path: str) -> Path:
    candidate = root / relative_path
    current = candidate
    while current != root:
        if current.is_symlink():
            raise ReviewError(f"Scope unit проходит через symlink: {relative_path}.")
        current = current.parent
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
    except (FileNotFoundError, ValueError) as exc:
        raise ReviewError(f"Scope unit отсутствует или выходит за --root: {relative_path}.") from exc
    if not resolved.is_file():
        raise ReviewError(f"Scope unit не является файлом: {relative_path}.")
    return resolved


def verify_scope_files(root: Path, units: list[dict[str, Any]]) -> list[Path]:
    resolved_files: list[Path] = []
    for unit in units:
        resolved = resolve_scope_file(root, unit["path"])
        actual = hashlib.sha256(resolved.read_bytes()).hexdigest()
        if actual != unit["content_sha256"]:
            raise ReviewError(f"Content SHA-256 не совпадает: {unit['path']}.")
        resolved_files.append(resolved)
    return resolved_files


def criterion_definitions(rubric: dict[str, Any]) -> list[dict[str, Any]]:
    return [criterion for dimension in rubric["dimensions"] for criterion in dimension["criteria"]]


def validate_schema_contract(schema: dict[str, Any], rubric: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    properties = schema.get("properties", {})
    expected_root = {"scope", "criteria", "systemic_findings", "strengths", "recommendations", "limitations"}
    if set(schema.get("required", [])) != expected_root or set(properties) != expected_root:
        errors.append("review.schema.json: root-поля не согласованы с calculator.")
    scope_schema = properties.get("scope", {})
    expected_scope = {"purpose", "version", "complete", "digest", "units", "excluded"}
    if set(scope_schema.get("required", [])) != expected_scope:
        errors.append("review.schema.json: обязательные scope-поля не согласованы.")
    unit_schema = scope_schema.get("properties", {}).get("units", {}).get("items", {})
    expected_unit = {"path", "content_sha256", "role", "profile"}
    if set(unit_schema.get("required", [])) != expected_unit:
        errors.append("review.schema.json: обязательные поля scope unit не согласованы.")
    unit_properties = unit_schema.get("properties", {})
    if set(unit_properties.get("role", {}).get("enum", [])) != ROLES:
        errors.append("review.schema.json: roles не согласованы.")
    if set(unit_properties.get("profile", {}).get("enum", [])) != PROFILES:
        errors.append("review.schema.json: profiles не согласованы.")
    definitions = schema.get("$defs", {})
    criteria_schema = properties.get("criteria", {})
    if criteria_schema.get("minProperties") != 32 or criteria_schema.get("maxProperties") != 32:
        errors.append("review.schema.json: число criteria properties не согласовано.")
    if set(definitions.get("evidence", {}).get("required", [])) != {"kind", "path", "locator", "note"}:
        errors.append("review.schema.json: обязательные evidence-поля не согласованы.")
    if definitions.get("evidence", {}).get("properties", {}).get("path", {}).get("type") != "string":
        errors.append("review.schema.json: тип evidence.path не согласован.")
    if set(definitions.get("evidence", {}).get("properties", {}).get("kind", {}).get("enum", [])) != EVIDENCE_KINDS:
        errors.append("review.schema.json: evidence kinds не согласованы.")
    if set(definitions.get("criterion", {}).get("required", [])) != {"status", "rationale", "evidence"}:
        errors.append("review.schema.json: обязательные criterion-поля не согласованы.")
    if set(definitions.get("systemicFinding", {}).get("required", [])) != {
        "id", "type", "root_cause", "summary", "evidence"
    }:
        errors.append("review.schema.json: обязательные systemic finding-поля не согласованы.")
    if set(definitions.get("criterion", {}).get("properties", {}).get("status", {}).get("enum", [])) != STATUSES:
        errors.append("review.schema.json: statuses не согласованы.")
    if set(definitions.get("systemicFinding", {}).get("properties", {}).get("type", {}).get("enum", [])) != set(rubric.get("systemic_gates", {})):
        errors.append("review.schema.json: systemic finding types не согласованы.")
    if definitions.get("systemicFinding", {}).get("properties", {}).get("id", {}).get("pattern") != IDENTIFIER_RE.pattern:
        errors.append("review.schema.json: pattern systemic finding id не согласован.")
    return errors


def validate_rubric(rubric: dict[str, Any], schema: dict[str, Any]) -> None:
    errors: list[str] = validate_schema_contract(schema, rubric)
    if rubric.get("schema_version") != "2.0":
        errors.append("rubric.schema_version должен быть 2.0.")
    calculation = rubric.get("calculation")
    if not isinstance(calculation, dict):
        errors.append("rubric.calculation должен быть объектом.")
        calculation = {}
    factors = calculation.get("status_factors")
    if not isinstance(factors, dict) or set(factors) != STATUSES:
        errors.append("rubric.calculation.status_factors должен определять четыре статуса.")
        factors = {}
    for status in STATUSES:
        factor = factors.get(status)
        if not isinstance(factor, dict) or set(factor) != {"minimum", "maximum"}:
            errors.append(f"status_factors.{status} должен содержать minimum и maximum.")
            continue
        minimum = factor["minimum"]
        maximum = factor["maximum"]
        if (
            isinstance(minimum, bool)
            or isinstance(maximum, bool)
            or not isinstance(minimum, (int, float))
            or not isinstance(maximum, (int, float))
            or not math.isfinite(float(minimum))
            or not math.isfinite(float(maximum))
            or not 0 <= float(minimum) <= float(maximum) <= 1
        ):
            errors.append(f"status_factors.{status} должен задавать конечный диапазон 0..1.")
    precision = calculation.get("precision")
    if precision != 0.5:
        errors.append("rubric.calculation.precision должен быть 0.5.")
    if calculation.get("set_aggregation") != "single_document_system_no_averaging":
        errors.append("rubric.calculation.set_aggregation имеет неизвестное значение.")
    confidence = calculation.get("confidence")
    if not isinstance(confidence, dict) or set(confidence) != {
        "high_maximum_unknown_points",
        "medium_maximum_unknown_points",
    }:
        errors.append("rubric.calculation.confidence неполон.")
    elif confidence["high_maximum_unknown_points"] != 0 or not 0 < confidence[
        "medium_maximum_unknown_points"
    ] <= 100:
        errors.append("Пороги confidence некорректны.")

    dimensions = rubric.get("dimensions")
    if not isinstance(dimensions, list) or not dimensions:
        errors.append("rubric.dimensions должен быть непустым массивом.")
        dimensions = []
    dimension_ids: set[str] = set()
    criterion_ids: set[str] = set()
    total = 0.0
    for index, dimension in enumerate(dimensions):
        label = f"rubric.dimensions[{index}]"
        if not isinstance(dimension, dict):
            errors.append(f"{label} должен быть объектом.")
            continue
        errors.extend(require_exact_keys(dimension, {"id", "name", "criteria"}, label))
        dimension_id = dimension.get("id")
        if not valid_text(dimension_id) or not IDENTIFIER_RE.fullmatch(dimension_id):
            errors.append(f"{label}.id некорректен.")
        elif dimension_id in dimension_ids:
            errors.append(f"Дублируется dimension id: {dimension_id}.")
        else:
            dimension_ids.add(dimension_id)
        if not valid_text(dimension.get("name")):
            errors.append(f"{label}.name обязателен.")
        criteria = dimension.get("criteria")
        if not isinstance(criteria, list) or not criteria:
            errors.append(f"{label}.criteria должен быть непустым массивом.")
            continue
        for criterion_index, criterion in enumerate(criteria):
            criterion_label = f"{label}.criteria[{criterion_index}]"
            if not isinstance(criterion, dict):
                errors.append(f"{criterion_label} должен быть объектом.")
                continue
            errors.extend(require_exact_keys(criterion, {"id", "name", "points"}, criterion_label))
            criterion_id = criterion.get("id")
            if not valid_text(criterion_id) or not IDENTIFIER_RE.fullmatch(criterion_id):
                errors.append(f"{criterion_label}.id некорректен.")
            elif criterion_id in criterion_ids:
                errors.append(f"Дублируется criterion id: {criterion_id}.")
            else:
                criterion_ids.add(criterion_id)
            if not valid_text(criterion.get("name")):
                errors.append(f"{criterion_label}.name обязателен.")
            points = criterion.get("points")
            if (
                isinstance(points, bool)
                or not isinstance(points, (int, float))
                or not math.isfinite(float(points))
                or points <= 0
                or float(points) * 2 != int(float(points) * 2)
            ):
                errors.append(f"{criterion_label}.points должен быть положительным с шагом 0.5.")
            else:
                total += float(points)
    if total != 100:
        errors.append(f"Сумма критериев рубрики должна быть 100, получено {total}.")

    gates = rubric.get("systemic_gates")
    if not isinstance(gates, dict) or not gates:
        errors.append("rubric.systemic_gates должен быть непустым объектом.")
        gates = {}
    for gate_id, gate in gates.items():
        label = f"rubric.systemic_gates.{gate_id}"
        if not IDENTIFIER_RE.fullmatch(gate_id) or not isinstance(gate, dict):
            errors.append(f"{label} некорректен.")
            continue
        errors.extend(require_exact_keys(gate, {"name", "cap", "minimum_evidence"}, label))
        cap = gate.get("cap")
        minimum = gate.get("minimum_evidence")
        if not valid_text(gate.get("name")):
            errors.append(f"{label}.name обязателен.")
        if isinstance(cap, bool) or not isinstance(cap, int) or not 0 <= cap <= 100:
            errors.append(f"{label}.cap должен быть целым 0..100.")
        if isinstance(minimum, bool) or not isinstance(minimum, int) or minimum < 1:
            errors.append(f"{label}.minimum_evidence должен быть положительным целым.")

    levels = rubric.get("levels")
    if not isinstance(levels, list) or not levels:
        errors.append("rubric.levels должен быть непустым массивом.")
        levels = []
    previous = 101.0
    for index, level in enumerate(levels):
        if not isinstance(level, dict) or set(level) != {"minimum", "name"}:
            errors.append(f"rubric.levels[{index}] некорректен.")
            continue
        minimum = level["minimum"]
        if (
            isinstance(minimum, bool)
            or not isinstance(minimum, (int, float))
            or not math.isfinite(float(minimum))
            or not 0 <= float(minimum) <= 100
            or float(minimum) >= previous
        ):
            errors.append("rubric.levels должен строго убывать в диапазоне 0..100.")
        previous = float(minimum)
        if not valid_text(level.get("name")):
            errors.append(f"rubric.levels[{index}].name обязателен.")
    if levels and levels[-1].get("minimum") != 0:
        errors.append("Последний уровень рубрики должен начинаться с 0.")

    schema_ids = (
        schema.get("properties", {})
        .get("criteria", {})
        .get("propertyNames", {})
        .get("enum")
    )
    if not isinstance(schema_ids, list) or set(schema_ids) != criterion_ids or len(schema_ids) != len(criterion_ids):
        errors.append("review.schema.json не согласован с критериями rubric.json.")
    if errors:
        raise ReviewError("\n".join(f"- {error}" for error in errors))


def review_template(rubric: dict[str, Any], scope: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "scope": scope
        or {
            "purpose": "PURPOSE",
            "version": "VERSION",
            "complete": True,
            "digest": "SCOPE_SHA256",
            "units": [
                {
                    "path": "PATH",
                    "content_sha256": "CONTENT_SHA256",
                    "role": "core",
                    "profile": "guide",
                }
            ],
            "excluded": [],
        },
        "criteria": {
            criterion["id"]: {
                "status": "unknown",
                "rationale": "Объяснить, почему источник недоступен.",
                "evidence": [
                    {
                        "kind": "unavailable",
                        "path": "SOURCE_URL_OR_PATH",
                        "locator": "HEADING_OR_LINE",
                        "note": "Назвать недоступное доказательство.",
                    }
                ],
            }
            for criterion in criterion_definitions(rubric)
        },
        "systemic_findings": [],
        "strengths": ["Назвать подтверждённую сильную сторону."],
        "recommendations": ["Назвать приоритетное улучшение."],
        "limitations": ["Перечислить ограничение проверки или удалить этот элемент."],
    }


def build_scope_manifest(
    root_value: str,
    purpose: str,
    version: str,
    unit_specs: list[list[str]],
    excluded: list[str],
) -> dict[str, Any]:
    if not valid_text(purpose) or not valid_text(version):
        raise ReviewError("--purpose и --version обязательны и не могут быть placeholders.")
    root = resolved_scope_root(root_value)
    units: list[dict[str, str]] = []
    seen: set[str] = set()
    for path, role, profile in unit_specs:
        if not safe_relative_path(path):
            raise ReviewError(f"--unit path должен быть безопасным относительным POSIX-путём: {path!r}.")
        if path in seen:
            raise ReviewError(f"Дублируется --unit: {path}.")
        seen.add(path)
        if role not in ROLES or profile not in PROFILES:
            raise ReviewError(f"--unit {path}: неизвестный role или profile.")
        resolved = resolve_scope_file(root, path)
        units.append(
            {
                "path": path,
                "content_sha256": hashlib.sha256(resolved.read_bytes()).hexdigest(),
                "role": role,
                "profile": profile,
            }
        )
    if not any(unit["role"] in {"authority", "core"} for unit in units):
        raise ReviewError("--unit требует хотя бы одну роль authority или core.")
    if not all(valid_text(item) for item in excluded) or len(set(excluded)) != len(excluded):
        raise ReviewError("--exclude должен содержать уникальные непустые значения.")
    scope: dict[str, Any] = {
        "purpose": purpose,
        "version": version,
        "complete": True,
        "units": units,
        "excluded": excluded,
    }
    scope["digest"] = scope_digest(scope)
    return scope


def validate_evidence(
    evidence: Any,
    label: str,
    unit_paths: set[str],
) -> tuple[list[str], list[dict[str, str]]]:
    errors: list[str] = []
    if not isinstance(evidence, list) or not evidence:
        return [f"{label} должен быть непустым массивом."], []
    normalized: list[dict[str, str]] = []
    signatures: set[tuple[str, str]] = set()
    for index, item in enumerate(evidence):
        item_label = f"{label}[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{item_label} должен быть объектом.")
            continue
        errors.extend(require_exact_keys(item, {"kind", "path", "locator", "note"}, item_label))
        kind = item.get("kind")
        path = item.get("path")
        locator = item.get("locator")
        note = item.get("note")
        if not isinstance(kind, str) or kind not in EVIDENCE_KINDS:
            errors.append(f"{item_label}.kind неизвестен.")
        if not valid_text(path) or not valid_text(locator) or not valid_text(note):
            errors.append(f"{item_label} требует непустые path, locator и note без placeholders.")
            continue
        if kind != "unavailable" and path not in unit_paths:
            errors.append(f"{item_label}.path не входит в scope.units; URL разрешён только для unavailable.")
        signature = (path.casefold(), locator.casefold())
        if signature in signatures:
            errors.append(f"{item_label} дублирует path + locator в том же finding.")
        signatures.add(signature)
        normalized.append({"kind": kind, "path": path, "locator": locator, "note": note})
    return errors, normalized


def validate_string_list(value: Any, label: str, minimum: int, maximum: int | None) -> list[str]:
    if not isinstance(value, list):
        return [f"{label} должен быть массивом."]
    if len(value) < minimum or (maximum is not None and len(value) > maximum):
        bound = f"{minimum}..{maximum}" if maximum is not None else f"не менее {minimum}"
        return [f"{label} должен содержать {bound} элементов."]
    if not all(valid_text(item) for item in value):
        return [f"{label} содержит пустой элемент или placeholder."]
    return []


def validate_and_calculate(
    review: dict[str, Any],
    rubric: dict[str, Any],
    root: Path | None,
) -> tuple[dict[str, Any], list[Path]]:
    errors = require_exact_keys(
        review,
        {"scope", "criteria", "systemic_findings", "strengths", "recommendations", "limitations"},
        "review",
    )
    scope = review.get("scope")
    unit_paths: set[str] = set()
    unit_roles: dict[str, str] = {}
    resolved_files: list[Path] = []
    if not isinstance(scope, dict):
        errors.append("scope должен быть объектом.")
        scope = {}
    else:
        errors.extend(
            require_exact_keys(
                scope,
                {"purpose", "version", "complete", "digest", "units", "excluded"},
                "scope",
            )
        )
        if not valid_text(scope.get("purpose")) or not valid_text(scope.get("version")):
            errors.append("scope.purpose и scope.version обязательны и не могут быть placeholders.")
        if scope.get("complete") is not True:
            errors.append("scope.complete должен быть true; неполную область нужно завершить или разделить.")
        units = scope.get("units")
        core_count = 0
        if not isinstance(units, list) or not units:
            errors.append("scope.units должен быть непустым массивом.")
        else:
            for index, unit in enumerate(units):
                label = f"scope.units[{index}]"
                if not isinstance(unit, dict):
                    errors.append(f"{label} должен быть объектом.")
                    continue
                errors.extend(
                    require_exact_keys(unit, {"path", "content_sha256", "role", "profile"}, label)
                )
                path = unit.get("path")
                content_sha256 = unit.get("content_sha256")
                role = unit.get("role")
                profile = unit.get("profile")
                if not safe_relative_path(path):
                    errors.append(f"{label}.path должен быть безопасным относительным POSIX-путём.")
                elif path in unit_paths:
                    errors.append(f"Дублируется scope unit: {path}.")
                else:
                    unit_paths.add(path)
                    unit_roles[path] = role if isinstance(role, str) else ""
                if not isinstance(content_sha256, str) or not SHA256_RE.fullmatch(content_sha256):
                    errors.append(f"{label}.content_sha256 некорректен.")
                if not isinstance(role, str) or role not in ROLES:
                    errors.append(f"{label}.role неизвестен.")
                elif role in {"authority", "core"}:
                    core_count += 1
                if not isinstance(profile, str) or profile not in PROFILES:
                    errors.append(f"{label}.profile неизвестен.")
            if core_count == 0:
                errors.append("scope.units требует хотя бы одну роль authority или core.")
        excluded = scope.get("excluded")
        if not isinstance(excluded, list) or not all(valid_text(item) for item in excluded):
            errors.append("scope.excluded должен быть массивом непустых строк.")
        elif len(set(excluded)) != len(excluded):
            errors.append("scope.excluded содержит дубликаты.")
        digest = scope.get("digest")
        if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
            errors.append("scope.digest должен быть SHA-256.")
        elif not errors:
            if digest != scope_digest(scope):
                errors.append("scope.digest не соответствует purpose/version/units/excluded.")
            elif root is not None:
                try:
                    resolved_files = verify_scope_files(root, scope["units"])
                except ReviewError as exc:
                    errors.append(str(exc))

    expected = {criterion["id"] for criterion in criterion_definitions(rubric)}
    assessments = review.get("criteria")
    if not isinstance(assessments, dict):
        errors.append("criteria должен быть объектом.")
        assessments = {}
    missing = expected - set(assessments)
    extra = set(assessments) - expected
    if missing:
        errors.append("Нет критериев: " + ", ".join(sorted(missing)) + ".")
    if extra:
        errors.append("Неизвестные критерии: " + ", ".join(sorted(extra)) + ".")

    factors = rubric["calculation"]["status_factors"]
    criterion_results: dict[str, dict[str, Any]] = {}
    for definition in criterion_definitions(rubric):
        criterion_id = definition["id"]
        value = assessments.get(criterion_id)
        if not isinstance(value, dict):
            continue
        label = f"criteria.{criterion_id}"
        errors.extend(require_exact_keys(value, {"status", "rationale", "evidence"}, label))
        status = value.get("status")
        rationale = value.get("rationale")
        if not isinstance(status, str) or status not in STATUSES:
            errors.append(f"{label}.status неизвестен.")
            continue
        if not valid_text(rationale):
            errors.append(f"{label}.rationale обязателен и не может быть placeholder.")
        evidence_errors, evidence = validate_evidence(value.get("evidence"), f"{label}.evidence", unit_paths)
        errors.extend(evidence_errors)
        kinds = {item["kind"] for item in evidence if isinstance(item.get("kind"), str)}
        if status == "met" and kinds != {"supports"}:
            errors.append(f"{label}: met допускает только supports.")
        elif status == "partial" and not (
            "supports" in kinds and bool(kinds & {"gap", "conflict"}) and "unavailable" not in kinds
        ):
            errors.append(f"{label}: partial требует supports и gap/conflict без unavailable.")
        elif status == "unmet" and not (kinds and kinds <= {"gap", "conflict"}):
            errors.append(f"{label}: unmet допускает только gap/conflict.")
        elif status == "unknown" and kinds != {"unavailable"}:
            errors.append(f"{label}: unknown допускает только unavailable.")
        if status in {"met", "partial"} and not any(
            item["kind"] == "supports" and unit_roles.get(item["path"]) in {"authority", "core"}
            for item in evidence
        ):
            errors.append(f"{label}: supports требует хотя бы один authority/core locator.")
        points = float(definition["points"])
        minimum = round(points * float(factors[status]["minimum"]), 1)
        maximum = round(points * float(factors[status]["maximum"]), 1)
        criterion_results[criterion_id] = {
            "id": criterion_id,
            "name": definition["name"],
            "points": points,
            "status": status,
            "minimum": minimum,
            "maximum": maximum,
            "rationale": rationale,
            "evidence": evidence,
        }

    gate_definitions = rubric["systemic_gates"]
    findings = review.get("systemic_findings")
    if not isinstance(findings, list):
        errors.append("systemic_findings должен быть массивом.")
        findings = []
    finding_results: list[dict[str, Any]] = []
    finding_ids: set[str] = set()
    root_causes: set[str] = set()
    caps: list[int] = []
    for index, finding in enumerate(findings):
        label = f"systemic_findings[{index}]"
        if not isinstance(finding, dict):
            errors.append(f"{label} должен быть объектом.")
            continue
        errors.extend(require_exact_keys(finding, {"id", "type", "root_cause", "summary", "evidence"}, label))
        finding_id = finding.get("id")
        finding_type = finding.get("type")
        root_cause = finding.get("root_cause")
        summary = finding.get("summary")
        if not valid_text(finding_id) or not IDENTIFIER_RE.fullmatch(finding_id):
            errors.append(f"{label}.id некорректен.")
        elif finding_id in finding_ids:
            errors.append(f"Дублируется finding id: {finding_id}.")
        else:
            finding_ids.add(finding_id)
        if not isinstance(finding_type, str) or finding_type not in gate_definitions:
            errors.append(f"{label}.type неизвестен.")
            continue
        if not valid_text(root_cause):
            errors.append(f"{label}.root_cause обязателен.")
            normalized_cause = ""
        else:
            normalized_cause = re.sub(r"\s+", " ", root_cause.casefold()).strip()
            if normalized_cause in root_causes:
                errors.append(f"{label}.root_cause уже зарегистрирован.")
            root_causes.add(normalized_cause)
        if not valid_text(summary):
            errors.append(f"{label}.summary обязателен.")
        evidence_errors, evidence = validate_evidence(
            evidence=finding.get("evidence"),
            label=f"{label}.evidence",
            unit_paths=unit_paths,
        )
        errors.extend(evidence_errors)
        gate = gate_definitions[finding_type]
        if len(evidence) < gate["minimum_evidence"]:
            errors.append(f"{label}.evidence требует минимум {gate['minimum_evidence']} уникальных локатора.")
        if finding_type in {
            "critical_authority_conflict",
            "operational_contradiction",
            "material_primary_source_mismatch",
        }:
            if {item["kind"] for item in evidence if isinstance(item.get("kind"), str)} != {"conflict"}:
                errors.append(f"{label}: противоречие требует только conflict evidence.")
        elif not {
            item["kind"] for item in evidence if isinstance(item.get("kind"), str)
        } <= {"gap", "conflict"}:
            errors.append(f"{label}: системный cap требует gap/conflict evidence.")
        caps.append(gate["cap"])
        finding_results.append(
            {
                "id": finding_id,
                "type": finding_type,
                "name": gate["name"],
                "root_cause": root_cause,
                "summary": summary,
                "cap": gate["cap"],
                "evidence": evidence,
            }
        )

    errors.extend(validate_string_list(review.get("strengths"), "strengths", 1, 5))
    errors.extend(validate_string_list(review.get("recommendations"), "recommendations", 1, 5))
    errors.extend(validate_string_list(review.get("limitations"), "limitations", 0, None))
    if errors:
        raise ReviewError("\n".join(f"- {error}" for error in errors))

    dimension_results: list[dict[str, Any]] = []
    raw_minimum = 0.0
    raw_maximum = 0.0
    for dimension in rubric["dimensions"]:
        results = [criterion_results[item["id"]] for item in dimension["criteria"]]
        minimum = round(sum(item["minimum"] for item in results), 1)
        maximum = round(sum(item["maximum"] for item in results), 1)
        weight = round(sum(item["points"] for item in results), 1)
        raw_minimum += minimum
        raw_maximum += maximum
        dimension_results.append(
            {
                "id": dimension["id"],
                "name": dimension["name"],
                "minimum": minimum,
                "maximum": maximum,
                "weight": weight,
                "criteria": results,
            }
        )
    raw_minimum = round(raw_minimum, 1)
    raw_maximum = round(raw_maximum, 1)
    applied_cap = min(caps) if caps else 100
    confirmed = round(min(raw_minimum, applied_cap), 1)
    potential = round(min(raw_maximum, applied_cap), 1)
    unknown_points = round(raw_maximum - raw_minimum, 1)
    medium_limit = rubric["calculation"]["confidence"]["medium_maximum_unknown_points"]
    confidence = "high" if unknown_points == 0 else "medium" if unknown_points <= medium_limit else "low"

    def level_for(score: float) -> str:
        return next(level["name"] for level in rubric["levels"] if score >= level["minimum"])

    level_minimum = level_for(confirmed)
    level_maximum = level_for(potential)
    return {
        "schema_version": "2.0",
        "scope": scope,
        "scope_verification": "verified" if root is not None else "unverified",
        "confirmed_score": confirmed,
        "potential_score": potential,
        "raw_minimum": raw_minimum,
        "raw_maximum": raw_maximum,
        "unknown_points": unknown_points,
        "confidence": confidence,
        "level": level_minimum if level_minimum == level_maximum else "неопределённая",
        "level_minimum": level_minimum,
        "level_maximum": level_maximum,
        "cap": applied_cap,
        "dimensions": dimension_results,
        "systemic_findings": finding_results,
        "strengths": review["strengths"],
        "recommendations": review["recommendations"],
        "limitations": review["limitations"],
    }, resolved_files


def format_number(value: float | int) -> str:
    numeric = float(value)
    return str(int(numeric)) if numeric.is_integer() else f"{numeric:.1f}"


def render_markdown(result: dict[str, Any]) -> str:
    minimum = format_number(result["confirmed_score"])
    maximum = format_number(result["potential_score"])
    score_text = minimum if minimum == maximum else f"{minimum}–{maximum}"
    confidence_labels = {"high": "высокая", "medium": "средняя", "low": "низкая"}
    lines = [
        f"# Итог: {score_text}/100 — {result['level']} — "
        f"{confidence_labels[result['confidence']]} уверенность",
        "",
        "## Область",
        "",
        f"Цель: {result['scope']['purpose']}",
        "",
        f"Версия: `{result['scope']['version']}`",
        "",
        f"Scope digest: `{result['scope']['digest']}`; files: `{result['scope_verification']}`.",
        "",
        "| Путь | Роль | Профиль |",
        "|---|---|---|",
    ]
    for unit in result["scope"]["units"]:
        lines.append(
            f"| `{unit['path']}` (`{unit['content_sha256'][:12]}…`) | "
            f"{unit['role']} | {unit['profile']} |"
        )
    lines.extend(["", "Исключения: " + ("; ".join(result["scope"]["excluded"]) or "нет")])
    lines.extend(
        [
            "",
            "## Измерения",
            "",
            "| Измерение | Подтверждено | Потенциально | Максимум |",
            "|---|---:|---:|---:|",
        ]
    )
    for dimension in result["dimensions"]:
        lines.append(
            f"| {dimension['name']} | {format_number(dimension['minimum'])} | "
            f"{format_number(dimension['maximum'])} | {format_number(dimension['weight'])} |"
        )
    lines.extend(
        [
            "",
            "## Расчёт",
            "",
            f"До cap: {format_number(result['raw_minimum'])}–{format_number(result['raw_maximum'])}; "
            f"неизвестно: {format_number(result['unknown_points'])}; cap: {result['cap']}; "
            f"итог: {score_text}.",
            "",
            "## Системные ограничения",
            "",
        ]
    )
    if not result["systemic_findings"]:
        lines.append("Не зарегистрированы.")
    else:
        for finding in result["systemic_findings"]:
            lines.append(f"- **{finding['name']}** (`{finding['id']}`, cap {finding['cap']}): {finding['summary']}")
            for evidence in finding["evidence"]:
                lines.append(
                    f"  - `{evidence['path']}` — `{evidence['locator']}`: {evidence['note']}"
                )
    lines.extend(["", "## Критерии и evidence", ""])
    for dimension in result["dimensions"]:
        lines.append(f"### {dimension['name']}")
        lines.append("")
        for criterion in dimension["criteria"]:
            lines.append(
                f"- **{criterion['name']}** — `{criterion['status']}`, "
                f"{format_number(criterion['minimum'])}–{format_number(criterion['maximum'])}/{format_number(criterion['points'])}. "
                f"{criterion['rationale']}"
            )
            for evidence in criterion["evidence"]:
                lines.append(
                    f"  - `{evidence['kind']}` · `{evidence['path']}` · `{evidence['locator']}`: {evidence['note']}"
                )
        lines.append("")
    lines.extend(["## Сильные стороны", ""] + [f"- {item}" for item in result["strengths"]])
    lines.extend(["", "## Приоритетные улучшения", ""] + [f"- {item}" for item in result["recommendations"]])
    lines.extend(["", "## Ограничения проверки", ""])
    lines.extend([f"- {item}" for item in result["limitations"]] or ["Нет."])
    return "\n".join(lines) + "\n"


def write_exclusive(path: Path, content: str, protected: set[Path]) -> None:
    output = path.expanduser().absolute()
    if output in protected or output.resolve(strict=False) in protected:
        raise ReviewError("Файл вывода совпадает с входным файлом.")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(output, flags, 0o600)
    except OSError as exc:
        raise ReviewError(f"Нельзя создать файл вывода без перезаписи: {exc}") from exc
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(content)


def threshold_value(raw: str) -> float:
    try:
        value = float(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("порог должен быть числом") from exc
    if not math.isfinite(value) or not 0 <= value <= 100:
        raise argparse.ArgumentTypeError("порог должен быть конечным числом 0..100")
    return value


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Проверить evidence и рассчитать диапазон 0–100.")
    parser.add_argument("review", nargs="?", help="JSON экспертной оценки")
    parser.add_argument("--template", action="store_true", help="Вывести полный шаблон оценки")
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    parser.add_argument("--output", help="Создать новый файл; существующий файл не перезаписывается")
    parser.add_argument("--fail-below", type=threshold_value, help="Gate по подтверждённой границе")
    parser.add_argument("--root", help="Корень scope для обязательной сверки content hashes")
    parser.add_argument("--purpose", help="Цель для manifest template")
    parser.add_argument("--version", help="Версия для manifest template")
    parser.add_argument(
        "--unit",
        nargs=3,
        action="append",
        metavar=("PATH", "ROLE", "PROFILE"),
        help="Добавить unit в manifest template; можно повторять",
    )
    parser.add_argument("--exclude", action="append", default=[], help="Исключение для manifest template")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        rubric = load_json(RUBRIC_PATH)
        schema = load_json(SCHEMA_PATH)
        if not isinstance(rubric, dict) or not isinstance(schema, dict):
            raise ReviewError("Рубрика и схема должны быть JSON-объектами.")
        validate_rubric(rubric, schema)
        protected = {RUBRIC_PATH.resolve(), SCHEMA_PATH.resolve()}
        root = resolved_scope_root(args.root) if args.root else None
        if args.template:
            if args.review:
                raise ReviewError("Не указывайте review вместе с --template.")
            if args.fail_below is not None:
                raise ReviewError("--fail-below нельзя использовать с --template.")
            manifest_args = [args.root, args.purpose, args.version, args.unit]
            if any(value is not None for value in manifest_args):
                if not all(value is not None for value in manifest_args):
                    raise ReviewError(
                        "Manifest template требует --root, --purpose, --version и минимум один --unit."
                    )
                scope = build_scope_manifest(
                    args.root,
                    args.purpose,
                    args.version,
                    args.unit,
                    args.exclude,
                )
                output_data = review_template(rubric, scope)
                protected.update(verify_scope_files(root, scope["units"]))
            else:
                if args.exclude:
                    raise ReviewError("--exclude допустим только для manifest template.")
                output_data = review_template(rubric)
            result = None
        else:
            if not args.review:
                raise ReviewError("Укажите review.json или используйте --template.")
            if args.purpose or args.version or args.unit or args.exclude:
                raise ReviewError("--purpose/--version/--unit/--exclude допустимы только с --template.")
            review_path = Path(args.review).expanduser().resolve()
            protected.add(review_path)
            review = load_json(review_path)
            if not isinstance(review, dict):
                raise ReviewError("Экспертная оценка должна быть JSON-объектом.")
            result, scope_files = validate_and_calculate(review, rubric, root)
            protected.update(scope_files)
            output_data = result
        output = (
            render_markdown(result)
            if result is not None and args.format == "markdown"
            else json.dumps(output_data, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        )
        if args.output:
            write_exclusive(Path(args.output), output, protected)
        else:
            sys.stdout.write(output)
    except (ReviewError, TypeError, KeyError) as exc:
        print(f"Ошибка оценки:\n{exc}", file=sys.stderr)
        return 2
    if result is not None and args.fail_below is not None:
        if result["scope_verification"] != "verified":
            print("Gate отклонён: scope hashes не сверены; укажите --root.", file=sys.stderr)
            return 4
        if result["confidence"] == "low":
            print("Gate отклонён: низкая уверенность.", file=sys.stderr)
            return 4
        if result["confirmed_score"] < args.fail_below:
            return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
