"""Claim verification. An LLM explanation cannot change the verdict."""

from __future__ import annotations

from datetime import datetime

from truth_firewall.claims.proposition import AtomicProposition
from truth_firewall.constants import MAX_LLM_EVIDENCE_SNIPPET_CHARS
from truth_firewall.providers.base import LLMProvider
from truth_firewall.redaction import redact_value
from truth_firewall.schemas import Claim, ClaimAssessment, EvidenceRecord
from truth_firewall.verification.entailment import assess


class DeterministicClaimVerifier:
    def __init__(
        self,
        *,
        freshness_seconds: int,
        provider: LLMProvider | None = None,
        explain: bool = False,
    ) -> None:
        self.freshness_seconds = freshness_seconds
        self.provider = provider
        self.explain = explain

    def verify(
        self,
        claim: Claim,
        evidence: list[EvidenceRecord],
        *,
        now: datetime,
        workspace_root: str,
        current_test_record_ids: set[int] | None = None,
        proposition: AtomicProposition | None = None,
    ) -> ClaimAssessment:
        assessment = assess(
            claim,
            evidence,
            now=now,
            freshness_seconds=self.freshness_seconds,
            workspace_root=workspace_root,
            current_test_record_ids=current_test_record_ids or set(),
            proposition=proposition,
        )
        if not self.explain or self.provider is None:
            return assessment
        return _attach_explanation(assessment, claim, evidence, self.provider)


def _attach_explanation(
    assessment: ClaimAssessment,
    claim: Claim,
    evidence: list[EvidenceRecord],
    provider: LLMProvider,
) -> ClaimAssessment:
    """Keep the rule verdict. Model text is an annotation only."""
    allowed = {record.evidence_id for record in evidence}
    slim = []
    for record in evidence:
        item = redact_value(record.to_dict())
        item["stdout"] = str(item.get("stdout") or "")[:MAX_LLM_EVIDENCE_SNIPPET_CHARS]
        slim.append(item)
    try:
        explained = provider.verify_claim_evidence(claim.to_dict(), slim)
    except Exception as exc:
        limitations = assessment.limitations + (f"explanation_unavailable: {exc}",)
        return _copy(assessment, limitations=limitations)
    cited = explained.get("evidence_ids") if isinstance(explained, dict) else []
    if not isinstance(cited, list):
        cited = []
    ignored = [str(item) for item in cited if str(item) not in allowed]
    limitations = assessment.limitations
    if ignored:
        limitations = limitations + ("model cited evidence that is not in the candidate set",)
    note = explained.get("explanation") if isinstance(explained, dict) else ""
    explanation = assessment.explanation
    if isinstance(note, str) and note.strip():
        explanation = assessment.explanation + " Model note (not a verdict): " + note.strip()
    return _copy(assessment, explanation=explanation, limitations=limitations)


def _copy(
    assessment: ClaimAssessment,
    *,
    explanation: str | None = None,
    limitations: tuple[str, ...] | None = None,
) -> ClaimAssessment:
    return ClaimAssessment(
        claim_id=assessment.claim_id,
        verdict=assessment.verdict,
        evidence_ids=assessment.evidence_ids,
        explanation=assessment.explanation if explanation is None else explanation,
        scope_match=assessment.scope_match,
        freshness_ok=assessment.freshness_ok,
        contradiction=assessment.contradiction,
        confidence=assessment.confidence,
        limitations=assessment.limitations if limitations is None else limitations,
    )
