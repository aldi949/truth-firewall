"""The published init starter must not authorize completion before explicit review."""

import json
from copy import deepcopy

import pytest

from truth_firewall.cli import main
from truth_firewall.completion import CompletionDecision, TaskCompletionVerifier
from truth_firewall.pilot import _load_spec, run_pilot


def _init(tmp_path, monkeypatch, path):
    monkeypatch.chdir(tmp_path)
    replies = iter(("In active.py, answer() must return the integer 42.", path))
    monkeypatch.setattr("builtins.input", lambda _: next(replies))
    assert main(["init"]) == 0
    return tmp_path / ".truth-firewall" / "task.json"


def _decision(root, spec):
    _, contract = _load_spec(spec)
    return TaskCompletionVerifier(root, contract).attempt_done("DONE").decision


def test_published_placeholder_reproduction_fails_closed(tmp_path, monkeypatch):
    (tmp_path / "active.py").write_text("def answer(): return 0\n", encoding="utf-8")
    (tmp_path / "EDIT_ME_required_output.py").write_text("# placeholder\n", encoding="utf-8")
    spec = _init(tmp_path, monkeypatch, "")
    data, contract = _load_spec(spec)
    assert data["acceptance_review"] == "unreviewed"
    assert contract.conditions[0].path == "EDIT_ME_required_output.py"
    assert _decision(tmp_path, spec) == CompletionDecision.HUMAN_REQUIRED
    assert main(["activate"]) == 1  # Creating the placeholder does not activate it.


def test_published_legacy_template_without_review_marker_fails_closed(tmp_path):
    (tmp_path / "EDIT_ME_required_output.py").write_text("", encoding="utf-8")
    spec = tmp_path / "task.json"
    spec.write_text(json.dumps({
        "task": "In active.py, answer() must return 42.",
        "onboarding_note": "EDITABLE TEMPLATE: replace the placeholder condition before running.",
        "conditions": [{"id": "EDIT_ME_required_file", "kind": "file_exists",
                        "path": "EDIT_ME_required_output.py", "exists": True}],
    }), encoding="utf-8")
    assert _decision(tmp_path, spec) == CompletionDecision.HUMAN_REQUIRED


def test_old_concrete_starter_and_reviewed_placeholder_still_block(tmp_path):
    (tmp_path / "output.py").write_text("", encoding="utf-8")
    spec = tmp_path / "task.json"
    data = {
        "task": "In active.py, answer() must return 42.",
        "onboarding_note": "STARTER CHECK ONLY: file existence does not establish behavior.",
        "conditions": [{"id": "required-file", "kind": "file_exists",
                        "path": "output.py", "exists": True}],
    }
    spec.write_text(json.dumps(data), encoding="utf-8")
    assert _decision(tmp_path, spec) == CompletionDecision.HUMAN_REQUIRED
    data["acceptance_review"] = "reviewed"
    data["conditions"][0]["path"] = "EDIT_ME_required_output.py"
    (tmp_path / "EDIT_ME_required_output.py").write_text("", encoding="utf-8")
    spec.write_text(json.dumps(data), encoding="utf-8")
    assert _decision(tmp_path, spec) == CompletionDecision.HUMAN_REQUIRED


def test_unreviewed_concrete_starter_blocks_and_skips_worker(tmp_path, monkeypatch, capsys):
    (tmp_path / "output.py").write_text("", encoding="utf-8")
    spec = _init(tmp_path, monkeypatch, "output.py")
    assert _decision(tmp_path, spec) == CompletionDecision.HUMAN_REQUIRED
    monkeypatch.setattr("truth_firewall.pilot.discover_codex",
                        lambda: (_ for _ in ()).throw(AssertionError("preflight reached Codex")))
    monkeypatch.setattr("truth_firewall.pilot.run_supervised_task",
                        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("worker launched")))
    assert run_pilot(str(spec)) == 2
    output = capsys.readouterr().out
    assert "HUMAN_REQUIRED" in output and "Codex was not started" in output
    summaries = list((tmp_path / ".truth-firewall" / "runs").glob("*/summary.json"))
    assert len(summaries) == 1
    summary = json.loads(summaries[0].read_text(encoding="utf-8"))
    assert summary["final_decision"] == "HUMAN_REQUIRED" and summary["attempts"] == 0


