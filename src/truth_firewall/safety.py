"""Bounds for untrusted files and paths."""

from __future__ import annotations

from pathlib import Path

from truth_firewall.constants import MAX_EVIDENCE_FILE_BYTES, MAX_TEXT_FIELD_CHARS
from truth_firewall.errors import EvidenceError, UnsafePathError

SECRET_NAMES = {
    ".env",
    ".env.local",
    ".env.production",
    "credentials.json",
    "id_rsa",
    "secrets.json",
}
SECRET_SUFFIXES = {".pem", ".key", ".p12", ".pfx"}


def is_secret_path(path: Path) -> bool:
    name = path.name.lower()
    if name in SECRET_NAMES or name.startswith(".env"):
        return True
    return path.suffix.lower() in SECRET_SUFFIXES


def is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def resolve_under_root(root: Path, user_path: str) -> Path:
    if "\x00" in user_path:
        raise UnsafePathError("path contains a null byte")
    base = root.resolve()
    raw = Path(user_path)
    candidate = raw if raw.is_absolute() else base / raw
    try:
        resolved = candidate.resolve(strict=False)
    except OSError as exc:
        raise UnsafePathError(str(exc)) from exc
    if not is_relative_to(resolved, base):
        raise UnsafePathError(f"path escapes the allowed root: {user_path}")
    return resolved


def read_bounded_bytes(path: Path, *, limit: int = MAX_EVIDENCE_FILE_BYTES) -> bytes:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise EvidenceError(f"cannot stat evidence file: {path.name}") from exc
    if size > limit:
        raise EvidenceError(
            f"evidence file {path.name} is {size} bytes; limit is {limit}"
        )
    data = path.read_bytes()
    if b"\x00" in data:
        raise EvidenceError(f"evidence file {path.name} looks binary and was refused")
    return data


def truncate_text(value: str, limit: int = MAX_TEXT_FIELD_CHARS) -> tuple[str, bool]:
    if len(value) <= limit:
        return value, False
    return value[:limit] + "\n[truncated]", True
