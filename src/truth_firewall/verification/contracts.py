"""The common admission boundary for every evidence-backed verdict."""

from datetime import datetime

from truth_firewall.claims.proposition import AtomicProposition
from truth_firewall.evidence.provenance import is_attested, state_is_current
from truth_firewall.evidence_requirements import EVIDENCE_CONTRACTS, EvidenceContract
from truth_firewall.schemas import Claim, EvidenceRecord, parse_time
from truth_firewall.workspace import same_workspace


def contract_for(claim: Claim, proposition: AtomicProposition) -> EvidenceContract | None:
    contract = EVIDENCE_CONTRACTS.get(claim.claim_type)
    if contract is None or proposition.kind not in contract.proposition_kinds:
        return None
    operation = {"creation": "created", "modification": "edited", "deletion": "deleted"}.get(claim.claim_type)
    if operation and proposition.terms.get("operation") != operation:
        return None
    return contract


def admissible(
    record: EvidenceRecord, contract: EvidenceContract, *, workspace_root: str,
    now: datetime, freshness_seconds: int,
) -> tuple[bool, str]:
    if record.evidence_type not in contract.allowed_evidence_types:
        return False, "incompatible_evidence_type"
    if record.trust_level != contract.minimum_trust or not is_attested(record):
        return False, "missing_first_party_attestation"
    if not same_workspace(record.workspace_root, workspace_root):
        return False, "workspace_mismatch"
    stamped = parse_time(record.timestamp)
    if stamped is None or now.tzinfo is None or not 0 <= (now - stamped).total_seconds() <= freshness_seconds:
        return False, "stale_or_untimed_evidence"
    if not state_is_current(record):
        return False, "observed_state_changed"
    payload = record.structured_payload()
    if payload.get("stdout_truncated") or payload.get("stderr_truncated"):
        return False, "truncated_observation"
    return True, "admitted"
