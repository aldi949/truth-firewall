"""Resolve Git independently of PATH and bind status observations to Git state."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from pathlib import Path


def trusted_git() -> Path | None:
    candidates: list[Path] = []
    if os.name == "nt":
        for variable in ("ProgramFiles", "ProgramFiles(x86)"):
            base = os.environ.get(variable)
            if base:
                candidates.append(Path(base) / "Git" / "cmd" / "git.exe")
        candidates.append(Path.home() / ".cache" / "codex-runtimes" / "codex-primary-runtime" /
                          "dependencies" / "native" / "git" / "cmd" / "git.exe")
    else:
        candidates.extend(Path(name) for name in ("/usr/bin/git", "/bin/git", "/opt/homebrew/bin/git"))
    # A PATH entry never grants trust. It may only name one of the independently
    # enumerated installation files above.
    located = shutil.which("git")
    if located:
        candidates.append(Path(located))
    allowed = {path.resolve() for path in candidates[:-1] if path.is_file()} if located else {
        path.resolve() for path in candidates if path.is_file()
    }
    for path in candidates:
        try:
            resolved = path.resolve(strict=True)
            if resolved in allowed and resolved.is_file():
                return resolved
        except (OSError, RuntimeError):
            continue
    return None


def executable_hash(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def git_state(root: Path, executable: Path) -> dict[str, str] | None:
    """Fingerprint HEAD, index bytes, and status, which cover clean/dirty claims."""
    try:
        def call(*args: str) -> subprocess.CompletedProcess[str]:
            return subprocess.run([str(executable), *args], cwd=root, capture_output=True,
                                  text=True, timeout=15, check=False)

        head = call("rev-parse", "HEAD")
        head_identity = head.stdout.strip() if head.returncode == 0 else ""
        if not head_identity:
            unborn = call("symbolic-ref", "--quiet", "HEAD")
            if unborn.returncode != 0:
                return None
            head_identity = "unborn:" + unborn.stdout.strip()
        git_directory = call("rev-parse", "--git-dir")
        index_path = call("rev-parse", "--git-path", "index")
        status = call("status", "--porcelain=v1", "-b", "--untracked-files=all")
        if any(item.returncode != 0 for item in (git_directory, index_path, status)):
            return None
        git_root = Path(git_directory.stdout.strip())
        if not git_root.is_absolute():
            git_root = root / git_root
        git_root = git_root.resolve(strict=True)
        index = Path(index_path.stdout.strip())
        if not index.is_absolute():
            index = root / index
        index = index.resolve()
        index.relative_to(git_root)
        index_digest = hashlib.sha256(index.read_bytes()).hexdigest() if index.is_file() else "absent"
        return {"head": head_identity, "index_sha256": index_digest,
                "status_sha256": hashlib.sha256(status.stdout.encode("utf-8")).hexdigest()}
    except (OSError, RuntimeError, subprocess.TimeoutExpired, UnicodeError):
        return None
