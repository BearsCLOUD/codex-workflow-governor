"""Shared credential redaction for the local MCP adapter and workflow CLI."""

from __future__ import annotations

import re
from typing import Any


_CREDENTIAL_KEY = re.compile(
    r"(?i)^(?:.*[_-])?(?:access[_-]?token|api[_-]?key|authorization|credential|"
    r"access[_-]?key|password|private[_-]?key|proxy[_-]?authorization|secret|"
    r"secret[_-]?key|token)$"
)
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)(?<![A-Za-z0-9_])([\"']?(?:aws[_-]?secret[_-]?access[_-]?key|token|"
    r"password|secret|authorization|proxy-authorization|api[_-]?key|access[_-]?key|"
    r"credential|private[_-]?key)[\"']?)"
    r"\s*[:=]\s*(?:\"[^\"\r\n]*\"|'[^'\r\n]*'|[^\r\n,;]+)"
)
_STANDALONE_SECRET = re.compile(
    r"\b(?:"
    r"gh[opusr]_[A-Za-z0-9_]{20,}|"
    r"github_pat_[A-Za-z0-9_]{20,}|"
    r"sk-[A-Za-z0-9_-]{20,}|"
    r"xox[baprs]-[A-Za-z0-9-]{10,}|"
    r"glpat-[A-Za-z0-9_-]{20,}|"
    r"(?:AKIA|ASIA)[A-Z0-9]{16}"
    r")\b"
)


def redact_text(value: str) -> str:
    value = _SECRET_ASSIGNMENT.sub(lambda match: f"{match.group(1)}=[REDACTED]", value)
    return _STANDALONE_SECRET.sub("[REDACTED]", value)


def redact_value(value: Any, *, key: str | None = None) -> Any:
    if key is not None and _CREDENTIAL_KEY.fullmatch(key):
        return "[REDACTED]"
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    if isinstance(value, dict):
        return {str(item_key): redact_value(item, key=str(item_key)) for item_key, item in value.items()}
    return value
