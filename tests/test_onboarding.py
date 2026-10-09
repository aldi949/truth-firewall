import json

import pytest

from truth_firewall.cli import main
from truth_firewall.pilot import _load_spec


def answers(monkeypatch, *values):
    replies = iter(values)
    monkeypatch.setattr("builtins.input", lambda prompt: next(replies))


def test_init_creates_valid_task_in_current_repository(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    answers(monkeypatch, "Implement a Python parser.", "src/parser.py")

    assert main(["init"]) == 0

    target = tmp_path / ".truth-firewall" / "task.json"
    data, contract = _load_spec(target)
    assert data["task"] == "Implement a Python parser."
    assert contract.conditions[0].kind == "file_exists"
    assert contract.conditions[0].path == "src/parser.py"
    assert all(condition.mandatory for condition in contract.conditions)
    output = capsys.readouterr().out
    assert "cannot infer omitted requirements" in output
    assert "file existence does not verify behavior or all requirements" in output
    assert "Created .truth-firewall/task.json" in output
    assert "Review every mandatory requirement before running." in output
    assert "Next: truth-firewall run" in output


def test_init_does_not_silently_overwrite(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    directory = tmp_path / ".truth-firewall"
    directory.mkdir()
    target = directory / "task.json"
    target.write_bytes(b"original contract")
    answers(monkeypatch)  # No prompts allowed when refusing overwrite.

    assert main(["init"]) == 1
    assert target.read_bytes() == b"original contract"
    assert "--overwrite" in capsys.readouterr().err


def test_explicit_overwrite_and_editable_template(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    directory = tmp_path / ".truth-firewall"
    directory.mkdir()
    target = directory / "task.json"
    target.write_text("old", encoding="utf-8")
    answers(monkeypatch, "Fix the parser's edge cases.", "")

    assert main(["init", "--overwrite"]) == 0
    data, contract = _load_spec(target)
    assert "EDITABLE TEMPLATE" in data["onboarding_note"]
    assert contract.conditions[0].path == "EDIT_ME_required_output.py"
    assert "Editable template only" in capsys.readouterr().out
    assert not list(directory.glob(".init-*.json"))


@pytest.mark.parametrize("path", ["../outside.py", "C:\\outside.py", "/outside.py", ".", ".truth-firewall/task.json"])
def test_init_rejects_invalid_output_paths(tmp_path, monkeypatch, path):
    monkeypatch.chdir(tmp_path)
    answers(monkeypatch, "Implement parser", path)
    assert main(["init"]) == 1
    assert not (tmp_path / ".truth-firewall" / "task.json").exists()


@pytest.mark.parametrize("value", ["", "   "])
def test_init_rejects_empty_task(tmp_path, monkeypatch, value):
    monkeypatch.chdir(tmp_path)
    answers(monkeypatch, value)
    assert main(["init"]) == 1
    assert not (tmp_path / ".truth-firewall").exists()


def test_validation_failure_preserves_existing_contract(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    directory = tmp_path / ".truth-firewall"
    directory.mkdir()
    target = directory / "task.json"
    target.write_bytes(b"original")
    answers(monkeypatch, "Implement parser", "parser.py")

    def fail_validation(path):
        assert json.loads(path.read_text(encoding="utf-8"))["task"] == "Implement parser"
        raise ValueError("invalid spec")

    monkeypatch.setattr("truth_firewall.onboarding._load_spec", fail_validation)
    assert main(["init", "--overwrite"]) == 1
    assert target.read_bytes() == b"original"
    assert not list(directory.glob(".init-*.json"))


def test_input_eof_creates_no_contract(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    def end_input(prompt):
        raise EOFError

    monkeypatch.setattr("builtins.input", end_input)
    assert main(["init"]) == 1
    assert not (tmp_path / ".truth-firewall" / "task.json").exists()


def test_init_validates_saved_file_before_success(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    answers(monkeypatch, "Implement parser", "parser.py")
    validated = []

    def validate(path):
        validated.append(path)
        return _load_spec(path)

    monkeypatch.setattr("truth_firewall.onboarding._load_spec", validate)
    assert main(["init"]) == 0
    assert validated[-1] == tmp_path / ".truth-firewall" / "task.json"


def test_init_preserves_contract_created_during_prompts(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    answers(monkeypatch, "Implement parser", "parser.py")
    target = tmp_path / ".truth-firewall" / "task.json"

    def concurrent_creation(path):
        result = _load_spec(path)
        target.write_bytes(b"another user's contract")
        return result

    monkeypatch.setattr("truth_firewall.onboarding._load_spec", concurrent_creation)
    assert main(["init"]) == 1
    assert target.read_bytes() == b"another user's contract"


@pytest.mark.parametrize("argv,spec,attempts", [
    (["run"], ".truth-firewall/task.json", 3),
    (["run", "--spec", "custom.json", "--attempts", "2"], "custom.json", 2),
])
def test_run_dispatch_is_unchanged(monkeypatch, argv, spec, attempts):
    calls = []

    def run(spec_value, *, attempts):
        calls.append((spec_value, attempts))
        return 2

    monkeypatch.setattr("truth_firewall.pilot.run_pilot", run)
    assert main(argv) == 2
    assert calls == [(spec, attempts)]
