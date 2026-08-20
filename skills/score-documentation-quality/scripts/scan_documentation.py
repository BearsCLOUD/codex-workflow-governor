#!/usr/bin/env python3
"""Collect deterministic, non-semantic evidence from Markdown documentation."""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import unquote


SUPPORTED_SUFFIXES = {".md", ".mdx"}
DEFAULT_EXCLUDED_DIRS = {
    ".git",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "vendor",
    "venv",
}
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)(?:\s+#+)?\s*$")
LINK_RE = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
TODO_RE = re.compile(
    r"(?<!\w)(?:TODO|FIXME|TBD|XXX|ДОПИСАТЬ|УТОЧНИТЬ)(?!\w)",
    re.IGNORECASE,
)
NORMATIVE_RE = re.compile(
    r"\b(?:must|should|required|never|always|shall|нужно|следует|должен|должна|"
    r"должны|обязательно|нельзя|никогда|всегда)\b",
    re.IGNORECASE,
)
ABSOLUTE_USER_PATH_RE = re.compile(r"(?<![\w:])/(?:home|Users|root)/[^\s`'\"()<>]+")
LIST_PREFIX_RE = re.compile(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)")
FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})")
SETEXT_RE = re.compile(r"^\s*(=+|-+)\s*$")


def slugify_heading(value: str) -> str:
    value = re.sub(r"[`*_~]", "", value.strip().lower())
    value = re.sub(r"[^\w\-\s]", "", value, flags=re.UNICODE)
    return re.sub(r"[\s\-]+", "-", value).strip("-")


def normalize_normative(value: str) -> str:
    value = LIST_PREFIX_RE.sub("", value)
    value = value.casefold()
    value = re.sub(r"[`*_~]", "", value)
    value = re.sub(r"[^\w\s]", " ", value, flags=re.UNICODE)
    return re.sub(r"\s+", " ", value).strip()


def is_excluded(path: Path, root: Path, patterns: list[str]) -> bool:
    try:
        relative = path.relative_to(root)
    except ValueError:
        relative = path
    if any(part in DEFAULT_EXCLUDED_DIRS for part in relative.parts):
        return True
    portable = relative.as_posix()
    return any(fnmatch.fnmatch(portable, pattern) for pattern in patterns)


def discover_files(inputs: Iterable[str], patterns: list[str]) -> tuple[Path, list[Path]]:
    lexical = [Path(item).expanduser().absolute() for item in inputs]
    symlinks = [
        str(path)
        for path in lexical
        if any(component.is_symlink() for component in (path, *path.parents))
    ]
    if symlinks:
        raise ValueError("Символические ссылки нельзя включать в область: " + ", ".join(symlinks))
    resolved = [path.resolve() for path in lexical]
    missing = [str(path) for path in resolved if not path.exists()]
    if missing:
        raise ValueError("Не найдены пути: " + ", ".join(missing))

    roots = [path if path.is_dir() else path.parent for path in resolved]
    root = Path(os.path.commonpath([str(path) for path in roots])).resolve()
    files: set[Path] = set()
    for path in resolved:
        if path.is_file():
            if path.suffix.lower() in SUPPORTED_SUFFIXES and not is_excluded(path, root, patterns):
                files.add(path)
            continue
        for candidate in path.rglob("*"):
            if candidate.is_symlink():
                continue
            if (
                candidate.is_file()
                and candidate.suffix.lower() in SUPPORTED_SUFFIXES
                and not is_excluded(candidate, root, patterns)
            ):
                files.add(candidate.resolve())
    if not files:
        raise ValueError("В заданной области нет файлов .md или .mdx")
    return root, sorted(files)


