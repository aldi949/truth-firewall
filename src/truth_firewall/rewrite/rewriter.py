"""Replace unsupported factual spans. Verified wording stays as written."""

from __future__ import annotations

from truth_firewall.policy.engine import is_enforced
from truth_firewall.schemas import Claim, ClaimAssessment, PolicyDecision


def rewrite(
    response_text: str,
    claims: list[Claim],
    assessments: list[ClaimAssessment],
    decisions: list[PolicyDecision],
) -> str:
    grounded, _ledger = rewrite_with_coverage(response_text, claims, assessments, decisions)
    return grounded


def rewrite_with_coverage(
    response_text: str,
    claims: list[Claim],
    assessments: list[ClaimAssessment],
    decisions: list[PolicyDecision],
    uncovered_spans: list[tuple[int, int]] | None = None,
) -> tuple[str, list[dict[str, object]]]:
    by_id = {claim.claim_id: claim for claim in claims}
    assessed = {item.claim_id: item for item in assessments}
    spans: list[tuple[int, int, str, str]] = []
    search_from: dict[str, int] = {}
    for decision in decisions:
        if not is_enforced(decision):
            continue
        claim = by_id.get(decision.claim_id)
        assessment = assessed.get(decision.claim_id)
        if claim is None or assessment is None:
            continue
        replacement = render_replacement(claim, assessment)
        start = claim.source_start
        end = claim.source_end
        if (
            start is None
            or end is None
            or start < 0
            or end > len(response_text)
            or response_text[start:end] != claim.raw_text
        ):
            start = response_text.find(claim.raw_text, search_from.get(claim.raw_text, 0))
            end = start + len(claim.raw_text) if start >= 0 else None
        if start < 0:
            raise ValueError(f"Cannot safely locate enforced claim {claim.claim_id} in response")
        assert end is not None
        spans.append((start, end, replacement, decision.claim_id))
        search_from[claim.raw_text] = end
    for start, end in uncovered_spans or []:
        if not 0 <= start < end <= len(response_text):
            raise ValueError("Invalid uncovered assertion span")
        spans.append((start, end, " [Unverified factual statement omitted.]", "uncovered"))
    ordered = sorted(spans, key=lambda item: item[0])
    if any(first[1] > second[0] for first, second in zip(ordered, ordered[1:])):
        raise ValueError("Cannot safely rewrite overlapping claim or coverage spans")
    parts: list[str] = []
    events: list[tuple[int, int, int, int]] = []
    ledger: list[dict[str, object]] = []
    cursor = 0
    output_length = 0
    for start, end, replacement, claim_id in ordered:
        prefix = response_text[cursor:start]
        parts.extend((prefix, replacement))
        output_length += len(prefix)
        delivered = [output_length, output_length + len(replacement)]
        ledger.append({"claim_id": claim_id, "source_span": [start, end],
                       "verdict": assessed[claim_id].verdict if claim_id in assessed else "UNKNOWN",
                       "action": "rewrite" if claim_id != "uncovered" else "omit_uncovered",
                       "delivered_span": delivered})
        events.append((start, end, len(replacement), end - start))
        output_length += len(replacement)
        cursor = end
    parts.append(response_text[cursor:])
    for claim in claims:
        start, end = claim.source_start, claim.source_end
        if start is None or end is None or any(item["claim_id"] == claim.claim_id for item in ledger):
            continue
        if any(left < end and right > start for left, right, _, _ in events):
            raise ValueError("Allowed claim overlaps a rewritten assertion")
        delta = sum(new - old for left, right, new, old in events if right <= start)
        ledger.append({"claim_id": claim.claim_id, "source_span": [start, end],
                       "verdict": assessed[claim.claim_id].verdict if claim.claim_id in assessed else "UNKNOWN",
                       "action": "retain", "delivered_span": [start + delta, end + delta]})
    return "".join(parts), sorted(ledger, key=lambda item: item["source_span"][0])


def render_replacement(claim: Claim, assessment: ClaimAssessment) -> str:
    if assessment.verdict == "CONTRADICTED":
        ids = ", ".join(assessment.evidence_ids) or "the recorded evidence"
        return (
            f"The claim that {_bare(claim.normalized_claim)} is contradicted by evidence {ids}. "
            f"{assessment.explanation}"
        )
    if claim.claim_type == "causal":
        return _causal_wording(claim, assessment.verdict)
    if claim.claim_type == "action_change" and assessment.verdict == "UNKNOWN":
        return f"I do not have sufficient evidence to verify that {_bare(claim.normalized_claim)}."
    if assessment.verdict == "INFERRED":
        return (
            f"My current hypothesis is that {_bare(claim.normalized_claim)}. "
            "This is inferred from incomplete evidence and is not fully verified."
        )
    return (
        f"My current hypothesis is that {_bare(claim.normalized_claim)}; "
        "this has not been verified."
    )


def _causal_wording(claim: Claim, verdict: str) -> str:
    obj = _with_article(claim.claim_object)
    subject = claim.subject.strip()
    if obj and subject and subject.lower() not in {"the root cause", "root cause"}:
        target = _with_article(subject)
        sentence = f"My current hypothesis is that {obj} is causing {target}"
    elif obj:
        sentence = f"My current hypothesis is that {obj} is the root cause"
    else:
        sentence = f"My current hypothesis is that {_bare(claim.normalized_claim)}"
    if verdict == "INFERRED":
        return sentence + ". This is an inference from incomplete evidence, not a verified cause."
    return sentence + "; I do not have sufficient evidence to verify that yet."


def _with_article(noun: str) -> str:
    text = noun.strip().rstrip(".")
    if not text:
        return text
    if text.lower().startswith("the "):
        return "the " + text[4:]
    return text


def _bare(text: str) -> str:
    return text.strip().rstrip(".")


def _apply(response_text: str, spans: list[tuple[int, int, str]]) -> str:
    ordered = sorted(spans, key=lambda item: item[0], reverse=True)
    used: list[tuple[int, int]] = []
    result = response_text
    for start, end, replacement in ordered:
        if any(not (end <= left or start >= right) for left, right in used):
            raise ValueError("Cannot safely rewrite overlapping enforced claims")
        result = result[:start] + replacement + result[end:]
        used.append((start, end))
    return result
