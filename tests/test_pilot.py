import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from truth_firewall.lifecycle import AttemptRecord, TaskRun, TerminalOutcome
from truth_firewall.pilot import _load_spec, _print_result, _write_summary


def test_load_spec_builds_mandatory_python_contract(tmp_path: Path):
    spec = {
        "task_id": "small-task",
        "task_type": "python",
        "task": "Implement add_one(value).",
        "conditions": [
            {"id": "api", "kind": "python_function", "path": "app.py",
             "symbol": "add_one", "parameters": ["value"]},
            {"id": "behavior", "kind": "black_box", "path": "app.py",
             "symbol": "add_one", "cases": [{"args": [1], "expected": 2}]},
            {"id": "file", "kind": "file_exists", "path": "app.py", "exists": True},
        ],
    }
    path = tmp_path / "task.json"
    path.write_text(json.dumps(spec), encoding="utf-8")

    _, contract = _load_spec(path)

    assert contract.task_id == "small-task"
    assert contract.original_task == spec["task"]
    assert len(contract.conditions) == 3
    assert all(condition.mandatory for condition in contract.conditions)
    assert contract.conditions[1].cases == (((1,), 2),)


@pytest.mark.parametrize("bad", [
    {"task": "x", "conditions": []},
    {"task": "x", "task_type": "javascript", "conditions": [{"id": "x", "kind": "file_exists", "path": "x"}]},
    {"task": "x", "conditions": [{"id": "x", "kind": "unknown", "path": "x"}]},
])
def test_load_spec_rejects_empty_or_unsupported_contracts(tmp_path: Path, bad):
    path = tmp_path / "task.json"
    path.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(ValueError):
        _load_spec(path)


def test_local_summary_contains_only_pilot_telemetry(tmp_path: Path):
    path = tmp_path / "summary.json"
    _write_summary(path, run_id="run-123", task_type="python", outcome="HUMAN_REQUIRED",
                   attempts=3, mandatory=4, rejects=2, continued=True,
                   human_reason="attempt_limit_reached_unproven", error_category=None)
    summary = json.loads(path.read_text(encoding="utf-8"))
    assert summary == {
        "run_id": "run-123",
        "task_type": "python",
        "final_decision": "HUMAN_REQUIRED",
        "attempts": 3,
        "mandatory_requirements": 4,
        "reject_done_count": 2,
        "automatic_continuation_occurred": True,
        "human_required_reason_category": "attempt_limit_reached_unproven",
        "runtime_error_category": None,
    }
    serialized = json.dumps(summary).lower()
    assert "implement add_one" not in serialized
    assert "original_task" not in serialized


def test_human_required_output_names_unproven_requirement_and_action(tmp_path: Path, capsys):
    record = AttemptRecord(
        task_run_id="run", contract_version="1", attempt_number=1, state_identity=None,
        done_attempt="DONE", condition_verdicts=(("runtime", "UNKNOWN"),),
        failed_conditions=(), unknown_conditions=("runtime",), evidence_refs=(), decision="HUMAN_REQUIRED",
        continuation=None, invalidated_prior_state=False,
        condition_observations=(("runtime", "the required runtime behavior cannot be observed"),),
    )
    run = TaskRun("run", "1", 3, (record,), TerminalOutcome.HUMAN_REQUIRED)
    fake_contract = SimpleNamespace(original_task="Implement a local function.", conditions=(None,))

    assert _print_result(run, fake_contract, tmp_path / "summary.json") == 2
    output = capsys.readouterr().out
    assert "HUMAN_REQUIRED" in output
    assert "runtime: UNKNOWN | the required runtime behavior cannot be observed" in output
    assert "Human action needed:" in output


def test_verified_output_shows_task_checks_evidence_and_attempts(tmp_path: Path, capsys):
    record = AttemptRecord(
        task_run_id="run", contract_version="1", attempt_number=2, state_identity="seal",
        done_attempt="DONE", condition_verdicts=(("api", "VERIFIED"),),
        failed_conditions=(), unknown_conditions=(), evidence_refs=("evidence",),
        decision="VERIFIED_DONE", continuation=None, invalidated_prior_state=True,
        condition_observations=(("api", "signature and required parameter observed"),),
    )
    run = TaskRun("run", "1", 3, (record,), TerminalOutcome.VERIFIED_DONE)
    fake_contract = SimpleNamespace(original_task="Implement add_one(value).", conditions=(None,))
    assert _print_result(run, fake_contract, tmp_path / "summary.json") == 0
    output = capsys.readouterr().out
    assert "VERIFIED_DONE" in output
    assert "Original task: Implement add_one(value)." in output
    assert "Mandatory requirements checked: 1/1" in output
    assert "api: VERIFIED | signature and required parameter observed" in output
    assert "Attempts used: 1" in output


def test_error_output_is_not_presented_as_task_failure(tmp_path: Path, capsys):
    record = AttemptRecord(
        task_run_id="run", contract_version="1", attempt_number=1, state_identity=None,
        done_attempt="<no result>", condition_verdicts=(), failed_conditions=(), unknown_conditions=(),
        evidence_refs=(), decision="TIMEOUT", continuation=None, invalidated_prior_state=False,
        error="worker attempt ended as TIMEOUT", worker_execution_status="TIMEOUT",
    )
    run = TaskRun("run", "1", 3, (record,), TerminalOutcome.TIMEOUT)
    assert _print_result(run, None, tmp_path / "summary.json") == 1
    output = capsys.readouterr().out
    assert "ERROR" in output
    assert "This is an execution error, not a finding that the coding task failed." in output
