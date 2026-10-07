"""Falsification tests for the bounded completion lifecycle."""

from pathlib import Path

from truth_firewall.completion import (
    AcceptanceCondition,
    CompletionDecision,
    ConditionVerdict,
    TaskCompletionContract,
    TaskCompletionVerifier,
)


def contract(*conditions: AcceptanceCondition) -> TaskCompletionContract:
    return TaskCompletionContract("reset-helper", "Implement a reset token helper with the specified API and behavior.",
                                  tuple(conditions))


STRUCTURE = AcceptanceCondition("api", "python_function", "helper.py", "make_token", ["user_id"])
BEHAVIOR = AcceptanceCondition("behavior", "black_box", "helper.py", "make_token",
                               cases=((('alice',), 'reset:alice'), (('bob',), 'reset:bob')))


def write_helper(root: Path, body: str) -> None:
    (root / "helper.py").write_text(body, encoding="utf-8")


def test_false_done_retry_and_stale_state(tmp_path: Path) -> None:
    verifier = TaskCompletionVerifier(tmp_path, contract(STRUCTURE, BEHAVIOR))
    write_helper(tmp_path, "def make_token(user_id):\n    return 'reset:alice'\n")
    first = verifier.attempt_done("DONE, both requirements met")
    assert first.decision == CompletionDecision.REJECT_DONE
    assert [(e.condition_id, e.verdict) for e in first.evidence] == [
        ("api", ConditionVerdict.VERIFIED), ("behavior", ConditionVerdict.FAILED)]
    assert first.unmet_conditions == ("behavior",)

    write_helper(tmp_path, "def make_token(user_id):\n    return 'reset:' + user_id\n")
    second = verifier.attempt_done("DONE")
    assert second.decision == CompletionDecision.VERIFIED_DONE
    assert all(e.verdict == ConditionVerdict.VERIFIED for e in second.evidence)
    assert all(e.condition_id == condition.condition_id for e, condition in zip(second.evidence, verifier.contract.conditions))
    assert not first.is_current(tmp_path)

    write_helper(tmp_path, "def make_token(user_id):\n    return 'broken'\n")
    assert not second.is_current(tmp_path)
    assert second.current_decision(tmp_path) != CompletionDecision.VERIFIED_DONE
    assert verifier.attempt_done("DONE").decision == CompletionDecision.REJECT_DONE


def test_omitted_requirement_and_worker_claim_have_no_authority(tmp_path: Path) -> None:
    write_helper(tmp_path, "def make_token(user_id):\n    return 'wrong'\n")
    attempt = TaskCompletionVerifier(tmp_path, contract(STRUCTURE, BEHAVIOR)).attempt_done("API done; tests pass")
    assert attempt.decision == CompletionDecision.REJECT_DONE
    assert attempt.unmet_conditions == ("behavior",)


def test_unknown_unsupported_and_missing_evidence_require_human(tmp_path: Path) -> None:
    write_helper(tmp_path, "def make_token(user_id):\n    return 'reset:' + user_id\n")
    unsupported = AcceptanceCondition("quality", "subjective_quality", expected="delightful")
    attempt = TaskCompletionVerifier(tmp_path, contract(STRUCTURE, unsupported)).attempt_done("DONE")
    assert attempt.decision == CompletionDecision.HUMAN_REQUIRED
    assert attempt.evidence[1].verdict == ConditionVerdict.UNKNOWN
    assert attempt.unmet_conditions == ("quality",)


def test_evidence_cannot_cross_conditions_or_prove_partial_behavior(tmp_path: Path) -> None:
    write_helper(tmp_path, "def make_token(user_id):\n    return 'reset:alice'\n")
    attempt = TaskCompletionVerifier(tmp_path, contract(STRUCTURE, BEHAVIOR)).attempt_done("DONE")
    assert attempt.evidence[0].condition_id == "api"
    assert attempt.evidence[1].condition_id == "behavior"
    assert attempt.evidence[1].verdict == ConditionVerdict.FAILED
    assert attempt.decision == CompletionDecision.REJECT_DONE


def test_worker_controlled_test_and_claim_verdict_are_not_completion_evidence(tmp_path: Path) -> None:
    write_helper(tmp_path, "def make_token(user_id):\n    return 'wrong'\n")
    (tmp_path / "test_helper.py").write_text("def test_it():\n    assert True\n", encoding="utf-8")
    attempt = TaskCompletionVerifier(tmp_path, contract(STRUCTURE, BEHAVIOR)).attempt_done(
        "DONE. pytest passed. Claim verifier marked everything supported.")
    assert attempt.decision == CompletionDecision.REJECT_DONE
    assert attempt.evidence[1].verdict == ConditionVerdict.FAILED


def test_duplicate_condition_ids_rejected() -> None:
    try:
        contract(STRUCTURE, STRUCTURE)
    except ValueError:
        pass
    else:
        raise AssertionError("duplicate IDs must be rejected")


def test_malformed_expectation_never_passes(tmp_path: Path) -> None:
    (tmp_path / "helper.py").write_text("", encoding="utf-8")
    condition = AcceptanceCondition("file", "file_exists", "helper.py", expected="false")
    attempt = TaskCompletionVerifier(tmp_path, contract(condition)).attempt_done("DONE")
    assert attempt.decision == CompletionDecision.HUMAN_REQUIRED


def test_snapshot_excluded_path_cannot_be_a_requirement(tmp_path: Path) -> None:
    cache = tmp_path / ".ruff_cache"
    cache.mkdir()
    (cache / "proof.txt").write_text("present", encoding="utf-8")
    condition = AcceptanceCondition("cache", "file_exists", ".ruff_cache/proof.txt")
    attempt = TaskCompletionVerifier(tmp_path, contract(condition)).attempt_done("DONE")
    assert attempt.decision == CompletionDecision.HUMAN_REQUIRED
