"""Guided setup preserves the explicit review boundary and bounded evidence scope."""

import json

import pytest

from truth_firewall.cli import main
from truth_firewall.completion import CompletionDecision, TaskCompletionVerifier
from truth_firewall.pilot import _load_spec


def _answers(monkeypatch, *values):
    replies = iter(values)
    monkeypatch.setattr("builtins.input", lambda _: next(replies))


def _decision(root, spec):
    _, contract = _load_spec(spec)
    return TaskCompletionVerifier(root, contract).attempt_done("DONE").decision


def test_guided_behavior_is_unreviewed_until_explicit_activation(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "answer.py").write_text("def answer(): return 42\n", encoding="utf-8")
    _answers(monkeypatch, "answer() returns 42", "No-argument answer returns integer 42",
             "behavior", "answer.py", "answer", "0", "number", "42", "no", "no")
    assert main(["init", "--guided"]) == 0
    spec = tmp_path / ".truth-firewall/task.json"
    data, contract = _load_spec(spec)
    assert data["acceptance_review"] == "unreviewed"
    assert data["conditions"][0]["cases"] == [{"args": [], "expected": 42}]
    assert contract.readiness_gaps()
    assert _decision(tmp_path, spec) == CompletionDecision.HUMAN_REQUIRED
    output = capsys.readouterr().out
    assert "Finite cases prove only those cases" in output
    assert "nothing was activated" in output
    _answers(monkeypatch, "ACTIVATE")
    assert main(["activate"]) == 0
    output = capsys.readouterr().out
    assert '"expected": 42' in output
    assert "omitted requirements cannot be inferred" in output
    assert _decision(tmp_path, spec) == CompletionDecision.VERIFIED_DONE
    (tmp_path / "answer.py").write_text("def answer(): return 0\n", encoding="utf-8")
    assert _decision(tmp_path, spec) == CompletionDecision.REJECT_DONE


