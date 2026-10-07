"""Shared trusted test-result recollection for host integration events.

Host event JSON is only a trigger and supplies a bounded command/scope candidate.
Its output, exit code, timestamps, and declared trust never enter the trusted run.
"""

from __future__ import annotations

import os
import shlex
import shutil
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

from truth_firewall.evidence.collectors.test_runner import TestEvidence, recognize_test_invocation
from truth_firewall.evidence.provenance import (
    _ENV_ROOTS,
    _ENV_TOOL_DIRS,
    attest_record,
    environment_workspace_snapshot,
    workspace_snapshot,
)
from truth_firewall.schemas import EvidenceRecord, json_dumps


def collect_trusted_local_test_results(
    records: list[EvidenceRecord], workspace: Path, *, source: str,
) -> list[EvidenceRecord]:
    root = workspace.resolve(strict=True)
    # Running repository tests is an active operation. A copy limits workspace
    # writes; it is not a network/process sandbox, so explicit opt-in is required.
    if os.environ.get("TRUTH_FIREWALL_ALLOW_TEST_RERUN") != "1":
        return []
    trusted: list[EvidenceRecord] = []
    seen: set[tuple[str, tuple[str, ...]]] = set()
    for record in records:
        if not record.provenance.startswith(("codex_hook:", "cursor_hook:")):
            continue
        command = record.command
        invocation = recognize_test_invocation(command)
        if invocation is None or invocation.framework != "pytest" or invocation.scope == "unknown":
            continue
        try:
            parts = shlex.split(command or "", posix=os.name != "nt")
            if not parts:
                continue
            parts = [part.strip("\"'") for part in parts]
            executable = Path(parts[0]).name.lower()
            index = 1
            if executable.startswith("python") or executable in {"py", "py.exe"}:
                if index < len(parts) and parts[index] == "-I":
                    index += 1
                if parts[index:index + 2] != ["-m", "pytest"]:
                    continue
                args = parts[index + 2:]
            elif executable in {"pytest", "pytest.exe"}:
                args = parts[1:]
            else:
                continue
            cwd = Path(record.cwd or root).resolve(strict=True)
            cwd.relative_to(root)
            if not cwd.is_dir():
                continue
            key = (str(cwd), tuple(args))
            if key in seen:
                continue
            seen.add(key)
            # Always execute this candidate through the current trusted Python's
            # isolated pytest module; host executable identities are ignored.
            with tempfile.TemporaryDirectory(prefix="tf-hook-rerun-") as temporary:
                isolated_root = Path(temporary) / "workspace"
                # Reject links rather than copying through them or allowing a
                # test to reach outside the workspace via a linked path.
                excluded = {".git", ".truth-firewall", "__pycache__", ".pytest_cache",
                            ".ruff_cache", "build", "dist"}

                def ignored(directory: str, names: list[str]) -> set[str]:
                    relative = Path(directory).relative_to(root)
                    in_environment = any(part.lower() in _ENV_ROOTS for part in relative.parts)
                    return {name for name in names if name.lower() in excluded
                            or name.endswith(".egg-info")
                            or (in_environment and name.lower() in _ENV_TOOL_DIRS)}

                if any(path.is_symlink() for path in root.rglob("*")
                       if not set(path.relative_to(root).parts) & excluded
                       and not (set(part.lower() for part in path.relative_to(root).parts) & _ENV_TOOL_DIRS)):
                    continue
                shutil.copytree(root, isolated_root, copy_function=shutil.copy2,
                                ignore=ignored)
                before = workspace_snapshot(root)
                environment_before = environment_workspace_snapshot(root)
                if before is None or before != workspace_snapshot(isolated_root):
                    continue
                if environment_before is None or environment_before != environment_workspace_snapshot(isolated_root):
                    continue
                isolated_cwd = isolated_root / cwd.relative_to(root)
                result = TestEvidence().run(
                    [sys.executable, "-m", "pytest", *args], isolated_cwd,
                    explicit=True,
                    evidence_id=f"{source}-local-test-{len(trusted) + 1}",
                    workspace_root=isolated_root,
                )
                if (result.structured_payload().get("controlled_pytest") is True
                        and before == workspace_snapshot(root)
                        and environment_before == environment_workspace_snapshot(root)):
                    payload = result.structured_payload()
                    payload["workspace_snapshot"] = before
                    payload["environment_workspace_snapshot"] = environment_before
                    trusted.append(attest_record(replace(
                        result, cwd=str(cwd), workspace_root=str(root),
                        payload_json=json_dumps(payload), collector_attestation=None,
                    )))
        except (OSError, RuntimeError, TypeError, ValueError):
            continue
    return trusted
