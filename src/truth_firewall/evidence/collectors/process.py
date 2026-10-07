"""Process evidence. Recording never executes a command. run() requires explicit=True."""

from __future__ import annotations

import subprocess
import sys
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from truth_firewall.constants import COLLECTOR_TIMEOUT_SECONDS
from truth_firewall.errors import CollectorError
from truth_firewall.evidence.executable_identity import attest_runner, isolate_known_module_argv
from truth_firewall.evidence.provenance import attest_record, workspace_snapshot
from truth_firewall.safety import truncate_text
from truth_firewall.schemas import EvidenceRecord, EvidenceType, TrustLevel, json_dumps, utc_now


class ProcessEvidence:
    def record(
        self,
        *,
        command: str,
        cwd: str | None,
        exit_code: int | None,
        stdout: str,
        stderr: str = "",
        started_at: datetime | None = None,
        finished_at: datetime | None = None,
        source: str = "process",
        trust_level: str = TrustLevel.A.value,
        evidence_id: str = "process-1",
        provenance: str = "process-record",
    ) -> EvidenceRecord:
        out, out_flag = truncate_text(stdout)
        err, err_flag = truncate_text(stderr)
        moment = finished_at or started_at
        return EvidenceRecord(
            evidence_id=evidence_id,
            evidence_type=EvidenceType.PROCESS.value,
            trust_level=trust_level,
            source=source,
            timestamp=moment.isoformat() if moment else utc_now().isoformat(),
            command=command,
            cwd=cwd,
            exit_code=exit_code,
            stdout=out,
            stderr=err,
            file_path=None,
            file_hash=None,
            before_hash=None,
            after_hash=None,
            git_metadata_json=json_dumps({}),
            payload_json=json_dumps(
                {
                    "kind": "process",
                    "stdout_truncated": out_flag,
                    "stderr_truncated": err_flag,
                    "exit_code_recorded": exit_code is not None,
                }
            ),
            provenance=provenance,
        )

    def run(
        self,
        args: list[str],
        cwd: Path,
        *,
        explicit: bool,
        evidence_id: str = "process-run",
        env: dict[str, str] | None = None,
        workspace_root: Path | None = None,
    ) -> EvidenceRecord:
        if explicit is not True:
            raise CollectorError("refusing to execute a command without explicit=True")
        if isinstance(args, str) or not all(isinstance(part, str) for part in args):
            raise CollectorError("command must be an argument list, not a shell string")
        root = (workspace_root or cwd).resolve(strict=True)
        try:
            cwd.resolve(strict=True).relative_to(root)
        except (OSError, RuntimeError, ValueError) as exc:
            raise CollectorError("process working directory is outside its bound workspace") from exc
        args = isolate_known_module_argv(args)
        before_state = workspace_snapshot(root)
        identity_before = attest_runner(args, cwd)
        completed = subprocess.run(
            args,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=COLLECTOR_TIMEOUT_SECONDS,
            shell=False,
            check=False,
            env=env,
        )
        record = self.record(
            command=subprocess.list2cmdline(args) if sys.platform == "win32" else " ".join(args),
            cwd=str(cwd),
            exit_code=completed.returncode,
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
            evidence_id=evidence_id,
            provenance="process:explicit-run",
        )
        identity_after = attest_runner(args, cwd)
        after_state = workspace_snapshot(root)
        if identity_before is None or identity_before != identity_after or before_state is None or after_state is None:
            return record
        payload = record.structured_payload()
        payload["runner_identity"] = identity_before
        payload["workspace_snapshot"] = after_state
        payload["argv"] = args
        bound_record = replace(
            record, workspace_root=str(root), payload_json=json_dumps(payload), tool_attestation=True,
        )
        return attest_record(bound_record)
