"""Import JSON or JSONL runtime events. Original payloads are preserved."""

from __future__ import annotations

from pathlib import Path

from truth_firewall.evidence.io import load_evidence_path
from truth_firewall.schemas import EvidenceRecord, json_dumps


class RuntimeEventImporter:
    def load_path(self, path: Path) -> tuple[list[EvidenceRecord], list[str]]:
        records, errors = load_evidence_path(path)
        preserved: list[EvidenceRecord] = []
        for record in records:
            payload = record.structured_payload()
            if "original" not in payload:
                payload = {"original": payload, **payload}
            preserved.append(
                EvidenceRecord(
                    evidence_id=record.evidence_id,
                    evidence_type=record.evidence_type,
                    trust_level=record.trust_level,
                    source=record.source,
                    timestamp=record.timestamp,
                    command=record.command,
                    cwd=record.cwd,
                    exit_code=record.exit_code,
                    stdout=record.stdout,
                    stderr=record.stderr,
                    file_path=record.file_path,
                    file_hash=record.file_hash,
                    before_hash=record.before_hash,
                    after_hash=record.after_hash,
                    git_metadata_json=record.git_metadata_json,
                    payload_json=json_dumps(payload),
                    provenance=record.provenance,
                    workspace_root=record.workspace_root,
                    sequence=record.sequence,
                )
            )
        return preserved, errors
