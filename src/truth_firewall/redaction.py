"""Best-effort redaction of obvious secrets before a provider call."""

from __future__ import annotations

import re
from typing import Any

_TOKEN_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9]{10,}"),
    re.compile(r"sk-proj-[A-Za-z0-9_-]{10,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"Bearer\s+[A-Za-z0-9._\-]{8,}", re.IGNORECASE),
)
_ASSIGNMENT = re.compile(
    r"(?i)\b(api[_-]?key|secret|password|passwd|token|authorization)\b(\s*[:=]\s*)(\S+)"
)


def redact_text(value: str) -> str:
    redacted = _ASSIGNMENT.sub(r"\1\2[REDACTED]", value)
    for pattern in _TOKEN_PATTERNS:
        redacted = pattern.sub("[REDACTED]", redacted)
    return redacted


def redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): redact_value(item) for key, item in value.items()}
    return value
