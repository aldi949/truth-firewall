"""Unsupported or ambiguous acceptance config cannot authorize completion."""

from __future__ import annotations

import json

import pytest

from truth_firewall.cli import main
from truth_firewall.completion import CompletionDecision, TaskCompletionVerifier
from truth_firewall.pilot import _load_spec


def _base_spec():
    return {
        "task": "Make output.py exist.",
        "conditions": [{"id": "output", "kind": "file_exists", "path": "output.py", "exists": True}],
    }


def test_unknown_condition_evaluation_field_fails_before_worker(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "output.py").touch()
    data = _base_spec()
    data["conditions"][0]["unsupported_required_command"] = "exit 1"
    spec = tmp_path / "task.json"
    spec.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="unsupported condition.*unsupported_required_command"):
        _load_spec(spec)
    monkeypatch.setattr("truth_firewall.pilot.run_supervised_task",
                        lambda *args, **kwargs: pytest.fail("worker started"))
    assert main(["run", "--spec", str(spec)]) == 1
    assert "unsupported condition" in capsys.readouterr().out


def test_duplicate_task_keys_fail_before_worker(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "output.py").touch()
    spec = tmp_path / "task.json"
    spec.write_text(
        '{"task":"Return 42 from active.py.","task":"Make output.py exist.",'
        '"conditions":[{"id":"output","kind":"file_exists","path":"output.py","exists":true}]}',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="duplicate JSON field: task"):
        _load_spec(spec)
    monkeypatch.setattr("truth_firewall.pilot.run_supervised_task",
                        lambda *args, **kwargs: pytest.fail("worker started"))
    assert main(["run", "--spec", str(spec)]) == 1
    assert "duplicate JSON field: task" in capsys.readouterr().out


def test_valid_manual_legacy_contract_still_verifies(tmp_path):
    (tmp_path / "output.py").touch()
    spec = tmp_path / "task.json"
    spec.write_text(json.dumps(_base_spec()), encoding="utf-8")
    _, contract = _load_spec(spec)
    assert TaskCompletionVerifier(tmp_path, contract).attempt_done("DONE").decision == CompletionDecision.VERIFIED_DONE
