"""Process-local collector capabilities, not self-declared serialized trust.

The Python host and installed collectors are trusted. Imported JSON, hook payloads,
and assistant text are not. These seals authenticate records only in this process;
they are deliberately neither portable signatures nor a hostile-Python sandbox.
"""

from __future__ import annotations

import hashlib
import hmac
import itertools
import os
import secrets
import stat
from dataclasses import replace
from pathlib import Path

from truth_firewall.schemas import EvidenceRecord, json_dumps

_KEY = secrets.token_bytes(32)
_OBSERVATIONS = itertools.count(1)
_IGNORED = {".git", ".truth-firewall", "__pycache__", ".pytest_cache", ".ruff_cache", ".venv", "venv", "build", "dist"}
_ENV_IGNORED = {".git", ".truth-firewall", "__pycache__", ".pytest_cache", ".ruff_cache", "build", "dist"}
_ENV_TOOL_DIRS = {"site-packages", "dist-packages", "scripts", "bin", "include"}
_ENV_ROOTS = {".venv", "venv", "env", ".conda", ".tox"}
_MAX_FILES = 20_000
_MAX_BYTES = 128 * 1024 * 1024


def begin_observation_cycle() -> int:
    """Reserve a process-local sequence boundary before active verification."""
    return next(_OBSERVATIONS)


def _entry_metadata(path: Path) -> list[object]:
    """Stable type and permission bits; never bind volatile timestamps or inode IDs."""
    mode = path.lstat().st_mode
    kind = ("link" if stat.S_ISLNK(mode) else "directory" if stat.S_ISDIR(mode)
            else "file" if stat.S_ISREG(mode) else "special")
    return [kind, stat.S_IMODE(mode)]


def _message(record: EvidenceRecord) -> bytes:
    value = record.to_dict()
    value["tool_attestation"] = record.tool_attestation is True
    return json_dumps(value).encode("utf-8", errors="strict")


def attest_record(record: EvidenceRecord) -> EvidenceRecord:
    """Internal collector boundary. Never call this on imported/host-reported data."""
    record = replace(record, sequence=next(_OBSERVATIONS))
    digest = hmac.new(_KEY, _message(record), hashlib.sha256).hexdigest()
    return replace(record, collector_attestation=digest)


def is_attested(record: EvidenceRecord) -> bool:
    seal = record.collector_attestation
    if not isinstance(seal, str):
        return False
    try:
        digest = hmac.new(_KEY, _message(record), hashlib.sha256).hexdigest()
        return hmac.compare_digest(seal, digest)
    except (TypeError, ValueError, UnicodeError):
        return False


is_first_party = is_attested


def workspace_snapshot(
    root: Path, *, include_generated: bool = False, include_environment: bool = False,
) -> str | None:
    """Hash the bounded workspace state, including test data and configuration.

    Generated/cache directories are excluded. Test environment mode additionally
    binds workspace-local virtualenv state outside installed package/tool trees.
    Symlinks are recorded but never followed, and an external target makes the
    snapshot unavailable.
    """
    try:
        root = root.resolve(strict=True)
        digest = hashlib.sha256()
        count = size = 0
        for directory, dirs, files in os.walk(root, followlinks=False):
            parent = Path(directory)
            parent_parts = {part.lower() for part in parent.relative_to(root).parts}
            ignored = _ENV_IGNORED if include_environment else _IGNORED
            dirs[:] = sorted(
                name for name in dirs
                if include_generated or (
                    name.lower() not in ignored
                    and not name.endswith(".egg-info")
                    and not (include_environment and parent_parts.intersection({".venv", "venv"})
                             and name.lower() in _ENV_TOOL_DIRS)
                )
            )
            for name in sorted([*dirs, *files]):
                path = parent / name
                relative = path.relative_to(root).as_posix()
                metadata = _entry_metadata(path)
                count += 1
                if count > _MAX_FILES:
                    return None
                digest.update(json_dumps([relative, metadata]).encode())
                if path.is_symlink():
                    target = path.resolve(strict=True)
                    target.relative_to(root)
                    digest.update(json_dumps([relative, "link", os.readlink(path)]).encode())
                    if target.is_dir():
                        return None  # Do not silently omit a linked source tree.
                if path.is_dir():
                    continue
                if not path.is_file():
                    return None
                stat_before = path.stat()
                size += stat_before.st_size
                if count > _MAX_FILES or size > _MAX_BYTES:
                    return None
                content = hashlib.sha256()
                with path.open("rb") as stream:
                    for block in iter(lambda: stream.read(1024 * 1024), b""):
                        content.update(block)
                stat_after = path.stat()
                if ((stat_before.st_size, stat_before.st_mtime_ns)
                        != (stat_after.st_size, stat_after.st_mtime_ns)
                        or metadata != _entry_metadata(path)):
                    return None
                digest.update(json_dumps([relative, stat_after.st_size, content.hexdigest()]).encode())
        return digest.hexdigest()
    except (OSError, RuntimeError, ValueError, UnicodeError):
        return None


