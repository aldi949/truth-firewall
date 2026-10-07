"""Canonical workspace and path comparisons for evidence scope binding."""

from __future__ import annotations

import ntpath
import os
import posixpath
from pathlib import Path


def canonical_workspace_root(value: str | Path | None, *, allow_relative: bool = False) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if ntpath.splitdrive(text)[0] or text.startswith("\\\\"):
        if os.name == "nt":
            try:
                text = str(Path(text).resolve(strict=False))
            except (OSError, RuntimeError, ValueError):
                return None
        return ntpath.normcase(ntpath.normpath(text.replace("/", "\\")))
    if posixpath.isabs(text):
        return posixpath.normpath(text)
    if not allow_relative:
        return None
    try:
        return os.path.normcase(str(Path(text).expanduser().resolve()))
    except (OSError, RuntimeError, ValueError):
        return None


def same_workspace(first: str | None, second: str | None) -> bool:
    left = canonical_workspace_root(first)
    right = canonical_workspace_root(second)
    return left is not None and right is not None and left == right


def normalize_workspace_path(path: str | None, workspace_root: str | None) -> str | None:
    """Return a relative path only when it stays inside the specified workspace."""
    if not isinstance(path, str) or not path.strip() or not workspace_root:
        return None
    root = canonical_workspace_root(workspace_root)
    value = path.strip()
    if root is None:
        return None

    windows_root = bool(ntpath.splitdrive(root)[0] or root.startswith("\\\\"))
    if windows_root:
        root_norm = ntpath.normcase(ntpath.normpath(root))
        raw_candidate = value.replace("/", "\\")
        if not (ntpath.isabs(raw_candidate) or ntpath.splitdrive(raw_candidate)[0]):
            normalized_relative = posixpath.normpath(value.replace("\\", "/"))
            if normalized_relative == ".." or normalized_relative.startswith("../"):
                return None
        candidate = ntpath.normcase(ntpath.normpath(raw_candidate))
        if ntpath.isabs(candidate) or ntpath.splitdrive(candidate)[0]:
            try:
                if ntpath.commonpath((root_norm, candidate)) != root_norm:
                    return None
                relative = ntpath.relpath(candidate, root_norm)
            except ValueError:
                return None
            relative = relative.replace("\\", "/")
        else:
            relative = posixpath.normpath(value.replace("\\", "/"))
        if relative == ".":
            return ""
        if relative == ".." or relative.startswith("../"):
            return None
        return relative.casefold()

    root_norm = posixpath.normpath(root)
    candidate = value.replace("\\", "/")
    if posixpath.isabs(candidate):
        candidate_norm = posixpath.normpath(candidate)
        try:
            if posixpath.commonpath((root_norm, candidate_norm)) != root_norm:
                return None
            relative = posixpath.relpath(candidate_norm, root_norm)
        except ValueError:
            return None
    else:
        relative = posixpath.normpath(candidate)
    if relative == ".":
        return ""
    if relative == ".." or relative.startswith("../"):
        return None
    return relative
