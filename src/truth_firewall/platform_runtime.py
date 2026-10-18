"""Local executable discovery and process cleanup for supported desktop platforms.

PATH discovery is convenience, not executable authentication or a security boundary.
"""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping


@dataclass(frozen=True)
class CodexRuntime:
    command: tuple[str, ...]


def discover_codex(*, environment: Mapping[str, str] | None = None,
                   which: Callable[[str], str | None] = shutil.which) -> CodexRuntime:
    """Resolve native Codex or npm's JS entrypoint without invoking a shell.

    An explicit JS override is authoritative: a bad override never falls back to
    a different executable. Native installs do not require Node.js.
    """
    env = os.environ if environment is None else environment

    def javascript(path: Path) -> CodexRuntime:
        if not path.is_file():
            raise RuntimeError("Codex CLI JavaScript entry point was not found; check TRUTH_FIREWALL_CODEX_JS "
                               "or install Codex CLI for this user")
        node = which("node") or which("node.exe")
        if not node:
            raise RuntimeError("Node.js was not found on PATH; install/configure Node.js for npm Codex CLI")
        return CodexRuntime((str(Path(node).resolve()), str(path.resolve())))

    override = env.get("TRUTH_FIREWALL_CODEX_JS")
    if override:
        return javascript(Path(override).expanduser())
    # On Windows, shutil.which("codex") can return npm's extensionless POSIX
    # launcher. CreateProcess cannot execute it (WinError 193). Prefer an actual
    # native .exe; otherwise resolve the npm launcher to its JavaScript entry.
    located = (which("codex.exe") or which("codex")) if os.name == "nt" else which("codex")
    if located:
        path = Path(located).resolve()
        if path.suffix.lower() == ".js":
            return javascript(path)
        if path.suffix.lower() in {".cmd", ".bat", ".ps1"} or (os.name == "nt" and not path.suffix):
            # npm's Windows wrappers require a shell. Launch their actual JS
            # entrypoint directly instead, preserving argv boundaries.
            return javascript(path.parent / "node_modules" / "@openai" / "codex" / "bin" / "codex.js")
        if path.is_file():
            return CodexRuntime((str(path),))
    appdata = env.get("APPDATA")
    if appdata:
        fallback = Path(appdata) / "npm" / "node_modules" / "@openai" / "codex" / "bin" / "codex.js"
        if fallback.is_file():
            return javascript(fallback)
    raise RuntimeError("Codex CLI was not found on PATH; install/authenticate Codex CLI, "
                       "or set TRUTH_FIREWALL_CODEX_JS to its JavaScript entry point")


def process_group_options() -> dict[str, object]:
    """Keep descendants in a supervisor-owned group/session for timeout cleanup."""
    return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
            "start_new_session": os.name != "nt"}


def terminate_process_tree(process: subprocess.Popen[bytes]) -> bool:
    """Best-effort cleanup of the supervised process tree, not containment."""
    if process.poll() is not None:
        return True
    if os.name == "nt":
        try:
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=3, check=False)
        except (OSError, subprocess.TimeoutExpired):
            pass
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            process.kill()
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except OSError:
            process.terminate()
        try:
            process.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            pass
        # The leader exiting after SIGTERM does not prove its descendants exited.
        # Always finish cleanup of the owned process group, including children
        # that ignore SIGTERM. A missing group simply means cleanup is complete.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except OSError:
            if process.poll() is None:
                process.kill()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
    return process.poll() is not None
