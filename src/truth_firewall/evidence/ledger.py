"""Append-only evidence ledger. Level D assistant text cannot verify claims."""

from __future__ import annotations

from dataclasses import replace

from truth_firewall.constants import MAX_PATH_CHARS
from truth_firewall.errors import EvidenceError
from truth_firewall.evidence.provenance import is_attested
from truth_firewall.schemas import EvidenceRecord, EvidenceType, TrustLevel


class EvidenceLedger:
    def __init__(self) -> None:
        self._records: list[EvidenceRecord] = []
        self.rejections: list[dict[str, str]] = []

    def __len__(self) -> int:
        return len(self._records)

    def records(self) -> tuple[EvidenceRecord, ...]:
        return tuple(self._records)

    def append(self, record: EvidenceRecord) -> None:
        reason = rejection_reason(record)
        if reason:
            self.rejections.append({"evidence_id": record.evidence_id, "reason": reason})
            raise EvidenceError(reason)
        if any(item.evidence_id == record.evidence_id for item in self._records):
            raise EvidenceError(f"duplicate evidence id {record.evidence_id}")
        # Collector-issued monotonic order is sealed. Imported sequence fields
        # are never promoted to first-party ordering authority.
        self._records.append(record if is_attested(record) else replace(record, sequence=len(self._records) + 1))

    def extend(self, records: list[EvidenceRecord]) -> list[str]:
        errors: list[str] = []
        for record in records:
            try:
                self.append(record)
            except EvidenceError as exc:
                errors.append(str(exc))
        return errors


def rejection_reason(record: EvidenceRecord) -> str | None:
    if not record.evidence_id or len(record.evidence_id) > MAX_PATH_CHARS:
        return "evidence id is missing or exceeds the schema limit"
    # Assistant prose is not an independent source. It must not enter the ledger.
    if record.trust_level == TrustLevel.D.value:
        return "Level D assistant text cannot be stored as verifying evidence"
    if record.evidence_type == EvidenceType.ASSISTANT_TEXT.value:
        return "assistant_text cannot be stored as verifying evidence"
    if record.trust_level not in {level.value for level in TrustLevel}:
        return f"unknown trust level {record.trust_level}"
    if record.evidence_type not in {kind.value for kind in EvidenceType}:
        return f"unknown evidence type {record.evidence_type}"
    return None


def verifying_records(records: list[EvidenceRecord] | tuple[EvidenceRecord, ...]) -> list[EvidenceRecord]:
    return [
        record
        for record in records
        if record.trust_level in {TrustLevel.A.value, TrustLevel.B.value, TrustLevel.C.value}
        and record.evidence_type != EvidenceType.ASSISTANT_TEXT.value
    ]
