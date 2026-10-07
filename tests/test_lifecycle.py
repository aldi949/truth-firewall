from pathlib import Path

from truth_firewall.completion import AcceptanceCondition, CompletionDecision, TaskCompletionContract, TaskCompletionVerifier
from truth_firewall.lifecycle import TerminalOutcome, run_task


def make_contract(*conditions):
    return TaskCompletionContract("lifecycle-demo", "Build a normalized lookup helper.", tuple(conditions))


def contract():
    return make_contract(
        AcceptanceCondition("api", "python_function", "helper.py", "lookup", ["key"]),
        AcceptanceCondition("ordinary", "black_box", "helper.py", "lookup", cases=((['A'], 'a'),)),
        AcceptanceCondition("empty", "black_box", "helper.py", "lookup", cases=(([''], None),)),
    )


def test_reject_continuation_drives_new_attempt_and_reverification(tmp_path: Path):
    states = ["def lookup(key):\n    return key.lower()\n",
              "def lookup(key):\n    return key.lower() if key else None\n"]
    calls = []

    def worker(root, feedback):
        calls.append(feedback)
        (root / "helper.py").write_text(states.pop(0), encoding="utf-8")
        return "DONE"

    run = run_task(tmp_path, contract(), worker, retry_budget=3, task_run_id="run-success")
    assert run.terminal_outcome == TerminalOutcome.VERIFIED_DONE
    assert [record.decision for record in run.history] == ["REJECT_DONE", "VERIFIED_DONE"]
    assert calls[0] is None
    assert calls[1].items[0].condition_id == "empty"
    assert "None" not in str(calls[1])
    assert run.history[0].state_identity != run.history[1].state_identity
    assert run.history[1].invalidated_prior_state
    assert run.history[1].attempt_number == 2


def test_retry_exhaustion_requires_human_and_preserves_reason(tmp_path: Path):
    (tmp_path / "helper.py").write_text("def lookup(key):\n    return key\n", encoding="utf-8")
    run = run_task(tmp_path, contract(), lambda root, feedback: "DONE", retry_budget=2)
    assert run.terminal_outcome == TerminalOutcome.HUMAN_REQUIRED
    assert run.terminal_outcome != TerminalOutcome.RETRY_EXHAUSTED
    assert run.terminal_reason == "retry limit reached while completion remains unproven"
    assert len(run.history) == 2
    assert all(record.decision == "REJECT_DONE" for record in run.history)


def test_unknown_mandatory_condition_stops_as_human_required(tmp_path: Path):
    (tmp_path / "helper.py").write_text("def lookup(key):\n    return key\n", encoding="utf-8")
    unknown = make_contract(AcceptanceCondition("subjective", "subjective_quality", expected="clear"))
    calls = []
    run = run_task(tmp_path, unknown, lambda root, feedback: calls.append(feedback) or "DONE", retry_budget=4)
    assert run.terminal_outcome == TerminalOutcome.HUMAN_REQUIRED
    assert len(run.history) == 1
    assert calls == [None]


def test_verifier_exception_is_safe_non_success(tmp_path: Path):
    class BrokenVerifier:
        def __init__(self, root, task_contract):
            pass

        def attempt_done(self, response):
            raise RuntimeError("boom")

    run = run_task(tmp_path, contract(), lambda root, feedback: "DONE", verifier_factory=BrokenVerifier)
    assert run.terminal_outcome == TerminalOutcome.ERROR
    assert run.history[0].decision == "ERROR"
    assert "RuntimeError" in run.history[0].error

    def broken_factory(root, task_contract):
        raise RuntimeError("cannot construct verifier")

    construction_failure = run_task(tmp_path, contract(), lambda root, feedback: "DONE",
                                    verifier_factory=broken_factory)
    assert construction_failure.terminal_outcome == TerminalOutcome.ERROR
    assert construction_failure.history[0].decision == "ERROR"


def test_worker_cannot_authorize_or_mutate_protected_contract(tmp_path: Path):
    original = contract()
    before = original.conditions

    def worker(root, feedback):
        (root / "helper.py").write_text("def lookup(key):\n    return key\n", encoding="utf-8")
        return "VERIFIED_DONE"

    run = run_task(tmp_path, original, worker, retry_budget=1)
    assert run.terminal_outcome == TerminalOutcome.HUMAN_REQUIRED
    assert run.history[0].done_attempt == "VERIFIED_DONE"
    assert original.conditions == before
    assert run.history[0].decision == CompletionDecision.REJECT_DONE.value


def test_worker_failure_is_not_success(tmp_path: Path):
    run = run_task(tmp_path, contract(), lambda root, feedback: (_ for _ in ()).throw(ValueError()), retry_budget=2)
    assert run.terminal_outcome == TerminalOutcome.ERROR
    assert run.history[0].decision == "ERROR"
