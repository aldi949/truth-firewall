"""Canonical policy for core rewriting and host correction adapters."""

from __future__ import annotations

from collections.abc import Iterable

from truth_firewall.schemas import Claim, ClaimAssessment, EpistemicStatus, PolicyAction, PolicyDecision, Verdict

DEFAULT_POLICY = {
    Verdict.VERIFIED.value: PolicyAction.ALLOW.value,
    Verdict.INFERRED.value: PolicyAction.RELABEL.value,
    Verdict.UNKNOWN.value: PolicyAction.RELABEL.value,
    Verdict.CONTRADICTED.value: PolicyAction.BLOCK_OR_REWRITE.value,
    Verdict.NOT_CHECKABLE.value: PolicyAction.RELABEL.value,
}


def decide(claim: Claim, assessment: ClaimAssessment, *, extractor_mode: str = "semantic") -> PolicyDecision:
    """Decide enforcement once; integrations must consume this decision."""
    if claim.epistemic_status != EpistemicStatus.ASSERTED_FACT.value:
        action = PolicyAction.ALLOW.value
        reason = "The statement is not presented as an established fact."
    else:
        action = DEFAULT_POLICY.get(assessment.verdict, PolicyAction.RELABEL.value)
        reason = f"{assessment.verdict} maps to {action}."
    return PolicyDecision(claim_id=claim.claim_id, verdict=assessment.verdict, action=action, reason=reason)


def is_enforced(decision: PolicyDecision) -> bool:
    """Whether this decision requires correcting the asserted wording."""
    return decision.action != PolicyAction.ALLOW.value


def requires_correction(
    decisions: Iterable[PolicyDecision],
    *,
    verification_available: bool = True,
    extraction_failed: bool = False,
) -> bool:
    """Host-independent correction requirement, independent of delivery capabilities."""
    return not verification_available or extraction_failed or any(is_enforced(item) for item in decisions)
