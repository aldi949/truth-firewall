"""Run a check. Verification failures stay closed; the user still gets an answer."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Callable

from truth_firewall.audit import write_audit
from truth_firewall.claims.deterministic import DeterministicFallbackExtractor, may_contain_unextracted_fact
from truth_firewall.claims.coverage import uncovered_assertive_spans
from truth_firewall.claims.normalization import normalize_claims
from truth_firewall.claims.proposition import parse_atomic_proposition
from truth_firewall.claims.semantic import SemanticClaimExtractor
from truth_firewall.constants import DEFAULT_FRESHNESS_SECONDS, MAX_CLAIMS, MAX_EVIDENCE_RECORDS
from truth_firewall.evidence.ledger import EvidenceLedger
from truth_firewall.evidence.local import collect_for_claims
from truth_firewall.evidence.provenance import begin_observation_cycle, is_attested
from truth_firewall.evidence.retriever import retrieve
from truth_firewall.policy.engine import decide, is_enforced
from truth_firewall.providers.base import LLMProvider
from truth_firewall.rewrite.rewriter import rewrite_with_coverage
from truth_firewall.schemas import (
    Claim,
    ClaimAssessment,
    EvidenceRecord,
    PolicyDecision,
    RunBundle,
    Verdict,
    utc_now,
)
from truth_firewall.verification.verifier import DeterministicClaimVerifier
from truth_firewall.workspace import canonical_workspace_root, same_workspace

UNAVAILABLE = "Verification unavailable; factual claims could not be independently verified."
CLAIM_LIMIT_MESSAGE = (
    "Verification unavailable; the response exceeds the claim limit. Reduce the number of factual claims and resubmit."
)


@dataclass
class CheckResult:
    run_id: str
    verification_available: bool
    extractor_mode: str
    claims: list[Claim] = field(default_factory=list)
    assessments: list[ClaimAssessment] = field(default_factory=list)
    decisions: list[PolicyDecision] = field(default_factory=list)
    grounded_response: str = ""
    audit_dir: str | None = None
    errors: list[str] = field(default_factory=list)
    delivery: str = "unchanged"
    candidates: dict[str, list[str]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    coverage_ledger: list[dict[str, object]] = field(default_factory=list)
    claim_limit_exceeded: bool = False

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "verification_available": self.verification_available,
            "extractor_mode": self.extractor_mode,
            "delivery": self.delivery,
            "claims": [claim.to_dict() for claim in self.claims],
            "assessments": [item.to_dict() for item in self.assessments],
            "decisions": [item.to_dict() for item in self.decisions],
            "candidates": self.candidates,
            "grounded_response": self.grounded_response,
            "audit_dir": self.audit_dir,
            "errors": self.errors,
            "warnings": self.warnings,
            "coverage_ledger": self.coverage_ledger,
            "claim_limit_exceeded": self.claim_limit_exceeded,
        }


def check_response(
    response_text: str,
    evidence: list[EvidenceRecord] | None = None,
    *,
    provider: LLMProvider | None = None,
    offline: bool = False,
    explain: bool = False,
    runs_dir: Path | None = None,
    now: datetime | None = None,
    freshness_seconds: int = DEFAULT_FRESHNESS_SECONDS,
    write_audit_log: bool = True,
    run_id: str | None = None,
    workspace_root: str | Path | None = None,
    collect_local: bool = False,
    fresh_test_collector: Callable[[], list[EvidenceRecord]] | None = None,
) -> CheckResult:
    if evidence is not None and len(evidence) > MAX_EVIDENCE_RECORDS:
        return _unavailable(
            response_text,
            [f"evidence record cap {MAX_EVIDENCE_RECORDS} exceeded; verification was not attempted"],
            runs_dir=runs_dir,
            run_id=run_id,
        )
    try:
        return _check(
            response_text,
            evidence or [],
            provider=provider,
            offline=offline,
            explain=explain,
            runs_dir=runs_dir,
            now=now or utc_now(),
            freshness_seconds=freshness_seconds,
            write_audit_log=write_audit_log,
            run_id=run_id,
            workspace_root=workspace_root,
            collect_local=collect_local,
            fresh_test_collector=fresh_test_collector,
        )
    except Exception as exc:
        return _unavailable(response_text, [str(exc)], runs_dir=runs_dir, run_id=run_id)


def _check(
    response_text: str,
    evidence: list[EvidenceRecord],
    *,
    provider: LLMProvider | None,
    offline: bool,
    explain: bool,
    runs_dir: Path | None,
    now: datetime,
    freshness_seconds: int,
    write_audit_log: bool,
    run_id: str | None,
    workspace_root: str | Path | None,
    collect_local: bool,
    fresh_test_collector: Callable[[], list[EvidenceRecord]] | None,
) -> CheckResult:
    run_id = _safe_run_id(run_id) or _new_run_id(now)
    errors: list[str] = []
    warnings: list[str] = []
    ledger = EvidenceLedger()
    errors.extend(ledger.extend(evidence))
    expected_workspace = canonical_workspace_root(workspace_root, allow_relative=True)
    expected_workspace = expected_workspace or canonical_workspace_root(Path.cwd())
    claims, mode, extract_warnings = _extract(response_text, provider=provider, offline=offline)
    warnings.extend(extract_warnings)
    if not claims and may_contain_unextracted_fact(response_text):
        return _unavailable(
            response_text,
            ["A possibly factual response produced no checkable claims."],
            runs_dir=runs_dir,
            run_id=run_id,
        )
    claims, proposed = normalize_claims(claims, response_text, Path(expected_workspace))
    propositions = {claim_id: item.proposition for claim_id, item in proposed.items()}
    current_test_ids: set[int] = set()
    current_test_claim = any(
        claim.claim_type == "test_result"
        and (proposition := parse_atomic_proposition(claim)) is not None
        and proposition.terms.get("temporal") == "current"
        for claim in claims
    )
    if current_test_claim and fresh_test_collector is not None:
        boundary = begin_observation_cycle()
        try:
            fresh_tests = fresh_test_collector()
            if not isinstance(fresh_tests, list):
                raise TypeError("fresh test collector did not return a list")
            errors.extend(ledger.extend(fresh_tests))
            current_test_ids = {
                id(record) for record in fresh_tests if record in ledger.records()
                and is_attested(record) and type(record.sequence) is int and record.sequence > boundary
                and record.evidence_type == "test" and record.provenance == "test:explicit-run"
            }
            now = utc_now()
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            warnings.append(f"fresh test observation unavailable: {type(exc).__name__}")
    if collect_local:
        generated, local_errors = collect_for_claims(claims, Path(expected_workspace), propositions)
        warnings.extend(local_errors)
        if len(ledger) + len(generated) > MAX_EVIDENCE_RECORDS:
            generated = []
            warnings.append("local evidence record cap exceeded")
        errors.extend(ledger.extend(generated))
        now = utc_now()  # Local observations can occur after the caller's initial clock read.
    bundle = RunBundle(response_text=response_text, evidence=ledger.records())
    scoped_evidence = [
        record for record in bundle.evidence if same_workspace(record.workspace_root, expected_workspace)
    ]
    if len(claims) > MAX_CLAIMS:
        result = CheckResult(
            run_id=run_id,
            verification_available=False,
            extractor_mode=mode,
            claims=claims,
            grounded_response=CLAIM_LIMIT_MESSAGE,
            errors=errors,
            delivery="claim_limit_exceeded",
            warnings=warnings + [f"claim limit exceeded: {len(claims)} claims; maximum is {MAX_CLAIMS}"],
            claim_limit_exceeded=True,
        )
        if write_audit_log and runs_dir is not None:
            result.audit_dir = _store(runs_dir, result, response_text, bundle)
        return result
    verifier = DeterministicClaimVerifier(
        freshness_seconds=freshness_seconds,
        provider=provider,
        explain=explain and provider is not None,
    )
    assessments: list[ClaimAssessment] = []
    candidates: dict[str, list[str]] = {}
    for claim in claims:
        found = retrieve(claim, scoped_evidence)
        if claim.claim_type == "test_result":
            found.extend(
                record
                for record in scoped_evidence
                if record not in found
                and (
                    record.structured_payload().get("kind") == "file_edit"
                    or record.structured_payload().get("change_type") in {"created", "edited", "deleted", "modified"}
                )
            )
        candidates[claim.claim_id] = [item.evidence_id for item in found]
        try:
            assessments.append(verifier.verify(claim, scoped_evidence, now=now,
                                               workspace_root=expected_workspace,
                                               current_test_record_ids=current_test_ids,
                                               proposition=propositions.get(claim.claim_id)))
        except Exception as exc:
            errors.append(f"{claim.claim_id}: {exc}")
            assessments.append(
                ClaimAssessment(
                    claim_id=claim.claim_id,
                    verdict=Verdict.UNKNOWN.value,
                    evidence_ids=(),
                    explanation="Verifier failed closed for this claim.",
                    scope_match="missing",
                    freshness_ok=False,
                    contradiction=False,
                    confidence=0.0,
                    limitations=("verifier_error",),
                )
            )
    decisions = [decide(claim, assessment, extractor_mode=mode)
                 for claim, assessment in zip(claims, assessments, strict=False)]
    uncovered = uncovered_assertive_spans(response_text, claims)
    if uncovered:
        warnings.append(f"{len(uncovered)} uncovered factual source span(s) were omitted")
    grounded, coverage_ledger = rewrite_with_coverage(response_text, claims, assessments, decisions, uncovered)
    delivery = _delivery(decisions, grounded, response_text)
    result = CheckResult(
        run_id=run_id,
        verification_available=True,
        extractor_mode=mode,
        claims=claims,
        assessments=assessments,
        decisions=decisions,
        grounded_response=grounded,
        errors=errors,
        delivery=delivery,
        candidates=candidates,
        warnings=warnings,
        coverage_ledger=coverage_ledger,
    )
    if write_audit_log and runs_dir is not None:
        result.audit_dir = _store(runs_dir, result, response_text, bundle)
    return result


def _extract(response_text: str, *, provider: LLMProvider | None, offline: bool):
    if provider is not None and not offline:
        try:
            extracted = SemanticClaimExtractor(provider).extract(response_text)
            safety = DeterministicFallbackExtractor().extract(response_text)
            claims = _merge_claim_coverage(extracted.claims, safety.claims)
            warnings = list(extracted.warnings)
            if len(claims) > len(extracted.claims):
                warnings.append("deterministic coverage guard added factual claims missed by semantic extraction")
            return claims, extracted.mode, warnings
        except Exception as exc:
            fallback = DeterministicFallbackExtractor().extract(response_text)
            fallback.warnings.append(f"semantic extractor failed: {exc}")
            return fallback.claims, fallback.mode, fallback.warnings
    extracted = DeterministicFallbackExtractor().extract(response_text)
    return extracted.claims, extracted.mode, extracted.warnings


def _merge_claim_coverage(semantic: list[Claim], deterministic: list[Claim]) -> list[Claim]:
    """Keep semantic extraction while restoring deterministic factual coverage and modality."""
    merged = [claim for claim in semantic if not _semantic_claim_covers_multiple_atoms(claim, deterministic)]
    for fallback_claim in deterministic:
        if fallback_claim.claim_type == "other" and fallback_claim.epistemic_status != "asserted_fact":
            continue
        match_index = next(
            (index for index, claim in enumerate(merged) if _claims_overlap(claim, fallback_claim)),
            None,
        )
        if match_index is None:
            if fallback_claim.claim_type == "other":
                fallback_claim = replace(
                    fallback_claim,
                    qualifiers=tuple(dict.fromkeys((*fallback_claim.qualifiers, "deterministic_coverage_only"))),
                )
            merged.append(fallback_claim)
            continue
        semantic_claim = merged[match_index]
        # Deterministic extraction owns the evidence taxonomy. Semantic labels may
        # not move a proposition into an evidence class the text itself did not earn.
        taxonomy_agrees = semantic_claim.claim_type == fallback_claim.claim_type
        merged[match_index] = replace(
            semantic_claim,
            claim_type=fallback_claim.claim_type,
            normalized_claim=(semantic_claim.normalized_claim if taxonomy_agrees else fallback_claim.normalized_claim),
            subject=semantic_claim.subject if taxonomy_agrees else fallback_claim.subject,
            predicate=semantic_claim.predicate if taxonomy_agrees else fallback_claim.predicate,
            claim_object=semantic_claim.claim_object if taxonomy_agrees else fallback_claim.claim_object,
            scope=semantic_claim.scope if taxonomy_agrees else fallback_claim.scope,
            time_scope=semantic_claim.time_scope if taxonomy_agrees else fallback_claim.time_scope,
            epistemic_status=fallback_claim.epistemic_status,
            modality=fallback_claim.modality,
            required_evidence_types=fallback_claim.required_evidence_types,
            attributes_json=fallback_claim.attributes_json,
            qualifiers=fallback_claim.qualifiers,
            source_start=fallback_claim.source_start,
            source_end=fallback_claim.source_end,
        )
    return [replace(claim, claim_id=f"c-{index:03d}") for index, claim in enumerate(merged, start=1)]


def _semantic_claim_covers_multiple_atoms(semantic_claim: Claim, atoms: list[Claim]) -> bool:
    overlapping = [atom for atom in atoms if _claims_overlap(semantic_claim, atom)]
    if len(overlapping) > 1:
        return True
    if semantic_claim.source_start is not None and semantic_claim.source_end is not None:
        return False
    semantic_text = re.sub(r"\W+", " ", semantic_claim.raw_text.lower()).strip()
    contained_atoms = [
        atom
        for atom in atoms
        if re.sub(r"\W+", " ", atom.raw_text.lower()).strip() in semantic_text
        and re.sub(r"\W+", " ", atom.raw_text.lower()).strip() != semantic_text
    ]
    return len(contained_atoms) > 1


def _claims_overlap(first: Claim, second: Claim) -> bool:
    if (
        first.source_start is not None
        and first.source_end is not None
        and second.source_start is not None
        and second.source_end is not None
    ):
        return first.source_start < second.source_end and second.source_start < first.source_end
    return first.raw_text.strip() == second.raw_text.strip()


def _delivery(decisions: list[PolicyDecision], grounded: str, original: str) -> str:
    if any(is_enforced(item) for item in decisions) or grounded != original:
        return "rewritten"
    return "unchanged"


def _store(runs_dir: Path, result: CheckResult, response_text: str, bundle: RunBundle) -> str:
    directory = runs_dir / result.run_id
    write_audit(
        directory,
        {
            "meta": {
                "run_id": result.run_id,
                "extractor_mode": result.extractor_mode,
                "delivery": result.delivery,
                "verification_available": result.verification_available,
                "claim_limit_exceeded": result.claim_limit_exceeded,
            },
            "response": response_text,
            "claims": [claim.to_dict() for claim in result.claims],
            "evidence": [item.to_dict() for item in bundle.evidence],
            "candidates": result.candidates,
            "assessments": [item.to_dict() for item in result.assessments],
            "decisions": [item.to_dict() for item in result.decisions],
            "coverage": result.coverage_ledger,
            "grounded": result.grounded_response,
            "errors": result.errors + result.warnings,
        },
        permitted_root=runs_dir,
    )
    return str(directory)


def _unavailable(
    response_text: str,
    errors: list[str],
    *,
    runs_dir: Path | None,
    run_id: str | None,
) -> CheckResult:
    identifier = _safe_run_id(run_id) or _new_run_id(utc_now())
    grounded = UNAVAILABLE
    result = CheckResult(
        run_id=identifier,
        verification_available=False,
        extractor_mode="unavailable",
        grounded_response=grounded,
        errors=errors,
        delivery="verification_unavailable",
    )
    if runs_dir is not None:
        try:
            result.audit_dir = _store(
                runs_dir,
                result,
                response_text,
                RunBundle(response_text=response_text, evidence=()),
            )
        except OSError as exc:
            result.errors.append(str(exc))
    return result


def _safe_run_id(value: str | None) -> str | None:
    if not value or len(value) > 128:
        return None
    if value in {".", ".."} or ".." in value or "/" in value or "\\" in value:
        return None
    return value if re.fullmatch(r"[A-Za-z0-9_.-]+", value) else None


def _new_run_id(now: datetime) -> str:
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{uuid.uuid4().hex[:8]}"
