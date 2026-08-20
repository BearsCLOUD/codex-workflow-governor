#!/usr/bin/env python3
"""Append, verify, and extract byte-exact non-authoritative archive records."""

from __future__ import annotations

import argparse
import base64
import binascii
import fcntl
import hashlib
import json
import os
import re
import secrets
import stat
import sys
from pathlib import Path, PurePosixPath
from typing import Any


SCHEMA_VERSION = "1.0"
SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
ID_RE = re.compile(r"^archive-[a-f0-9]{24}$")
CONTENT_KINDS = {"normative", "supporting", "mixed"}
RECORD_KINDS = {"file", "span"}
REQUIRED_FIELDS = {
    "schema_version", "id", "previous_record_sha256", "record_kind", "source_path",
    "locator", "start_byte", "end_byte", "byte_length", "content_kind",
    "content_sha256", "content_base64", "reason", "target", "status", "record_sha256",
}
HEAD_FIELDS = {
    "schema_version", "archive_path", "records", "archive_size", "archive_sha256",
    "tail_id", "tail_record_sha256",
}
SENSITIVE_PATTERNS = (
    (re.compile(rb"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----"), "private key"),
    (
        re.compile(
            rb"\b(?:sk-(?:proj-)?[A-Za-z0-9_-]{16,}|github_pat_[A-Za-z0-9_]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|(?:AKIA|ASIA)[A-Z0-9]{16}|xox[baprs]-[A-Za-z0-9-]{16,})\b"
        ),
        "credential token",
    ),
    (
        re.compile(
            rb"(?im)^\s*[A-Z0-9_]*(?:PASSWORD|TOKEN|SECRET|API_KEY|PRIVATE_KEY)[A-Z0-9_]*\s*[:=]\s*(?!\s*(?:<|\$\{|REDACTED\b|CHANGEME\b|EXAMPLE\b|DUMMY\b))\S{8,}"
        ),
        "secret assignment",
    ),
)


class ArchiveError(ValueError):
    pass


def reject_sensitive_content(content: bytes) -> None:
    """Fail closed instead of copying recognizable secrets into a repository archive."""

    findings = sorted({label for pattern, label in SENSITIVE_PATTERNS if pattern.search(content)})
    if findings:
        raise ArchiveError(
            "Source appears to contain sensitive material "
            f"({', '.join(findings)}); do not preserve it in the repository archive. "
            "Use an authorized secure location and review Git history before publication."
        )


def valid_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def safe_relative_path(value: Any) -> bool:
    if not valid_text(value):
        return False
    path = PurePosixPath(value)
    return (
        bool(path.parts)
        and value != "."
        and not path.is_absolute()
        and ".." not in path.parts
        and value == path.as_posix()
    )


def secure_flags(*, directory: bool = False) -> int:
    flags = os.O_CLOEXEC if hasattr(os, "O_CLOEXEC") else 0
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if directory and hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    return flags


def open_root(value: str) -> tuple[Path, int]:
    lexical = Path(value).expanduser().absolute()
    if any(component.is_symlink() for component in (lexical, *lexical.parents)):
        raise ArchiveError("--root не должен содержать symlink-компоненты.")
    try:
        root = lexical.resolve(strict=True)
    except FileNotFoundError as exc:
        raise ArchiveError("--root не найден.") from exc
    if not root.is_dir():
        raise ArchiveError("--root должен быть директорией.")
    try:
        descriptor = os.open(root, os.O_RDONLY | secure_flags(directory=True))
    except OSError as exc:
        raise ArchiveError(f"Нельзя безопасно открыть --root: {exc}.") from exc
    if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise ArchiveError("--root должен быть директорией.")
    return root, descriptor


def open_parent(root_fd: int, relative: str, *, create: bool) -> tuple[int, str]:
    if not safe_relative_path(relative):
        raise ArchiveError(f"Небезопасный относительный POSIX-путь: {relative!r}.")
    parts = PurePosixPath(relative).parts
    current = os.dup(root_fd)
    try:
        for part in parts[:-1]:
            if create:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=current)
                except FileExistsError:
                    pass
            try:
                child = os.open(
                    part,
                    os.O_RDONLY | secure_flags(directory=True),
                    dir_fd=current,
                )
            except OSError as exc:
                raise ArchiveError(f"Нельзя безопасно открыть parent для {relative}: {exc}.") from exc
            os.close(current)
            current = child
        return current, parts[-1]
    except Exception:
        os.close(current)
        raise


