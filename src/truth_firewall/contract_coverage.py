"""Conservative contract-coverage gate for independently prepared decompositions.

This gate checks a deliberately small, explicit claim language.  Translating a
natural-language task into those claims remains an independent review problem;
the gate never treats a model's unsupported assertion of entailment as proof.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class CoverageDecision(str, Enum):
    AUTHORIZED = "EVIDENCE_AUTHORIZED"
    UNRESOLVED = "COVERAGE_UNRESOLVED"


class RequirementClass(str, Enum):
    MANDATORY = "MANDATORY"
    OPTIONAL = "OPTIONAL"


class Semantics(str, Enum):
    FINAL_STATE = "FINAL_STATE"
    EXECUTION_INVARIANT = "EXECUTION_INVARIANT"
    OUTPUT = "OUTPUT"
    ERROR = "ERROR"
    TEMPORAL = "TEMPORAL"
    COMPATIBILITY = "COMPATIBILITY"


class Uncertainty(str, Enum):
    NONE = "NONE"
    REQUIRED = "REQUIRED"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"


class EntailmentStatus(str, Enum):
    ENTAILS = "ENTAILS"
    COUNTEREXAMPLE = "COUNTEREXAMPLE"
    UNKNOWN = "UNKNOWN"


class RejectionReason(str, Enum):
    INVALID = "INVALID"
    UNSUPPORTED = "UNSUPPORTED"
    CONTRADICTORY = "CONTRADICTORY"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"


@dataclass(frozen=True)
class Claim:
    """Atomic, source-grounded behavior; `cases` denotes covered input classes.

    The predicate is an identity for an exact behavior, not a natural-language
    similarity score. Side-effect prohibitions use EXECUTION_INVARIANT with a
    negative predicate and a scope naming the affected object.
    """

    predicate: str
    scope: str
    semantics: Semantics
    cases: frozenset[str]
    side_effects: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if not self.predicate or not self.scope or not self.cases:
            raise ValueError("claim predicate, scope, and cases are required")


@dataclass(frozen=True)
class ObligationView:
    obligation_id: str
    source_span: tuple[int, int]
    source_text: str
    required_behavior: str
    classification: RequirementClass
    claim: Claim
    uncertainty: Uncertainty = Uncertainty.NONE
    uncertainty_reason: str = ""

    def validate(self, original_task: str) -> None:
        start, end = self.source_span
        if (not self.obligation_id or not self.required_behavior or start < 0
                or end <= start or original_task[start:end] != self.source_text):
            raise ValueError("obligation is not grounded in the original task")
        if self.uncertainty is Uncertainty.REQUIRED and not self.uncertainty_reason:
            raise ValueError("required uncertainty needs a reason")


@dataclass(frozen=True)
class Evidence:
    obligation_id: str
    claim: Claim
    description: str


@dataclass(frozen=True)
class BranchCondition:
    obligation: ObligationView
    evidence: Evidence | None


@dataclass(frozen=True)
class SemanticReview:
    """Independent entailment review, including the counterexample question."""

    obligation_id: str
    status: EntailmentStatus
    counterexample_question: str
    rationale: str
    reviewer_identity: str

    def is_adequate(self) -> bool:
        return (self.status is EntailmentStatus.ENTAILS
                and bool(self.counterexample_question.strip())
                and bool(self.rationale.strip()) and bool(self.reviewer_identity.strip()))


@dataclass(frozen=True)
class Rejection:
    obligation_id: str
    branch: str
    reason: RejectionReason
    justification: str


@dataclass(frozen=True)
class Provenance:
    obligation_id: str
    source_text: str
    planner: BranchCondition | None
    reviewer: BranchCondition | None
    final: BranchCondition | None
    rejections: tuple[Rejection, ...]
    entailment_review: SemanticReview | None


@dataclass(frozen=True)
class CoverageResult:
    decision: CoverageDecision
    unresolved: tuple[str, ...]
    provenance: tuple[Provenance, ...]


def entails(evidence: Claim, obligation: Claim) -> bool:
    """Sound structural implication inside the declared atomic claim language.

    This does not establish that the declared obligation faithfully captures
    the original prose. That separate semantic review can return UNKNOWN.
    """

    if (evidence.predicate != obligation.predicate or evidence.scope != obligation.scope
            or not obligation.cases <= evidence.cases
            or not obligation.side_effects <= evidence.side_effects):
        return False
    if evidence.semantics is obligation.semantics:
        return True
    # A predicate true at every execution state is true at the terminal state.
    return (evidence.semantics is Semantics.EXECUTION_INVARIANT
            and obligation.semantics is Semantics.FINAL_STATE)


def _adequate(condition: BranchCondition | None, reviews: dict[str, SemanticReview]) -> bool:
    if condition is None or condition.evidence is None:
        return False
    obligation = condition.obligation
    evidence = condition.evidence
    review = reviews.get(obligation.obligation_id)
    return (obligation.classification is RequirementClass.MANDATORY
            and obligation.uncertainty is not Uncertainty.REQUIRED
            and evidence.obligation_id == obligation.obligation_id
            and bool(evidence.description.strip())
            and entails(evidence.claim, obligation.claim)
            and review is not None and review.is_adequate())


def review_coverage(
    original_task: str,
    source_obligations: tuple[ObligationView, ...],
    planner: tuple[BranchCondition, ...],
    reviewer: tuple[BranchCondition, ...],
    final: tuple[BranchCondition, ...],
    branch_reviews: tuple[SemanticReview, ...],
    final_reviews: tuple[SemanticReview, ...],
    rejections: tuple[Rejection, ...] = (),
) -> CoverageResult:
    """Fail closed on weak evidence, silent losses, and required uncertainty.

    Planner and reviewer must be produced independently before this call. The
    gate preserves their provenance but cannot itself establish their origin.
    An explicit rejection records a loss; it does not automatically authorize
    the final contract. Unsupported or contradictory mandatory behavior stays
    unresolved until the original task is independently adjudicated.
    """

    if not original_task:
        raise ValueError("original task is required")
    source_map: dict[str, ObligationView] = {}
    for item in source_obligations:
        item.validate(original_task)
        if item.obligation_id in source_map:
            raise ValueError(f"duplicate source obligation: {item.obligation_id}")
        source_map[item.obligation_id] = item
    branches = {"planner": planner, "reviewer": reviewer, "final": final}
    mappings: dict[str, dict[str, BranchCondition]] = {}
    for branch, conditions in branches.items():
        mapping: dict[str, BranchCondition] = {}
        for condition in conditions:
            condition.obligation.validate(original_task)
            key = condition.obligation.obligation_id
            if key in mapping:
                raise ValueError(f"duplicate {branch} obligation: {key}")
            mapping[key] = condition
        mappings[branch] = mapping

    def review_map(items: tuple[SemanticReview, ...]) -> dict[str, SemanticReview]:
        result: dict[str, SemanticReview] = {}
        for item in items:
            if item.obligation_id in result:
                raise ValueError("duplicate entailment review")
            result[item.obligation_id] = item
        return result

    reviewed_branches = review_map(branch_reviews)
    reviewed_final = review_map(final_reviews)
    all_ids = set(source_map).union(*(set(mapping) for mapping in mappings.values()))
    unresolved: list[str] = []
    provenance: list[Provenance] = []
    if not mappings["planner"] or not mappings["reviewer"]:
        unresolved.append("both independent decompositions are required")
    if not any(item.classification is RequirementClass.MANDATORY for item in source_map.values()):
        unresolved.append("no mandatory obligation established")
    for key in sorted(all_ids):
        source = source_map.get(key)
        p = mappings["planner"].get(key)
        r = mappings["reviewer"].get(key)
        f = mappings["final"].get(key)
        present = [item for item in (p, r, f) if item is not None]
        if source is None:
            unresolved.append(f"{key}: no source-grounded obligation inventory entry")
        # A shared ID must continue to mean the same source clause and behavior.
        baseline = source or present[0].obligation
        if any((item.obligation.source_span != baseline.source_span
                or item.obligation.source_text != baseline.source_text
                or item.obligation.claim != baseline.claim
                or item.obligation.classification != baseline.classification)
               for item in present):
            unresolved.append(f"{key}: inconsistent obligation identity")
        matching_rejections = tuple(item for item in rejections if item.obligation_id == key)
        provenance.append(Provenance(key, baseline.source_text, p, r, f,
                                     matching_rejections, reviewed_final.get(key)))
        if baseline.classification is RequirementClass.OPTIONAL:
            continue
        if baseline.uncertainty is Uncertainty.REQUIRED or any(
                item.obligation.uncertainty is Uncertainty.REQUIRED for item in present):
            unresolved.append(f"{key}: uncertainty affects required behavior")
            continue
        # OUT_OF_SCOPE questions are recorded but cannot block a sound mandatory
        # obligation or turn an optional behavior into a mandatory one.
        if f is None or not _adequate(f, reviewed_final):
            unresolved.append(f"{key}: final evidence does not entail mandatory behavior")
        for branch, condition in (("planner", p), ("reviewer", r)):
            if not _adequate(condition, reviewed_branches):
                continue
            preserved = (f is not None and f.evidence is not None
                         and condition.evidence is not None
                         and entails(f.evidence.claim, condition.evidence.claim))
            if not preserved:
                rejection = next((item for item in matching_rejections
                                  if item.branch == branch and item.justification.strip()), None)
                if rejection is None:
                    unresolved.append(f"{key}: adequate {branch} condition silently weakened or dropped")
                else:
                    unresolved.append(f"{key}: {branch} condition rejected: {rejection.reason.value}")
    return CoverageResult(CoverageDecision.UNRESOLVED if unresolved else CoverageDecision.AUTHORIZED,
                          tuple(unresolved), tuple(provenance))
