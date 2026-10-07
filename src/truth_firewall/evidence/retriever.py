"""Deterministic candidate selection. Retrieval never creates evidence."""

from __future__ import annotations

import re

from truth_firewall.constants import MAX_CANDIDATES, MIN_RETRIEVAL_SCORE
from truth_firewall.evidence.ledger import verifying_records
from truth_firewall.schemas import Claim, ClaimType, EvidenceRecord
from truth_firewall.workspace import normalize_workspace_path

_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|[A-Za-z]:\\[^\s]+|(?:[\w.-]+/)+[\w.-]+|[\w.-]+\.[A-Za-z0-9]+")


def retrieve(claim: Claim, records: list[EvidenceRecord] | tuple[EvidenceRecord, ...]) -> list[EvidenceRecord]:
    pool = verifying_records(records)
    scored: list[tuple[int, EvidenceRecord]] = []
    for record in pool:
        score = _score(claim, record)
        if score >= MIN_RETRIEVAL_SCORE:
            scored.append((score, record))
    scored.sort(key=lambda item: (-item[0], item[1].evidence_id))
    return [record for _, record in scored[:MAX_CANDIDATES]]


def _score(claim: Claim, record: EvidenceRecord) -> int:
    score = 0
    required = set(claim.required_evidence_types)
    if record.evidence_type in required:
        score += 2
    attrs = claim.attributes()
    haystack = _haystack(record)
    path = str(attrs.get("target_path") or "")
    if path and _path_match(path, record):
        score += 5
    query = str(attrs.get("query") or "")
    payload = record.structured_payload()
    if query and (payload.get("query") == query or query in haystack):
        score += 5
    command = str(attrs.get("command") or "")
    if command and _command_overlap(command, record.command or ""):
        score += 3
    symbol = str(attrs.get("symbol") or "")
    if symbol and symbol in haystack:
        score += 3
    factor = str(attrs.get("factor") or "")
    if factor and factor.lower() in haystack.lower():
        score += 3
    if re.search(r"\blint\b", claim.normalized_claim, re.IGNORECASE) and re.search(
        r"\b(ruff|flake8|pylint|mypy|eslint)\b", record.command or "", re.IGNORECASE
    ):
        score += 5
    if claim.claim_type in {
        ClaimType.BEHAVIOR.value,
        ClaimType.CAUSAL.value,
        ClaimType.COMPLETION.value,
    }:
        # Type match alone must not drag in unrelated tests.
        if score <= 2:
            return 0
    if claim.claim_type == ClaimType.TEST_RESULT.value and record.evidence_type == "test":
        score = max(score, MIN_RETRIEVAL_SCORE)
    if claim.claim_type == ClaimType.TEST_RESULT.value and record.provenance.startswith(
        ("codex_hook:", "cursor_hook:")
    ):
        score = max(score, MIN_RETRIEVAL_SCORE)
    if claim.claim_type == ClaimType.TEST_EXECUTION.value and record.evidence_type in {"test", "process"}:
        if command and _command_overlap(command, record.command or ""):
            score = max(score, MIN_RETRIEVAL_SCORE)
    return score


def _haystack(record: EvidenceRecord) -> str:
    payload = record.structured_payload()
    parts = [
        record.stdout,
        record.stderr,
        record.command or "",
        record.file_path or "",
        str(payload.get("snippet_new") or ""),
        str(payload.get("excerpt") or ""),
        str(payload.get("query") or ""),
        str(payload.get("factor") or ""),
        str(payload.get("target") or ""),
        str(payload.get("path") or ""),
        str(payload.get("symbol") or ""),
        str(payload.get("operation") or ""),
        str(payload.get("framework") or ""),
        str(payload.get("scope") or ""),
        str(payload.get("workspace") or ""),
        str(payload.get("scope_path") or ""),
    ]
    return "\n".join(parts)


def _path_match(claim_path: str, record: EvidenceRecord) -> bool:
    claim_norm = normalize_workspace_path(claim_path, record.workspace_root)
    if claim_norm is None:
        return False
    candidates = [record.file_path or "", str(record.structured_payload().get("path") or "")]
    for candidate in candidates:
        norm = normalize_workspace_path(candidate, record.workspace_root)
        if norm is not None and norm == claim_norm:
            return True
    return False


def _command_overlap(claimed: str, recorded: str) -> bool:
    claimed_tokens = {token.lower() for token in _TOKEN.findall(claimed)}
    recorded_tokens = {token.lower() for token in re.split(r"\s+", recorded)}
    if not claimed_tokens or not recorded_tokens:
        return False
    return bool(claimed_tokens & recorded_tokens)