def environment_workspace_snapshot(root: Path) -> str | None:
    """Hash bounded workspace-local virtualenv state, excluding installed tool trees."""
    try:
        root = root.resolve(strict=True)
        digest = hashlib.sha256()
        count = size = 0
        for name in sorted(_ENV_ROOTS):
            environment_root = root / name
            if not environment_root.exists():
                continue
            if environment_root.is_symlink() or not environment_root.is_dir():
                return None
            digest.update(json_dumps([name, _entry_metadata(environment_root)]).encode())
            for directory, dirs, files in os.walk(environment_root, followlinks=False):
                parent = Path(directory)
                relative_parent = parent.relative_to(root)
                in_environment = any(part.lower() in _ENV_ROOTS for part in relative_parent.parts)
                dirs[:] = sorted(
                    child for child in dirs
                    if child.lower() not in _ENV_IGNORED
                    and not (in_environment and child.lower() in _ENV_TOOL_DIRS)
                )
                for child in sorted([*dirs, *files]):
                    path = parent / child
                    relative = path.relative_to(root).as_posix()
                    metadata = _entry_metadata(path)
                    count += 1
                    if count > _MAX_FILES:
                        return None
                    digest.update(json_dumps([relative, metadata]).encode())
                    if path.is_symlink():
                        return None
                    if path.is_dir():
                        continue
                    if not path.is_file():
                        return None
                    stat_before = path.stat()
                    size += stat_before.st_size
                    if count > _MAX_FILES or size > _MAX_BYTES:
                        return None
                    content = hashlib.sha256(path.read_bytes()).hexdigest()
                    stat_after = path.stat()
                    if ((stat_before.st_size, stat_before.st_mtime_ns)
                            != (stat_after.st_size, stat_after.st_mtime_ns)
                            or metadata != _entry_metadata(path)):
                        return None
                    digest.update(json_dumps([relative, stat_after.st_size, content]).encode())
        return digest.hexdigest()
    except (OSError, RuntimeError, ValueError, UnicodeError):
        return None


def file_state(root: Path, path: str) -> dict | None:
    from truth_firewall.safety import resolve_under_root

    try:
        resolved = resolve_under_root(root, path)
        state: dict = {"exists": resolved.exists(), "is_file": resolved.is_file()}
        if state["is_file"]:
            digest = hashlib.sha256()
            with resolved.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
            state["hash"] = digest.hexdigest()
        return state
    except (OSError, RuntimeError, ValueError):
        return None


def state_is_current(record: EvidenceRecord) -> bool:
    """Check a collector's bound state. Unknown bindings never count as fresh."""
    if not is_attested(record):
        return False
    payload = record.structured_payload()
    if record.evidence_type == "test" and record.provenance == "test:explicit-run":
        from truth_firewall.evidence.executable_identity import attest_runner
        from truth_firewall.evidence.execution_environment import (
            controlled_test_environment,
            execution_environment_fingerprint,
        )

        root = Path(record.workspace_root or "")
        argv = payload.get("runner_argv")
        runner = payload.get("runner_identity")
        if (
            payload.get("controlled_pytest") is not True
            or not isinstance(argv, list)
            or any(not isinstance(item, str) for item in argv)
            or not isinstance(runner, dict)
            or not isinstance(payload.get("execution_environment_fingerprint"), str)
            or not isinstance(payload.get("environment_workspace_snapshot"), str)
        ):
            return False
        runner_cwd = Path(record.cwd or root)
        try:
            runner_cwd.resolve(strict=True).relative_to(root.resolve(strict=True))
        except (OSError, RuntimeError, ValueError):
            return False
        current_runner = attest_runner(argv, runner_cwd)
        if current_runner is None or current_runner != runner:
            return False
        current_environment = execution_environment_fingerprint(controlled_test_environment(), current_runner)
        if current_environment is None or not hmac.compare_digest(
            current_environment, payload["execution_environment_fingerprint"]
        ):
            return False
        if environment_workspace_snapshot(root) != payload["environment_workspace_snapshot"]:
            return False
    if record.evidence_type == "git":
        from truth_firewall.evidence.git_identity import executable_hash, git_state, trusted_git

        executable = trusted_git()
        if executable is None or str(executable) != payload.get("git_executable"):
            return False
        if executable_hash(executable) != payload.get("git_executable_sha256"):
            return False
        if not record.workspace_root or git_state(Path(record.workspace_root), executable) != payload.get("git_state"):
            return False
    snapshot = payload.get("workspace_snapshot")
    if isinstance(snapshot, str) and record.workspace_root:
        return workspace_snapshot(
            Path(record.workspace_root),
            include_generated=payload.get("include_generated") is True,
        ) == snapshot
    observed = payload.get("observed_state")
    if isinstance(observed, dict) and record.workspace_root and record.file_path:
        return file_state(Path(record.workspace_root), record.file_path) == observed
    # Trusted embedding hosts may supply independent event records without live
    # filesystem state. Contracts still enforce timestamp and event ordering.
    return payload.get("state_binding") == "event"
