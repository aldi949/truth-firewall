"""Conservative identity checks for first-party test and lint executions."""

from __future__ import annotations

import hashlib
import importlib.metadata
import importlib.util
import json
import os
import shlex
import shutil
import sys
import sysconfig
from pathlib import Path
from urllib.parse import unquote, urlparse

_MODULES = {"pytest": "pytest", "ruff": "ruff", "unittest": "unittest"}


def attest_runner(argv: list[str], cwd: Path) -> dict[str, str | bool] | None:
    """Return an attestation only for a known installed runner under this interpreter."""
    if not argv or not all(isinstance(arg, str) for arg in argv):
        return None
    framework: str | None = None
    invocation = ""
    exe: Path
    try:
        current = Path(sys.executable).resolve(strict=True)
        raw_executable = Path(argv[0])
        if not raw_executable.is_absolute() and len(raw_executable.parts) == 1:
            located = shutil.which(argv[0])
            exe = Path(located).resolve(strict=True) if located else raw_executable.resolve(strict=True)
        else:
            exe = raw_executable.resolve(strict=True)
    except (OSError, RuntimeError):
        return None

    module_index = 2 if len(argv) >= 4 and argv[1] == "-I" and argv[2] == "-m" else 1
    isolated = module_index == 2
    if (
        exe == current
        and len(argv) > module_index + 1
        and argv[module_index] == "-m"
        and argv[module_index + 1] in _MODULES
    ):
        framework = argv[module_index + 1]
        invocation = "isolated-interpreter-module" if isolated else "interpreter-module"
        if _shadows_module(cwd, framework):
            if not isolated:
                return None
    else:
        name = Path(argv[0]).name.lower().removesuffix(".exe")
        if name not in {"pytest", "ruff"}:
            return None
        framework = name
        found = shutil.which(argv[0])
        scripts = Path(sysconfig.get_path("scripts")).resolve()
        if not found:
            return None
        try:
            resolved = Path(found).resolve(strict=True)
            if os.path.commonpath((str(scripts), str(resolved))) != str(scripts):
                return None
        except (OSError, RuntimeError, ValueError):
            return None
        exe = resolved
        invocation = "installed-script"

    if framework in _MODULES and not _installed_module_matches(cwd, framework, isolated=isolated):
        return None
    try:
        executable_hash = hashlib.sha256(exe.read_bytes()).hexdigest()
        spec = importlib.util.find_spec(framework)
        if spec is None or not spec.origin:
            return None
        origin = Path(spec.origin).resolve(strict=True)
        module_hash = hashlib.sha256(origin.read_bytes()).hexdigest()
    except (OSError, ImportError, RuntimeError, ValueError):
        return None
    return {"trusted": True, "framework": framework, "invocation": invocation,
            "executable": str(exe), "executable_sha256": executable_hash,
            "module_origin": str(origin), "module_sha256": module_hash}


def identity_from_command(command: str | None, cwd: str | None) -> dict[str, str | bool] | None:
    """Parse a direct command without shell expansion, then apply the same identity check."""
    if not command or any(token in command for token in (";", "|", "&", "<", ">", "`", "\n", "\r")):
        return None
    try:
        args = shlex.split(command, posix=False)
    except ValueError:
        return None
    if not args:
        return None
    clean = [arg.strip("\"'") for arg in args]
    return attest_runner(clean, Path(cwd or Path.cwd()))


def _shadows_module(cwd: Path, framework: str) -> bool:
    if framework not in {"pytest", "ruff", "unittest"}:
        return False
    return (cwd / f"{framework}.py").exists() or (cwd / framework / "__init__.py").exists()


def _installed_module_matches(cwd: Path, framework: str, *, isolated: bool = False) -> bool:
    if _shadows_module(cwd, framework) and not isolated:
        return False
    try:
        if framework == "unittest":
            expected = Path(sysconfig.get_path("stdlib"), "unittest", "__init__.py").resolve(strict=True)
            if isolated:
                return expected.is_file()
            spec = importlib.util.find_spec(framework)
            origin = Path(spec.origin).resolve(strict=True) if spec and spec.origin else None
            return origin == expected
        distribution_name = "pytest" if framework == "pytest" else "ruff"
        distribution = importlib.metadata.distribution(distribution_name)
        expected = Path(distribution.locate_file(f"{framework}/__init__.py")).resolve()
        if not expected.is_file():
            direct = distribution.read_text("direct_url.json")
            if not direct:
                return False
            metadata = json.loads(direct)
            if metadata.get("dir_info", {}).get("editable") is not True:
                return False
            url = urlparse(metadata.get("url", ""))
            if url.scheme != "file":
                return False
            editable_root = Path(unquote(url.path.lstrip("/") if os.name == "nt" else url.path)).resolve(strict=True)
            spec = importlib.util.find_spec(framework)
            origin = Path(spec.origin).resolve(strict=True) if spec and spec.origin else None
            if origin is None or origin.name != "__init__.py":
                return False
            try:
                origin.relative_to(editable_root)
            except ValueError:
                return False
            return True
        if isolated:
            roots = [
                Path(sysconfig.get_path(key)).resolve()
                for key in ("purelib", "platlib")
                if sysconfig.get_path(key)
            ]
            if not expected.is_file():
                return False
            for root in roots:
                try:
                    if os.path.commonpath((str(root), str(expected))) == str(root):
                        return True
                except ValueError:
                    continue
            return False
        spec = importlib.util.find_spec(framework)
        origin = Path(spec.origin).resolve(strict=True) if spec and spec.origin else None
        return origin == expected
    except (ImportError, importlib.metadata.PackageNotFoundError, OSError, RuntimeError, ValueError):
        return False


def isolate_known_module_argv(argv: list[str]) -> list[str]:
    """Ignore cwd/PYTHONPATH shadow packages when explicitly running a known module."""
    located = shutil.which(argv[0]) if argv else None
    resolved = Path(located or argv[0]).resolve() if argv else None
    if len(argv) >= 3 and resolved == Path(sys.executable).resolve() and argv[1] == "-m":
        if argv[2] in _MODULES:
            return [argv[0], "-I", *argv[1:]]
    return list(argv)