def assert_regular_single_link(descriptor: int, label: str) -> os.stat_result:
    metadata = os.fstat(descriptor)
    if not stat.S_ISREG(metadata.st_mode):
        raise ArchiveError(f"{label} должен быть обычным файлом.")
    if metadata.st_nlink != 1:
        raise ArchiveError(f"{label} с несколькими hard links запрещён.")
    return metadata


def assert_name_matches_fd(parent_fd: int, name: str, descriptor: int, label: str) -> None:
    try:
        named = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except OSError as exc:
        raise ArchiveError(f"{label} path изменился во время операции: {exc}.") from exc
    opened = assert_regular_single_link(descriptor, label)
    if not stat.S_ISREG(named.st_mode) or named.st_nlink != 1 or (
        named.st_dev, named.st_ino
    ) != (opened.st_dev, opened.st_ino):
        raise ArchiveError(f"{label} path изменился во время операции.")


def read_all(descriptor: int) -> bytes:
    os.lseek(descriptor, 0, os.SEEK_SET)
    chunks: list[bytes] = []
    while True:
        chunk = os.read(descriptor, 1024 * 1024)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)


def write_all(descriptor: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        written = os.write(descriptor, payload[offset:])
        if written <= 0:
            raise ArchiveError("Файловая запись завершилась без прогресса.")
        offset += written


def open_regular_at(parent_fd: int, name: str, flags: int, label: str, mode: int = 0o600) -> int:
    try:
        descriptor = os.open(name, flags | secure_flags(), mode, dir_fd=parent_fd)
    except OSError as exc:
        raise ArchiveError(f"Нельзя безопасно открыть {label}: {exc}.") from exc
    try:
        assert_regular_single_link(descriptor, label)
    except Exception:
        os.close(descriptor)
        raise
    return descriptor


def read_optional_at(parent_fd: int, name: str, label: str) -> bytes | None:
    try:
        descriptor = os.open(name, os.O_RDONLY | secure_flags(), dir_fd=parent_fd)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ArchiveError(f"Нельзя безопасно открыть {label}: {exc}.") from exc
    try:
        assert_regular_single_link(descriptor, label)
        return read_all(descriptor)
    finally:
        os.close(descriptor)


def identity_record_bytes(record: dict[str, Any]) -> bytes:
    payload = {key: value for key, value in record.items() if key not in {"id", "record_sha256"}}
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def canonical_record_bytes(record: dict[str, Any]) -> bytes:
    payload = {key: value for key, value in record.items() if key != "record_sha256"}
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def validate_record(record: Any, line_number: int) -> bytes:
    label = f"archive line {line_number}"
    if not isinstance(record, dict):
        raise ArchiveError(f"{label}: запись должна быть JSON-объектом.")
    missing = REQUIRED_FIELDS - set(record)
    extra = set(record) - REQUIRED_FIELDS
    if missing or extra:
        details = []
        if missing:
            details.append("нет " + ", ".join(sorted(missing)))
        if extra:
            details.append("лишние " + ", ".join(sorted(extra)))
        raise ArchiveError(f"{label}: {'; '.join(details)}.")
    if record.get("schema_version") != SCHEMA_VERSION:
        raise ArchiveError(f"{label}: schema_version должен быть {SCHEMA_VERSION}.")
    if not isinstance(record.get("id"), str) or not ID_RE.fullmatch(record["id"]):
        raise ArchiveError(f"{label}: id некорректен.")
    previous_hash = record.get("previous_record_sha256")
    if previous_hash is not None and (
        not isinstance(previous_hash, str) or not SHA256_RE.fullmatch(previous_hash)
    ):
        raise ArchiveError(f"{label}: previous_record_sha256 некорректен.")
    if record.get("record_kind") not in RECORD_KINDS:
        raise ArchiveError(f"{label}: record_kind неизвестен.")
    if not safe_relative_path(record.get("source_path")):
        raise ArchiveError(f"{label}: source_path небезопасен.")
    for field in ("locator", "reason", "target"):
        if not valid_text(record.get(field)):
            raise ArchiveError(f"{label}: {field} должен быть непустой строкой.")
    if record.get("content_kind") not in CONTENT_KINDS:
        raise ArchiveError(f"{label}: content_kind неизвестен.")
    if record.get("status") != "archived":
        raise ArchiveError(f"{label}: status должен быть archived.")
    start = record.get("start_byte")
    end = record.get("end_byte")
    length = record.get("byte_length")
    if (
        not isinstance(start, int)
        or isinstance(start, bool)
        or not isinstance(end, int)
        or isinstance(end, bool)
        or not isinstance(length, int)
        or isinstance(length, bool)
        or start < 0
        or end < start
        or (record.get("record_kind") == "span" and end == start)
        or length != end - start
    ):
        raise ArchiveError(f"{label}: byte offsets/length некорректны.")
    if record["record_kind"] == "file" and start != 0:
        raise ArchiveError(f"{label}: file record должен начинаться с byte 0.")
    try:
        decoded = base64.b64decode(record.get("content_base64", ""), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ArchiveError(f"{label}: content_base64 некорректен.") from exc
    if len(decoded) != length:
        raise ArchiveError(f"{label}: byte_length не совпадает с decoded content.")
    content_hash = record.get("content_sha256")
    if not isinstance(content_hash, str) or not SHA256_RE.fullmatch(content_hash):
        raise ArchiveError(f"{label}: content_sha256 некорректен.")
    if hashlib.sha256(decoded).hexdigest() != content_hash:
        raise ArchiveError(f"{label}: content_sha256 не совпадает.")
    expected_id = "archive-" + hashlib.sha256(identity_record_bytes(record)).hexdigest()[:24]
    if record["id"] != expected_id:
        raise ArchiveError(f"{label}: id не соответствует содержимому записи.")
    record_hash = record.get("record_sha256")
    if not isinstance(record_hash, str) or not SHA256_RE.fullmatch(record_hash):
        raise ArchiveError(f"{label}: record_sha256 некорректен.")
    if hashlib.sha256(canonical_record_bytes(record)).hexdigest() != record_hash:
        raise ArchiveError(f"{label}: record_sha256 не совпадает.")
    return decoded


def read_archive_bytes(raw: bytes) -> list[tuple[dict[str, Any], bytes, int, int, str]]:
    if not raw:
        return []
    if not raw.endswith(b"\n"):
        raise ArchiveError("Архив должен завершаться newline.")
    results: list[tuple[dict[str, Any], bytes, int, int, str]] = []
    ids: set[str] = set()
    offset = 0
    previous_hash: str | None = None
    for line_number, line_with_newline in enumerate(raw.splitlines(keepends=True), start=1):
        start = offset
        offset += len(line_with_newline)
        if not line_with_newline.endswith(b"\n") or not line_with_newline[:-1].strip():
            raise ArchiveError(f"archive line {line_number}: пустая/незавершённая строка.")
        try:
            record = json.loads(line_with_newline[:-1].decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ArchiveError(f"archive line {line_number}: некорректный UTF-8 JSON: {exc}.") from exc
        decoded = validate_record(record, line_number)
        if record["previous_record_sha256"] != previous_hash:
            raise ArchiveError(f"archive line {line_number}: нарушена hash chain.")
        if record["id"] in ids:
            raise ArchiveError(f"archive line {line_number}: дублируется id {record['id']}.")
        ids.add(record["id"])
        previous_hash = record["record_sha256"]
        results.append(
            (record, decoded, start, offset, hashlib.sha256(line_with_newline).hexdigest())
        )
    return results


def head_name(archive_name: str) -> str:
    return archive_name + ".head.json"


def make_head(
    archive_path: str,
    raw: bytes,
    records: list[tuple[dict[str, Any], bytes, int, int, str]],
) -> dict[str, Any]:
    if not records:
        raise ArchiveError("Пустой archive не может иметь verified head.")
    tail = records[-1][0]
    return {
        "schema_version": SCHEMA_VERSION,
        "archive_path": archive_path,
        "records": len(records),
        "archive_size": len(raw),
        "archive_sha256": hashlib.sha256(raw).hexdigest(),
        "tail_id": tail["id"],
        "tail_record_sha256": tail["record_sha256"],
    }


def verify_head(
    archive_path: str,
    raw: bytes,
    records: list[tuple[dict[str, Any], bytes, int, int, str]],
    head_raw: bytes | None,
) -> dict[str, Any]:
    if head_raw is None:
        raise ArchiveError("Отсутствует archive head; усечение нельзя исключить.")
    try:
        head = json.loads(head_raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ArchiveError(f"Archive head некорректен: {exc}.") from exc
    if not isinstance(head, dict) or set(head) != HEAD_FIELDS:
        raise ArchiveError("Archive head имеет неизвестную структуру.")
    expected = make_head(archive_path, raw, records)
    if head != expected:
        raise ArchiveError("Archive head не соответствует JSONL; возможно усечение или замена.")
    return head


def make_record(
    args: argparse.Namespace,
    content: bytes,
    start: int,
    end: int,
    previous_record_sha256: str | None,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "previous_record_sha256": previous_record_sha256,
        "record_kind": "file" if args.start_byte is None else "span",
        "source_path": args.source,
        "locator": args.locator,
        "start_byte": start,
        "end_byte": end,
        "byte_length": len(content),
        "content_kind": args.content_kind,
        "content_sha256": hashlib.sha256(content).hexdigest(),
        "content_base64": base64.b64encode(content).decode("ascii"),
        "reason": args.reason,
        "target": args.target,
        "status": "archived",
    }
    digest = hashlib.sha256(identity_record_bytes(record)).hexdigest()
    record["id"] = "archive-" + digest[:24]
    record["record_sha256"] = hashlib.sha256(canonical_record_bytes(record)).hexdigest()
    return record


def atomic_write_head(parent_fd: int, name: str, payload: bytes) -> None:
    temporary = f".{name}.tmp-{os.getpid()}-{secrets.token_hex(8)}"
    descriptor: int | None = None
    try:
        descriptor = open_regular_at(
            parent_fd,
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            "temporary archive head",
        )
        write_all(descriptor, payload)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        os.replace(temporary, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        os.fsync(parent_fd)
    except Exception:
        if descriptor is not None:
            os.close(descriptor)
        try:
            os.unlink(temporary, dir_fd=parent_fd)
        except FileNotFoundError:
            pass
        raise


def command_add(args: argparse.Namespace) -> dict[str, Any]:
    if args.source in {args.archive, args.archive + ".head.json"}:
        raise ArchiveError("Archive/head и source должны различаться.")
    _, root_fd = open_root(args.root)
    source_parent = archive_parent = source_fd = archive_fd = None
    try:
        source_parent, source_name = open_parent(root_fd, args.source, create=False)
        source_fd = open_regular_at(source_parent, source_name, os.O_RDONLY, "source")
        source_stat = os.fstat(source_fd)
        data = read_all(source_fd)
        if (args.start_byte is None) != (args.end_byte is None):
            raise ArchiveError("--start-byte и --end-byte задаются вместе.")
        start = 0 if args.start_byte is None else args.start_byte
        end = len(data) if args.end_byte is None else args.end_byte
        if start < 0 or end < start or end > len(data) or (args.start_byte is not None and end == start):
            raise ArchiveError("Byte range должен находиться внутри source; span должен быть непустым.")
        content = data[start:end]
        reject_sensitive_content(content)

        archive_parent, archive_name = open_parent(root_fd, args.archive, create=True)
        archive_fd = open_regular_at(
            archive_parent,
            archive_name,
            os.O_RDWR | os.O_APPEND | os.O_CREAT,
            "archive",
        )
        fcntl.flock(archive_fd, fcntl.LOCK_EX)
        assert_name_matches_fd(archive_parent, archive_name, archive_fd, "archive")
        archive_stat = os.fstat(archive_fd)
        if (archive_stat.st_dev, archive_stat.st_ino) == (source_stat.st_dev, source_stat.st_ino):
            raise ArchiveError("Archive и source указывают на один inode.")
        raw = read_all(archive_fd)
        records = read_archive_bytes(raw)
        existing_head = read_optional_at(archive_parent, head_name(archive_name), "archive head")
        if raw:
            verify_head(args.archive, raw, records, existing_head)
        elif existing_head is not None:
            raise ArchiveError("Head существует для пустого archive.")
        previous_hash = records[-1][0]["record_sha256"] if records else None
        record = make_record(args, content, start, end, previous_hash)
        if any(item[0]["id"] == record["id"] for item in records):
            raise ArchiveError(f"Такая запись уже существует: {record['id']}.")
        line = (
            json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        evidence_start = len(raw)
        os.lseek(archive_fd, 0, os.SEEK_END)
        write_all(archive_fd, line)
        os.fsync(archive_fd)
        assert_name_matches_fd(archive_parent, archive_name, archive_fd, "archive")
        combined = raw + line
        verified = read_archive_bytes(combined)
        if verified[-1][0]["id"] != record["id"] or verified[-1][1] != content:
            raise ArchiveError("Добавленная запись не прошла проверку восстановления.")
        head = make_head(args.archive, combined, verified)
        head_payload = (
            json.dumps(head, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        atomic_write_head(archive_parent, head_name(archive_name), head_payload)
        assert_name_matches_fd(archive_parent, archive_name, archive_fd, "archive")
        return {
            "status": "archived",
            "id": record["id"],
            "content_sha256": record["content_sha256"],
            "record_sha256": record["record_sha256"],
            "records": len(verified),
            "evidence_path": args.archive,
            "evidence_locator": record["id"],
            "evidence_start_byte": evidence_start,
            "evidence_end_byte": evidence_start + len(line),
            "evidence_sha256": hashlib.sha256(line).hexdigest(),
            "head_path": str(PurePosixPath(args.archive).with_name(head_name(archive_name))),
        }
    finally:
        for descriptor in (source_fd, source_parent, archive_fd, archive_parent, root_fd):
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass


def load_verified_archive(
    root_fd: int, archive_path: str
) -> tuple[list[tuple[dict[str, Any], bytes, int, int, str]], bytes, int, int, str]:
    parent_fd, name = open_parent(root_fd, archive_path, create=False)
    archive_fd: int | None = None
    try:
        archive_fd = open_regular_at(parent_fd, name, os.O_RDONLY, "archive")
        fcntl.flock(archive_fd, fcntl.LOCK_SH)
        assert_name_matches_fd(parent_fd, name, archive_fd, "archive")
        raw = read_all(archive_fd)
        records = read_archive_bytes(raw)
        if not records:
            raise ArchiveError("Archive пуст; lossless records отсутствуют.")
        head_raw = read_optional_at(parent_fd, head_name(name), "archive head")
        verify_head(archive_path, raw, records, head_raw)
        assert_name_matches_fd(parent_fd, name, archive_fd, "archive")
        return records, raw, parent_fd, archive_fd, name
    except Exception:
        if archive_fd is not None:
            os.close(archive_fd)
        os.close(parent_fd)
        raise


def command_verify(args: argparse.Namespace) -> dict[str, Any]:
    _, root_fd = open_root(args.root)
    parent_fd = archive_fd = None
    try:
        records, raw, parent_fd, archive_fd, _ = load_verified_archive(root_fd, args.archive)
        return {
            "status": "verified",
            "records": len(records),
            "archive_sha256": hashlib.sha256(raw).hexdigest(),
            "tail_id": records[-1][0]["id"],
        }
    finally:
        for descriptor in (archive_fd, parent_fd, root_fd):
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass


def command_extract(args: argparse.Namespace) -> dict[str, Any]:
    _, root_fd = open_root(args.root)
    archive_parent = archive_fd = output_parent = output_fd = None
    try:
        records, _, archive_parent, archive_fd, _ = load_verified_archive(root_fd, args.archive)
        matches = [item for item in records if item[0]["id"] == args.id]
        if len(matches) != 1:
            raise ArchiveError(f"Запись не найдена: {args.id}.")
        output_parent, output_name = open_parent(root_fd, args.output, create=True)
        output_fd = open_regular_at(
            output_parent,
            output_name,
            os.O_RDWR | os.O_CREAT | os.O_EXCL,
            "output",
        )
        write_all(output_fd, matches[0][1])
        os.fsync(output_fd)
        if read_all(output_fd) != matches[0][1]:
            raise ArchiveError("Извлечённые байты не совпадают с archive.")
        return {
            "status": "extracted",
            "id": args.id,
            "output": args.output,
            "content_sha256": hashlib.sha256(matches[0][1]).hexdigest(),
        }
    finally:
        for descriptor in (output_fd, output_parent, archive_fd, archive_parent, root_fd):
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Вести byte-exact архив удаляемых инструкций.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("add", "verify", "extract"):
        child = subparsers.add_parser(name)
        child.add_argument("--root", required=True, help="Корень документного контура")
        child.add_argument(
            "--archive",
            default="archive/instructions.jsonl",
            help="Безопасный относительный JSONL-путь",
        )
    add = subparsers.choices["add"]
    add.add_argument("--source", required=True, help="Относительный source path")
    add.add_argument("--locator", required=True)
    add.add_argument("--content-kind", choices=sorted(CONTENT_KINDS), required=True)
    add.add_argument("--reason", required=True)
    add.add_argument("--target", required=True)
    add.add_argument("--start-byte", type=int)
    add.add_argument("--end-byte", type=int)
    extract = subparsers.choices["extract"]
    extract.add_argument("--id", required=True)
    extract.add_argument("--output", required=True, help="Новый относительный output path")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.command == "add":
            result = command_add(args)
        elif args.command == "verify":
            result = command_verify(args)
        else:
            result = command_extract(args)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ArchiveError, OSError) as exc:
        print(f"Ошибка архива: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