def display_path(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def split_link_target(raw: str) -> tuple[str, str]:
    target = raw.strip()
    if target.startswith("<") and ">" in target:
        target = target[1 : target.index(">")]
    else:
        target = target.split(maxsplit=1)[0]
    target = unquote(target)
    if "#" in target:
        path_part, anchor = target.split("#", 1)
        return path_part, anchor
    return target, ""


def add_finding(
    findings: list[dict[str, Any]],
    severity: str,
    code: str,
    path: str,
    line: int,
    message: str,
) -> None:
    findings.append(
        {
            "severity": severity,
            "code": code,
            "path": path,
            "line": line,
            "message": message,
        }
    )


def scan(root: Path, files: list[Path]) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    file_reports: list[dict[str, Any]] = []
    anchors_by_file: dict[Path, set[str]] = {}
    normative: dict[str, list[tuple[str, int, str]]] = defaultdict(list)
    link_records: list[tuple[Path, str, int]] = []

    for path in files:
        relative = display_path(path, root)
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            add_finding(findings, "error", "non-utf8", relative, 1, "Файл не читается как UTF-8.")
            continue
        lines = text.splitlines()
        headings: list[tuple[int, int, str]] = []
        links = 0
        todos = 0
        absolute_paths = 0

        if not text.strip():
            add_finding(findings, "error", "empty-file", relative, 1, "Файл пуст.")

        previous_level = 0
        h1_lines: list[int] = []
        anchors: set[str] = set()
        anchor_counts: dict[str, int] = defaultdict(int)
        active_fence: str | None = None
        for number, line in enumerate(lines, start=1):
            fence = FENCE_RE.match(line)
            if fence:
                marker = fence.group(1)[0]
                active_fence = None if active_fence == marker else marker
                continue
            if active_fence:
                continue

            heading = HEADING_RE.match(line)
            setext = SETEXT_RE.match(line)
            if not heading and setext and number > 1 and lines[number - 2].strip():
                level = 1 if setext.group(1).startswith("=") else 2
                title = lines[number - 2].strip()
                heading_data = (level, title, number - 1)
            elif heading:
                heading_data = (len(heading.group(1)), heading.group(2).strip(), number)
            else:
                heading_data = None
            if heading_data:
                level, title, heading_line = heading_data
                headings.append((heading_line, level, title))
                base_anchor = slugify_heading(title)
                occurrence = anchor_counts[base_anchor]
                anchors.add(base_anchor if occurrence == 0 else f"{base_anchor}-{occurrence}")
                anchor_counts[base_anchor] += 1
                if level == 1:
                    h1_lines.append(heading_line)
                if previous_level and level > previous_level + 1:
                    add_finding(
                        findings,
                        "warning",
                        "heading-level-jump",
                        relative,
                        heading_line,
                        f"Уровень заголовка перескакивает с H{previous_level} на H{level}.",
                    )
                previous_level = level

            for raw_target in LINK_RE.findall(line):
                links += 1
                link_records.append((path, raw_target, number))

            visible_line = re.sub(r"`[^`]*`", "", line)
            if TODO_RE.search(visible_line):
                todos += 1
                add_finding(
                    findings,
                    "info",
                    "unfinished-marker",
                    relative,
                    number,
                    "Найден маркер незавершённости; проверить его влияние на полноту.",
                )

            for match in ABSOLUTE_USER_PATH_RE.finditer(line):
                absolute_paths += 1
                add_finding(
                    findings,
                    "info",
                    "environment-specific-path",
                    relative,
                    number,
                    f"Путь {match.group(0)!r} зависит от окружения; проверить переносимость.",
                )

            normalized = normalize_normative(visible_line)
            if len(normalized) >= 30 and NORMATIVE_RE.search(visible_line):
                normative[normalized].append((relative, number, visible_line.strip()))

        if not h1_lines:
            add_finding(findings, "warning", "missing-h1", relative, 1, "Нет заголовка H1.")
        elif len(h1_lines) > 1:
            add_finding(
                findings,
                "warning",
                "multiple-h1",
                relative,
                h1_lines[1],
                f"Найдено заголовков H1: {len(h1_lines)}.",
            )
        anchors_by_file[path] = anchors
        file_reports.append(
            {
                "path": relative,
                "lines": len(lines),
                "headings": len(headings),
                "links": links,
                "unfinished_markers": todos,
                "environment_specific_paths": absolute_paths,
            }
        )

    file_set = set(files)
    for source_path, raw_target, line in link_records:
        path_part, anchor = split_link_target(raw_target)
        lower = path_part.lower()
        if lower.startswith(("http://", "https://", "mailto:", "tel:", "data:")):
            continue
        target_path = source_path if not path_part else (source_path.parent / path_part).resolve()
        relative = display_path(source_path, root)
        if path_part and not target_path.exists():
            add_finding(
                findings,
                "error",
                "broken-relative-link",
                relative,
                line,
                f"Цель относительной ссылки не существует: {path_part}",
            )
            continue
        if (
            anchor
            and target_path in file_set
            and target_path.is_file()
            and target_path.suffix.lower() in SUPPORTED_SUFFIXES
        ):
            anchors = anchors_by_file.get(target_path)
            if anchors is None:
                anchors = set()
            if anchor.casefold() not in {item.casefold() for item in anchors}:
                add_finding(
                    findings,
                    "warning",
                    "missing-anchor",
                    relative,
                    line,
                    f"Якорь ссылки не найден: #{anchor}",
                )

    duplicate_groups = 0
    for occurrences in normative.values():
        unique_files = {item[0] for item in occurrences}
        if len(unique_files) < 2:
            continue
        duplicate_groups += 1
        locations = ", ".join(f"{path}:{line}" for path, line, _ in occurrences[:5])
        first_path, first_line, _ = occurrences[0]
        add_finding(
            findings,
            "info",
            "duplicate-normative-statement",
            first_path,
            first_line,
            "Повтор нормативного утверждения; проверить источник истины и синхронизацию: " + locations,
        )

    severity_order = {"error": 0, "warning": 1, "info": 2}
    findings.sort(key=lambda item: (severity_order[item["severity"]], item["path"], item["line"], item["code"]))
    return {
        "schema_version": "1.0",
        "root": root.as_posix(),
        "files": file_reports,
        "metrics": {
            "file_count": len(file_reports),
            "line_count": sum(item["lines"] for item in file_reports),
            "finding_count": len(findings),
            "errors": sum(item["severity"] == "error" for item in findings),
            "warnings": sum(item["severity"] == "warning" for item in findings),
            "info": sum(item["severity"] == "info" for item in findings),
            "duplicate_normative_groups": duplicate_groups,
        },
        "findings": findings,
        "interpretation": "Наблюдаемые признаки и кандидаты для ручной проверки; не итоговый балл.",
    }


def render_markdown(report: dict[str, Any]) -> str:
    metrics = report["metrics"]
    lines = [
        "# Статический обзор документации",
        "",
        f"Файлов: {metrics['file_count']}; строк: {metrics['line_count']}; "
        f"ошибок: {metrics['errors']}; предупреждений: {metrics['warnings']}; "
        f"информационных кандидатов: {metrics['info']}.",
        "",
        "> Эти признаки требуют смысловой проверки и не являются итоговым баллом.",
        "",
        "## Наблюдения",
        "",
    ]
    if not report["findings"]:
        lines.append("Наблюдаемых структурных проблем не найдено.")
    else:
        lines.extend(["| Уровень | Код | Место | Наблюдение |", "|---|---|---|---|"])
        for finding in report["findings"]:
            message = finding["message"].replace("|", "\\|")
            lines.append(
                f"| {finding['severity']} | `{finding['code']}` | "
                f"`{finding['path']}:{finding['line']}` | {message} |"
            )
    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Собрать проверяемые структурные признаки из Markdown-документации."
    )
    parser.add_argument("paths", nargs="+", help="Файлы или каталоги .md/.mdx")
    parser.add_argument("--exclude", action="append", default=[], help="Glob относительно общей области")
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    parser.add_argument("--output", help="Файл вывода; по умолчанию stdout")
    return parser.parse_args()


def write_exclusive(path: Path, content: str, protected: set[Path]) -> None:
    output = path.expanduser().absolute()
    if output in protected or output.resolve(strict=False) in protected:
        raise ValueError("Файл вывода совпадает с входным документом.")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(output, flags, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(content)


def main() -> int:
    args = parse_args()
    try:
        root, files = discover_files(args.paths, args.exclude)
        report = scan(root, files)
    except (OSError, ValueError) as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 2
    output = json.dumps(report, ensure_ascii=False, indent=2) + "\n" if args.format == "json" else render_markdown(report)
    try:
        if args.output:
            write_exclusive(Path(args.output), output, set(files))
        else:
            sys.stdout.write(output)
    except (OSError, ValueError) as exc:
        print(f"Ошибка вывода: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