def test_activation_requires_explicit_confirmation(tmp_path, monkeypatch):
    spec = _init(tmp_path, monkeypatch, "output.py")
    before = spec.read_bytes()
    monkeypatch.setattr("builtins.input", lambda _: "")
    assert main(["activate"]) == 2
    assert spec.read_bytes() == before


def test_reviewed_controls_and_legacy_manual_spec(tmp_path, monkeypatch):
    active = tmp_path / "active.py"
    active.write_text("def answer(): return 0\n", encoding="utf-8")
    spec = _init(tmp_path, monkeypatch, "active.py")
    data = json.loads(spec.read_text(encoding="utf-8"))
    data["conditions"] = [{"id": "answer-output", "kind": "black_box", "path": "active.py",
                           "symbol": "answer", "cases": [{"args": [], "expected": 42}]}]
    spec.write_text(json.dumps(data), encoding="utf-8")
    assert _decision(tmp_path, spec) == CompletionDecision.HUMAN_REQUIRED
    monkeypatch.setattr("builtins.input", lambda _: "ACTIVATE")
    assert main(["activate"]) == 0
    assert _decision(tmp_path, spec) == CompletionDecision.REJECT_DONE
    active.write_text("def answer(): return 42\n", encoding="utf-8")
    assert _decision(tmp_path, spec) == CompletionDecision.VERIFIED_DONE
    data.pop("acceptance_review")
    data.pop("onboarding_note")
    manual = tmp_path / "manual.json"
    manual.write_text(json.dumps(data), encoding="utf-8")
    assert _decision(tmp_path, manual) == CompletionDecision.VERIFIED_DONE


def test_stale_target_remains_incomplete_contract_limitation(tmp_path):
    (tmp_path / "active.py").write_text("def answer(): return 0\n", encoding="utf-8")
    (tmp_path / "legacy.py").write_text("def answer(): return 42\n", encoding="utf-8")
    spec = tmp_path / "manual.json"
    spec.write_text(json.dumps({
        "task": "In active.py, answer() must return the integer 42.",
        "conditions": [{"id": "answer-output", "kind": "black_box", "path": "legacy.py",
                        "symbol": "answer", "cases": [{"args": [], "expected": 42}]}],
    }), encoding="utf-8")
    assert _decision(tmp_path, spec) == CompletionDecision.VERIFIED_DONE
    assert _decision(tmp_path, spec) != CompletionDecision.REJECT_DONE
    assert __import__("runpy").run_path(str(tmp_path / "active.py"))["answer"]() == 0


def _activate_checked_contract(tmp_path, monkeypatch):
    (tmp_path / "active.py").write_text("def answer(): return 42\n", encoding="utf-8")
    (tmp_path / "alternate.py").write_text("def answer(): return 42\n", encoding="utf-8")
    spec = _init(tmp_path, monkeypatch, "active.py")
    data = json.loads(spec.read_text(encoding="utf-8"))
    data["conditions"] = [
        {"id": "answer-output", "kind": "black_box", "path": "active.py", "symbol": "answer",
         "cases": [{"args": [], "expected": 42}]},
        {"id": "source-exists", "kind": "file_exists", "path": "active.py", "exists": True},
    ]
    spec.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr("builtins.input", lambda _: "ACTIVATE")
    assert main(["activate"]) == 0
    return spec


def test_unchanged_reviewed_digest_allows_verification(tmp_path, monkeypatch):
    spec = _activate_checked_contract(tmp_path, monkeypatch)
    data, contract = _load_spec(spec)
    assert len(data["acceptance_digest"]) == 64
    assert contract.readiness_gaps() == ()
    assert _decision(tmp_path, spec) == CompletionDecision.VERIFIED_DONE
    # Canonical key ordering must not require a new review.
    spec.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
    assert _decision(tmp_path, spec) == CompletionDecision.VERIFIED_DONE


