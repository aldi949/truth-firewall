"""Bounded local storage anchored to an explicitly permitted directory.

Every existing descendant component is checked, including the final file. Storage
links (symlinks, Windows reparse points and hard-linked files) are rejected rather
than treated as aliases. Checks are repeated at access time and O_NOFOLLOW is used
where available. This is not a sandbox against an actively compromised OS.
"""

from __future__ import annotations

import hashlib
import os
import re
import stat
from pathlib import Path

from truth_firewall.errors import UnsafePathError

MAX_STORAGE_BYTES = 4_000_000
_RESERVED = re.compile(r"(?i)^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)")


def safe_component(value: object, *, limit: int = 120) -> str:
    """Keep ordinary IDs readable; encode unsafe IDs without sanitization aliases."""
    if not isinstance(value, str) or not value.strip():
        return ""
    if (
        len(value) <= limit
        and re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9_.-]*", value)
        and not value.endswith((".", " "))
        and ".." not in value
        and not _RESERVED.match(value)
    ):
        return value
    return "id-" + hashlib.sha256(value.encode("utf-8", errors="surrogatepass")).hexdigest()


def checked_path(root: Path, path: Path) -> Path:
    """Resolve the boundary and reject traversal and links below that boundary."""
    try:
        lexical_base = Path(os.path.abspath(root))
        base = root.resolve(strict=True)
        candidate = Path(os.path.abspath(path if path.is_absolute() else lexical_base / path))
        try:
            relative = candidate.relative_to(lexical_base)
        except ValueError:
            relative = candidate.relative_to(base)
        resolved = candidate.resolve(strict=False)
        resolved.relative_to(base)
        current = base
        for part in relative.parts:
            current = current / part
            try:
                info = current.lstat()
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise UnsafePathError("Linked storage paths are not permitted")
            if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
                raise UnsafePathError("Hard-linked storage files are not permitted")
            if not stat.S_ISREG(info.st_mode) and not stat.S_ISDIR(info.st_mode):
                raise UnsafePathError("Storage path is not a regular file or directory")
        return base / relative
    except (OSError, RuntimeError, ValueError) as exc:
        raise UnsafePathError("Storage path cannot be safely contained within its permitted root") from exc


def ensure_directory(root: Path, path: Path, *, exclusive: bool = False) -> Path:
    candidate = checked_path(root, path)
    parent = root.resolve(strict=True)
    parts = candidate.relative_to(parent).parts
    for index, part in enumerate(parts):
        parent = parent / part
        checked_path(root, parent)
        last = index == len(parts) - 1
        try:
            parent.mkdir(exist_ok=not (last and exclusive))
        except FileExistsError:
            if last and exclusive:
                raise
        checked_path(root, parent)
        if not parent.is_dir():
            raise UnsafePathError("Storage parent is not a directory")
    if exclusive and not parts:
        raise FileExistsError("Exclusive storage directory already exists")
    return checked_path(root, candidate)


def ensure_root(root: Path) -> Path:
    """Create an explicitly configured output root, checking from its existing ancestor."""
    absolute = Path(os.path.abspath(root))
    ancestor = absolute
    while not ancestor.exists():
        ancestor = ancestor.parent
    return ensure_directory(ancestor, absolute)


def _open(root: Path, path: Path, flags: int) -> int:
    candidate = checked_path(root, path)
    descriptor = os.open(candidate, flags | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise UnsafePathError("Storage file must be an unlinked regular file")
        checked_path(root, candidate)
        current = candidate.stat()
        if (info.st_dev, info.st_ino) != (current.st_dev, current.st_ino):
            raise UnsafePathError("Storage file changed during access")
    except Exception:
        os.close(descriptor)
        raise
    return descriptor


def write_text(root: Path, path: Path, text: str, *, append: bool = False) -> None:
    encoded = text.encode("utf-8")
    if len(encoded) > MAX_STORAGE_BYTES:
        raise UnsafePathError("Storage text exceeds the size limit")
    ensure_directory(root, path.parent)
    flags = os.O_WRONLY | os.O_CREAT | (os.O_APPEND if append else 0)
    descriptor = _open(root, path, flags)
    with os.fdopen(descriptor, "wb") as handle:
        if append:
            if os.fstat(handle.fileno()).st_size + len(encoded) > MAX_STORAGE_BYTES:
                raise UnsafePathError("Storage file exceeds the size limit")
        else:
            handle.truncate(0)
        handle.write(encoded)


def read_text(root: Path, path: Path) -> str:
    descriptor = _open(root, path, os.O_RDONLY)
    with os.fdopen(descriptor, "rb") as handle:
        if os.fstat(handle.fileno()).st_size > MAX_STORAGE_BYTES:
            raise UnsafePathError("Storage file exceeds the size limit")
        value = handle.read(MAX_STORAGE_BYTES + 1)
    if len(value) > MAX_STORAGE_BYTES:
        raise UnsafePathError("Storage file exceeds the size limit")
    return value.decode("utf-8")