def test_guided_multiple_api_and_file_criteria(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _answers(monkeypatch, "Provide parse(text) in parser.py", "Parser has a text parameter",
             "api", "parser.py", "parse", "text", "yes", "Parser file exists",
             "file", "parser.py", "yes", "no")
    assert main(["init", "--guided"]) == 0
    data, _ = _load_spec(tmp_path / ".truth-firewall/task.json")
    assert [row["kind"] for row in data["conditions"]] == ["python_function", "file_exists"]
    assert data["conditions"][0]["parameters"] == ["text"]
    output = capsys.readouterr().out
    assert "signature check does not prove functional behavior" in output
    assert "existence does not prove modification" in output


def test_guided_text_arguments_require_no_json_quoting(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _answers(monkeypatch, "Echo a string", "Echo hello", "behavior", "echo.py", "echo",
             "1", "text", "hello", "text", "hello", "no", "no")
    assert main(["init", "--guided"]) == 0
    data, _ = _load_spec(tmp_path / ".truth-firewall/task.json")
    assert data["conditions"][0]["cases"] == [{"args": ["hello"], "expected": "hello"}]


def test_guided_multiple_cases_and_typed_values(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _answers(monkeypatch, "Transform values", "Two explicit transformation cases", "behavior", "active.py",
             "transform", "1", "list", "[1, 2]", "bool", "true", "yes",
             "1", "null", "null", "no", "no")
    assert main(["init", "--guided"]) == 0
    data, _ = _load_spec(tmp_path / ".truth-firewall/task.json")
    assert data["conditions"][0]["cases"] == [
        {"args": [[1, 2]], "expected": True}, {"args": [None], "expected": None}]


@pytest.mark.parametrize("kind", ["unsupported", "ambiguous", ""])
def test_unsupported_guided_criterion_never_saves_contract(tmp_path, monkeypatch, capsys, kind):
    monkeypatch.chdir(tmp_path)
    _answers(monkeypatch, "Support everything", "All inputs work", kind)
    assert main(["init", "--guided"]) == 1
    assert not (tmp_path / ".truth-firewall/task.json").exists()
    assert "HUMAN_REQUIRED" in capsys.readouterr().err


@pytest.mark.parametrize("path", ["../outside.py", "/outside.py", ".truth-firewall/task.json"])
def test_guided_paths_cannot_escape_or_target_contract(tmp_path, monkeypatch, path):
    monkeypatch.chdir(tmp_path)
    _answers(monkeypatch, "Create file", "File exists", "file", path)
    assert main(["init", "--guided"]) == 1
    assert not (tmp_path / ".truth-firewall/task.json").exists()


def test_guided_edits_invalidate_review_and_never_launch_worker(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _answers(monkeypatch, "Create output.py", "Output exists", "file", "output.py", "yes", "no")
    assert main(["init", "--guided"]) == 0
    spec = tmp_path / ".truth-firewall/task.json"
    monkeypatch.setattr("truth_firewall.pilot.run_supervised_task",
                        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("worker launch reached")))
    assert main(["run"]) == 2
    _answers(monkeypatch, "ACTIVATE")
    assert main(["activate"]) == 0
    data = json.loads(spec.read_text(encoding="utf-8"))
    data["conditions"][0]["description"] = "An edited criterion"
    spec.write_text(json.dumps(data), encoding="utf-8")
    assert main(["run", "--spec", str(spec)]) == 2
    assert "review is invalid" in capsys.readouterr().out


def test_guided_note_still_blocks_when_only_review_marker_is_removed(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "output.py").write_text("# preexisting file\n", encoding="utf-8")
    _answers(monkeypatch, "Create output.py", "Output exists", "file", "output.py", "yes", "no")
    assert main(["init", "--guided"]) == 0
    spec = tmp_path / ".truth-firewall/task.json"
    data = json.loads(spec.read_text(encoding="utf-8"))
    assert data["onboarding_note"].startswith("GUIDED CHECKS:")
    del data["acceptance_review"]
    spec.write_text(json.dumps(data), encoding="utf-8")
    assert _decision(tmp_path, spec) == CompletionDecision.HUMAN_REQUIRED
    monkeypatch.setattr("truth_firewall.pilot.run_supervised_task",
                        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("worker launch reached")))
    assert main(["run", "--spec", str(spec)]) == 2
    assert "Codex was not started" in capsys.readouterr().out


@pytest.mark.parametrize("value", ["NaN", "1e999", "true", '"number"'])
def test_guided_invalid_numeric_output_does_not_save(tmp_path, monkeypatch, value):
    monkeypatch.chdir(tmp_path)
    _answers(monkeypatch, "Return a number", "Returns 42", "behavior", "active.py", "answer",
             "0", "number", value)
    assert main(["init", "--guided"]) == 1
    assert not (tmp_path / ".truth-firewall/task.json").exists()


def test_guided_eof_preserves_existing_contract(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    target = tmp_path / ".truth-firewall/task.json"
    target.parent.mkdir()
    target.write_bytes(b"original")

    def end_input(_):
        raise EOFError

    monkeypatch.setattr("builtins.input", end_input)
    assert main(["init", "--guided", "--overwrite"]) == 1
    assert target.read_bytes() == b"original"


def test_guided_unsupported_overwrite_preserves_reviewed_contract(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    target = tmp_path / ".truth-firewall/task.json"
    target.parent.mkdir()
    original = b'{"acceptance_review":"reviewed","acceptance_digest":"existing"}'
    target.write_bytes(original)
    _answers(monkeypatch, "Replace task", "Unbounded quality requirement", "unsupported")
    assert main(["init", "--guided", "--overwrite"]) == 1
    assert target.read_bytes() == original


def test_guided_declining_activation_does_not_change_saved_bytes(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _answers(monkeypatch, "Create file", "File exists", "file", "output.py", "yes", "no")
    assert main(["init", "--guided"]) == 0
    target = tmp_path / ".truth-firewall/task.json"
    original = target.read_bytes()
    _answers(monkeypatch, "no")
    assert main(["activate"]) == 2
    assert target.read_bytes() == original


@pytest.mark.parametrize("tail", [
    ["file", "output.py", "maybe"],
    ["api", "parser.py", "parse", "text,text"],
    ["behavior", "active.py", "answer", "-1"],
    ["behavior", "active.py", "answer", "0", "number", "42", "maybe"],
    ["file", "output.py", "yes", "maybe"],
])
def test_guided_invalid_answers_never_save_incomplete_contract(tmp_path, monkeypatch, tail):
    monkeypatch.chdir(tmp_path)
    _answers(monkeypatch, "A bounded task", "An explicit criterion", *tail)
    assert main(["init", "--guided"]) == 1
    assert not (tmp_path / ".truth-firewall/task.json").exists()