@pytest.mark.parametrize("mutation", [
    "target_path", "task_text", "expected_output", "add_condition", "remove_condition",
    "task_id", "condition_order", "case_args", "symbol", "file_expectation",
])
def test_post_activation_edit_blocks_before_worker(tmp_path, monkeypatch, capsys, mutation):
    spec = _activate_checked_contract(tmp_path, monkeypatch)
    data = json.loads(spec.read_text(encoding="utf-8"))
    changed = deepcopy(data)
    if mutation == "target_path":
        changed["conditions"][0]["path"] = "alternate.py"
    elif mutation == "task_text":
        changed["task"] = "In active.py, answer() must return the integer 99."
    elif mutation == "expected_output":
        changed["conditions"][0]["cases"][0]["expected"] = 99
    elif mutation == "add_condition":
        changed["conditions"].append({"id": "other-file", "kind": "file_exists",
                                      "path": "alternate.py", "exists": True})
    elif mutation == "remove_condition":
        changed["conditions"].pop()
    elif mutation == "task_id":
        changed["task_id"] = "another-task"
    elif mutation == "condition_order":
        changed["conditions"].reverse()
    elif mutation == "case_args":
        changed["conditions"][0]["cases"][0]["args"] = [1]
    elif mutation == "symbol":
        changed["conditions"][0]["symbol"] = "other_answer"
    elif mutation == "file_expectation":
        changed["conditions"][1]["exists"] = False
    spec.write_text(json.dumps(changed), encoding="utf-8")
    assert _decision(tmp_path, spec) == CompletionDecision.HUMAN_REQUIRED
    monkeypatch.setattr("truth_firewall.pilot.discover_codex",
                        lambda: (_ for _ in ()).throw(AssertionError("Codex lookup reached")))
    monkeypatch.setattr("truth_firewall.pilot.run_supervised_task",
                        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("worker launched")))
    assert run_pilot(str(spec), attempts=1) == 2
    output = capsys.readouterr().out
    assert "HUMAN_REQUIRED" in output
    assert "review is invalid" in output and "activate again" in output
    assert "Codex was not started" in output
    summaries = list((tmp_path / ".truth-firewall" / "runs").glob("*/summary.json"))
    assert len(summaries) == 1
    summary = json.loads(summaries[0].read_text(encoding="utf-8"))
    assert summary["attempts"] == 0
    assert summary["human_required_reason_category"] == "acceptance_review_invalid"


def test_reactivation_requires_confirmation_and_restores_verification(tmp_path, monkeypatch):
    spec = _activate_checked_contract(tmp_path, monkeypatch)
    data = json.loads(spec.read_text(encoding="utf-8"))
    data["task"] = "In active.py, answer() must return the integer 42 exactly."
    spec.write_text(json.dumps(data), encoding="utf-8")
    assert _decision(tmp_path, spec) == CompletionDecision.HUMAN_REQUIRED
    before = spec.read_bytes()
    monkeypatch.setattr("builtins.input", lambda _: "")
    assert main(["activate"]) == 2
    assert spec.read_bytes() == before
    monkeypatch.setattr("builtins.input", lambda _: "ACTIVATE")
    assert main(["activate"]) == 0
    assert _decision(tmp_path, spec) == CompletionDecision.VERIFIED_DONE


def test_explicit_spec_review_integrity_and_missing_digest(tmp_path, monkeypatch, capsys):
    spec = _activate_checked_contract(tmp_path, monkeypatch)
    data = json.loads(spec.read_text(encoding="utf-8"))
    data.pop("acceptance_digest")
    spec.write_text(json.dumps(data), encoding="utf-8")
    assert _decision(tmp_path, spec) == CompletionDecision.HUMAN_REQUIRED
    monkeypatch.setattr("truth_firewall.pilot.discover_codex",
                        lambda: (_ for _ in ()).throw(AssertionError("Codex lookup reached")))
    assert main(["run", "--spec", str(spec), "--attempts", "1"]) == 2
    assert "activate --spec" in capsys.readouterr().out


@pytest.mark.parametrize("mutation", ["task_type", "remove_all_conditions"])
def test_changed_reviewed_spec_fails_review_before_schema_setup(tmp_path, monkeypatch, capsys, mutation):
    spec = _activate_checked_contract(tmp_path, monkeypatch)
    data = json.loads(spec.read_text(encoding="utf-8"))
    if mutation == "task_type":
        data["task_type"] = "javascript"
    else:
        data["conditions"] = []
    spec.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr("truth_firewall.pilot.discover_codex",
                        lambda: (_ for _ in ()).throw(AssertionError("Codex lookup reached")))
    assert run_pilot(str(spec), attempts=1) == 2
    assert "review is invalid" in capsys.readouterr().out
