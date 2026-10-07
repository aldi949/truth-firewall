"""Explicit git observations. Commands are a fixed argument list, never a shell string."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable

from truth_firewall.constants import COLLECTOR_TIMEOUT_SECONDS
from truth_firewall.errors import CollectorError
from truth_firewall.evidence.git_identity import executable_hash, git_state, trusted_git
from truth_firewall.evidence.provenance import attest_record, workspace_snapshot
from truth_firewall.safety import truncate_text
from truth_firewall.schemas import EvidenceRecord, EvidenceType, TrustLevel, json_dumps, utc_now

Runner = Callable[..., subprocess.CompletedProcess[str]]


class GitCollector:
    def __init__(self, cwd: Path, runner: Runner | None = None) -> None:
        self.cwd = cwd.resolve()
        self._runner = runner or subprocess.run

    def rev_parse(self, evidence_id: str = "git-rev-parse") -> EvidenceRecord:
        return self._capture(["git", "rev-parse", "HEAD"], evidence_id=evidence_id, kind="rev_parse")

    def status(self, evidence_id: str = "git-status") -> EvidenceRecord:
        return self._capture(
            ["git", "status", "--porcelain=v1", "-b"],
            evidence_id=evidence_id,
            kind="status",
        )

    def diff(self, evidence_id: str = "git-diff") -> EvidenceRecord:
        return self._capture(["git", "diff", "--"], evidence_id=evidence_id, kind="diff")

    def log(self, evidence_id: str = "git-log", limit: int = 5) -> EvidenceRecord:
        safe_limit = max(1, min(int(limit), 20))
        return self._capture(
            ["git", "log", f"-n{safe_limit}", "--oneline"],
            evidence_id=evidence_id,
            kind="log",
        )

    def _capture(self, args: list[str], *, evidence_id: str, kind: str) -> EvidenceRecord:
        if any(not isinstance(part, str) for part in args):
            raise CollectorError("git arguments must be strings")
        executable = trusted_git()
        if executable is None:
            raise CollectorError("no independently trusted Git executable is available")
        before_hash = executable_hash(executable)
        before_git = git_state(self.cwd, executable)
        command = [str(executable), *args[1:]]
        completed = self._runner(
            command,
            cwd=self.cwd,
            capture_output=True,
            text=True,
            timeout=COLLECTOR_TIMEOUT_SECONDS,
            shell=False,
            check=False,
        )
        stdout, stdout_truncated = truncate_text(completed.stdout or "")
        stderr, stderr_truncated = truncate_text(completed.stderr or "")
        payload = {
            "kind": kind,
            "args": args,
            "git_executable": str(executable),
            "git_executable_sha256": before_hash,
            "git_state": before_git,
            "stdout_truncated": stdout_truncated,
            "stderr_truncated": stderr_truncated,
            "workspace_snapshot": workspace_snapshot(self.cwd),
        }
        record = EvidenceRecord(
            evidence_id=evidence_id,
            evidence_type=EvidenceType.GIT.value,
            trust_level=TrustLevel.A.value,
            source="git",
            timestamp=utc_now().isoformat(),
            command=" ".join(args),
            cwd=str(self.cwd),
            exit_code=completed.returncode,
            stdout=stdout,
            stderr=stderr,
            file_path=None,
            file_hash=None,
            before_hash=None,
            after_hash=None,
            git_metadata_json=json_dumps({"kind": kind, "exit_code": completed.returncode}),
            payload_json=json_dumps(payload),
            provenance=f"git:{kind}",
        )
        after_git = git_state(self.cwd, executable)
        safe_runner = self._runner is subprocess.run
        stable = before_hash is not None and before_hash == executable_hash(executable)
        stable = stable and before_git is not None and before_git == after_git
        return attest_record(record) if safe_runner and stable and payload["workspace_snapshot"] is not None else record
