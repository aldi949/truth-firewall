"""New synthetic properties for the generic contract-coverage gate."""

from truth_firewall.contract_coverage import (
    BranchCondition,
    Claim,
    CoverageDecision,
    EntailmentStatus,
    Evidence,
    ObligationView,
    Rejection,
    RejectionReason,
    RequirementClass,
    SemanticReview,
    Semantics,
    Uncertainty,
    entails,
    review_coverage,
)


TASK = "Keep the ledger stable. Emit the summary. An extra report is optional."


def condition(
    obligation_id="stable",
    source="Keep the ledger stable",
    semantics=Semantics.EXECUTION_INVARIANT,
    evidence_semantics=None,
    classification=RequirementClass.MANDATORY,
    uncertainty=Uncertainty.NONE,
    cases=frozenset({"normal", "error"}),
    evidence_cases=None,
    evidence=True,
):
    start = TASK.index(source)
    claim = Claim("ledger_unchanged", "input ledger", semantics, cases)
    obligation = ObligationView(obligation_id, (start, start + len(source)), source,
                                source, classification, claim, uncertainty,
                                "affects required behavior" if uncertainty is Uncertainty.REQUIRED else "")
    proof = None
    if evidence:
        proof = Evidence(obligation_id,
                         Claim("ledger_unchanged", "input ledger", evidence_semantics or semantics,
                               evidence_cases or cases),
                         "protected observation of the declared claim")
    return BranchCondition(obligation, proof)


def review(obligation_id="stable", status=EntailmentStatus.ENTAILS):
    return SemanticReview(obligation_id, status,
                          "Could a candidate pass this evidence while violating the source clause?",
                          "No such counterexample under the declared scope" if status is EntailmentStatus.ENTAILS
                          else "A counterexample remains", "independent semantic reviewer")


def gate(planner, reviewer, final, branch_reviews=None, final_reviews=None, rejections=(), source=None):
    inventory = source if source is not None else tuple(
        {item.obligation.obligation_id: item.obligation
         for item in (*planner, *reviewer, *final)}.values()
    )
    return review_coverage(TASK, tuple(inventory), tuple(planner), tuple(reviewer), tuple(final),
                           tuple(branch_reviews if branch_reviews is not None else (review(),)),
                           tuple(final_reviews if final_reviews is not None else (review(),)),
                           tuple(rejections))


def test_terminal_equality_cannot_prove_no_intermediate_mutation():
    ledger = ["entry"]
    before = ledger.copy()
    observed_during_call = []
    ledger.append("temporary write")
    observed_during_call.append(ledger.copy())
    ledger.pop()
    assert ledger == before  # A terminal-only check passes.
    assert any(state != before for state in observed_during_call)  # The invariant fails.
    required = condition()
    terminal_only = condition(evidence_semantics=Semantics.FINAL_STATE)
    assert not entails(terminal_only.evidence.claim, required.obligation.claim)
    assert gate([required], [required], [terminal_only]).decision is CoverageDecision.UNRESOLVED


def test_stronger_planner_branch_cannot_be_silently_weakened():
    strong = condition()
    weak = condition(evidence_semantics=Semantics.FINAL_STATE)
    result = gate([strong], [weak], [weak])
    assert result.decision is CoverageDecision.UNRESOLVED
    assert any("planner condition silently weakened" in item for item in result.unresolved)


def test_reviewer_restores_planner_miss_or_requires_explicit_rejection():
    other = condition("summary", "Emit the summary", Semantics.OUTPUT)
    discovered = condition()
    result = gate([other], [other, discovered], [other],
                  branch_reviews=(review("summary"), review("stable")),
                  final_reviews=(review("summary"),))
    assert result.decision is CoverageDecision.UNRESOLVED
    assert any("reviewer condition silently weakened" in item for item in result.unresolved)
    rejected = gate([other], [other, discovered], [other],
                    branch_reviews=(review("summary"), review("stable")),
                    final_reviews=(review("summary"),),
                    rejections=(Rejection("stable", "reviewer", RejectionReason.UNSUPPORTED,
                                          "Evidence cannot be established in the supplied context"),))
    assert rejected.decision is CoverageDecision.UNRESOLVED
    assert "reviewer condition rejected" in " ".join(rejected.unresolved)
    assert rejected.provenance[0].rejections
    both_missing = gate([other], [other], [other],
                        branch_reviews=(review("summary"),), final_reviews=(review("summary"),),
                        source=(other.obligation, discovered.obligation))
    assert both_missing.decision is CoverageDecision.UNRESOLVED
    assert any("stable: final evidence does not entail" in item for item in both_missing.unresolved)


def test_irrelevant_unspecified_edge_does_not_force_human_required():
    item = condition(uncertainty=Uncertainty.OUT_OF_SCOPE)
    assert gate([item], [item], [item]).decision is CoverageDecision.AUTHORIZED


def test_required_ambiguity_remains_unresolved():
    item = condition(uncertainty=Uncertainty.REQUIRED)
    result = gate([item], [item], [item])
    assert result.decision is CoverageDecision.UNRESOLVED
    assert any("uncertainty affects required behavior" in reason for reason in result.unresolved)


def test_terminal_only_requirement_is_not_upgraded_to_trace_invariant():
    item = condition(semantics=Semantics.FINAL_STATE)
    assert gate([item], [item], [item]).decision is CoverageDecision.AUTHORIZED


def test_optional_requirement_does_not_become_mandatory():
    required = condition()
    optional = condition("extra", "An extra report is optional", Semantics.OUTPUT,
                         classification=RequirementClass.OPTIONAL, evidence=False)
    result = gate([required, optional], [required, optional], [required, optional])
    assert result.decision is CoverageDecision.AUTHORIZED


def test_genuinely_stronger_evidence_may_be_preserved():
    required = condition(semantics=Semantics.FINAL_STATE)
    stronger = condition(semantics=Semantics.FINAL_STATE,
                         evidence_semantics=Semantics.EXECUTION_INVARIANT)
    assert entails(stronger.evidence.claim, required.obligation.claim)
    assert gate([stronger], [required], [stronger]).decision is CoverageDecision.AUTHORIZED


def test_explicit_counterexample_review_blocks_authorization():
    item = condition()
    result = gate([item], [item], [item], final_reviews=(review(status=EntailmentStatus.COUNTEREXAMPLE),))
    assert result.decision is CoverageDecision.UNRESOLVED
