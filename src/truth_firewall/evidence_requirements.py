"""Declarative evidence contracts, independent of schema classes."""

from dataclasses import dataclass


@dataclass(frozen=True)
class EvidenceContract:
    proposition_kinds: tuple[str, ...]
    allowed_evidence_types: tuple[str, ...]
    minimum_trust: str = "A"
    first_party_attestation: bool = True
    workspace_identity: str = "exact"
    freshness: str = "timestamp_and_observed_state"
    scope: str = "entire_proposition"
    command_identity: str = "not_applicable"
    exit_code: str = "not_applicable"
    entailment: str = "complete_structural_proposition"
    imported_evidence: bool = False
    independent_corroboration: str = "first_party_observation"


def _contract(kind, types, **kwargs):
    return EvidenceContract((kind,), tuple(types), **kwargs)


EVIDENCE_CONTRACTS: dict[str, EvidenceContract] = {
    "existence": _contract("existence", ("filesystem",)),
    "file_repository_state": _contract("existence", ("filesystem",)),
    "absence": _contract("absence", ("filesystem", "process"), scope="complete_search_root_and_literal_query"),
    "action_change": _contract("action_change", ("filesystem", "runtime_event"),
                               independent_corroboration="observed_before_and_after"),
    "creation": _contract("action_change", ("filesystem",), independent_corroboration="observed_before_and_after"),
    "modification": _contract("action_change", ("filesystem",), independent_corroboration="observed_before_and_after"),
    "deletion": _contract("action_change", ("filesystem",), independent_corroboration="observed_before_and_after"),
    "test_execution": _contract("test_execution", ("test", "process"),
                                command_identity="attested_runner_and_argv", exit_code="recorded"),
    "test_result": _contract("test_result", ("test", "process"),
                             command_identity="attested_runner_and_scope", exit_code="outcome_compatible"),
    "lint": _contract("lint", ("test", "process"),
                      command_identity="attested_linter_and_scope", exit_code="outcome_compatible"),
    "process": _contract("process", ("process",), command_identity="exact_argv", exit_code="exact"),
    "git": _contract("git", ("git",), command_identity="attested_git_status", exit_code="zero"),
    "config": _contract("config", ("filesystem",), scope="exact_file_key_typed_value"),
    "source": _contract("source", ("filesystem",), scope="exact_file_structural_query"),
    "dependency": _contract("dependency", ("filesystem",), scope="exact_file_dependency_declaration"),
    # No authenticated runtime observer or controlled intervention collector exists yet.
    "runtime": EvidenceContract((), (), entailment="unsupported"),
    "environment": EvidenceContract((), (), entailment="unsupported"),
    "causal": EvidenceContract((), (), entailment="unsupported"),
    **{kind: EvidenceContract((), (), entailment="unsupported")
       for kind in ("behavior", "completion", "deployment", "external_fact", "other")},
}

MINIMUM_EVIDENCE_TYPES = {
    kind: contract.allowed_evidence_types for kind, contract in EVIDENCE_CONTRACTS.items()
}

MINIMUM_EVIDENCE_REQUIREMENTS = {
    kind: (contract.minimum_trust, contract.workspace_identity, contract.freshness,
           contract.scope, contract.command_identity, contract.exit_code, contract.entailment,
           contract.independent_corroboration)
    for kind, contract in EVIDENCE_CONTRACTS.items()
}
