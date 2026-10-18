"""Generic regressions for independently reproduced V0.2 evidence failures."""

import hashlib
import json
import os
import py_compile

import pytest

from truth_firewall.completion import (
    AcceptanceCondition,
    CompletionDecision,
    CompletionObligation,
    TaskCompletionContract,
    TaskCompletionVerifier,
)
from truth_firewall.pilot import _load_spec


def decision(root, *conditions, obligations=()):
    contract = TaskCompletionContract(
        "bounded", "Implement exactly the registered requirements.", tuple(conditions), obligations=obligations
    )
    return TaskCompletionVerifier(root, contract).attempt_done("DONE")


def behavior(expected=42):
    return AcceptanceCondition("behavior", "black_box", "active.py", "answer", cases=(((), expected),))


def test_stale_bytecode_cannot_authorize_current_incorrect_source(tmp_path):
    path = tmp_path / "active.py"
    path.write_text("def answer(): return 42\n", encoding="utf-8")
    stamp = path.stat().st_mtime_ns
    py_compile.compile(str(path), doraise=True)
    path.write_text("def answer(): return 00\n", encoding="utf-8")
    os.utime(path, ns=(stamp, stamp))
    assert decision(tmp_path, behavior()).decision == CompletionDecision.REJECT_DONE
    path.write_text("def answer(): return 42\n", encoding="utf-8")
    assert decision(tmp_path, behavior()).decision == CompletionDecision.VERIFIED_DONE


@pytest.mark.parametrize(
    "actual,expected",
    [
        ({"ok": 1}, {"ok": True}),
        ([{"x": [0]}], [{"x": [False]}]),
        ({"x": 1.0}, {"x": 1}),
        ({"a": True, "b": [1]}, {"a": True, "b": [1.0]}),
    ],
)
def test_nested_json_type_mismatches_fail(tmp_path, actual, expected):
    (tmp_path / "active.py").write_text(f"def answer(): return {actual!r}\n", encoding="utf-8")
    assert decision(tmp_path, behavior(expected)).decision == CompletionDecision.REJECT_DONE
    (tmp_path / "active.py").write_text(f"def answer(): return {expected!r}\n", encoding="utf-8")
    assert decision(tmp_path, behavior(expected)).decision == CompletionDecision.VERIFIED_DONE


def test_file_changed_proves_bytes_changed_not_merely_existence(tmp_path):
    path = tmp_path / "active.py"
    path.write_bytes(b"old")
    condition = AcceptanceCondition(
        "change", "file_changed", "active.py", expected=hashlib.sha256(path.read_bytes()).hexdigest()
    )
    assert decision(tmp_path, condition).decision == CompletionDecision.REJECT_DONE
    os.utime(path, None)
    assert decision(tmp_path, condition).decision == CompletionDecision.REJECT_DONE
    path.write_bytes(b"new")
    assert decision(tmp_path, condition).decision == CompletionDecision.VERIFIED_DONE
    path.unlink()
    assert decision(tmp_path, condition).decision == CompletionDecision.REJECT_DONE


def test_file_changed_without_reviewed_baseline_is_unknown(tmp_path):
    (tmp_path / "active.py").touch()
    assert (
        decision(tmp_path, AcceptanceCondition("change", "file_changed", "active.py")).decision
        == CompletionDecision.HUMAN_REQUIRED
    )


SUITE = """import unittest
from active import answer
class Required(unittest.TestCase):
    def test_answer(self): self.assertEqual(answer(), 42)
"""


def suite(root, source=SUITE):
    path = root / "test_required.py"
    path.write_text(source, encoding="utf-8")
    return AcceptanceCondition(
        "suite",
        "unittest",
        "test_required.py",
        expected={"sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "test_ids": ["Required.test_answer"]},
    )


def test_mandatory_suite_runs_and_cannot_be_weakened(tmp_path):
    (tmp_path / "active.py").write_text("def answer(): return 42\n", encoding="utf-8")
    check = suite(tmp_path)
    assert decision(tmp_path, check).decision == CompletionDecision.VERIFIED_DONE
    (tmp_path / "active.py").write_text("def answer(): return 00\n", encoding="utf-8")
    assert decision(tmp_path, check).decision == CompletionDecision.REJECT_DONE
    (tmp_path / "test_required.py").write_text(
        SUITE.replace("self.assertEqual(answer(), 42)", "pass"), encoding="utf-8"
    )
    assert decision(tmp_path, check).decision == CompletionDecision.REJECT_DONE


@pytest.mark.parametrize("decorator", ["@unittest.skip('disabled')", "@unittest.expectedFailure"])
def test_skipped_and_expected_failure_mandatory_tests_cannot_pass(tmp_path, decorator):
    (tmp_path / "active.py").write_text("def answer(): return 00\n", encoding="utf-8")
    source = SUITE.replace("    def test_answer", f"    {decorator}\n    def test_answer")
    assert decision(tmp_path, suite(tmp_path, source)).decision == CompletionDecision.REJECT_DONE


def test_wrong_test_inventory_and_empty_suite_cannot_pass(tmp_path):
    (tmp_path / "active.py").write_text("def answer(): return 42\n", encoding="utf-8")
    check = suite(tmp_path)
    check.expected["test_ids"] = ["Required.test_other"]
    assert decision(tmp_path, check).decision == CompletionDecision.REJECT_DONE
    assert decision(tmp_path, suite(tmp_path, "import unittest\n")).decision == CompletionDecision.REJECT_DONE


def test_suite_dependencies_do_not_reuse_stale_bytecode(tmp_path):
    path = tmp_path / "active.py"
    path.write_text("def answer(): return 42\n", encoding="utf-8")
    stamp = path.stat().st_mtime_ns
    py_compile.compile(str(path), doraise=True)
    path.write_text("def answer(): return 00\n", encoding="utf-8")
    os.utime(path, ns=(stamp, stamp))
    assert decision(tmp_path, suite(tmp_path)).decision == CompletionDecision.REJECT_DONE


def test_generic_unmapped_preservation_obligation_requires_human(tmp_path):
    (tmp_path / "active.py").write_text("def answer(): return 42\n", encoding="utf-8")
    obligations = (
        CompletionObligation("complete", "Transform complete tokens.", "examples", ("behavior",), "full"),
        CompletionObligation("preserve", "Preserve incomplete tokens literally.", "examples", (), "unresolved"),
    )
    assert decision(tmp_path, behavior(), obligations=obligations).decision == CompletionDecision.HUMAN_REQUIRED
    covered = (
        obligations[0],
        CompletionObligation("preserve", "A bounded preservation example.", "examples", ("behavior",), "full"),
    )
    assert decision(tmp_path, behavior(), obligations=covered).decision == CompletionDecision.VERIFIED_DONE


@pytest.mark.parametrize(
    "scope,coverage,target,bindings",
    [
        ("broader_behavior", "full", "", ("behavior",)),
        ("examples", "partial", "", ("behavior",)),
        ("ambiguous", "unresolved", "", ("behavior",)),
        ("examples", "full", "legacy.py", ("behavior",)),
        ("examples", "full", "", ("missing",)),
    ],
)
def test_unresolved_or_misdirected_explicit_scope_fails_closed(tmp_path, scope, coverage, target, bindings):
    (tmp_path / "active.py").write_text("def answer(): return 42\n", encoding="utf-8")
    obligation = CompletionObligation("requirement", "Explicit requirement.", scope, bindings, coverage, target)
    assert decision(tmp_path, behavior(), obligations=(obligation,)).decision == CompletionDecision.HUMAN_REQUIRED


def test_loader_binds_explicit_obligations_and_new_evidence_types(tmp_path):
    data = {
        "task": "Modify active.py.",
        "conditions": [{"id": "changed", "kind": "file_changed", "path": "active.py", "baseline_sha256": "a" * 64}],
        "obligations": [
            {
                "id": "change",
                "text": "Modify active.py.",
                "scope": "file_state",
                "coverage": "full",
                "condition_ids": ["changed"],
                "target_path": "active.py",
            }
        ],
    }
    spec = tmp_path / "task.json"
    spec.write_text(json.dumps(data), encoding="utf-8")
    _, contract = _load_spec(spec)
    assert contract.obligations[0].target_path == "active.py"
    assert contract.conditions[0].expected == "a" * 64
    data["obligations"][0]["condition_ids"] = []
    spec.write_text(json.dumps(data), encoding="utf-8")
    assert _load_spec(spec)[1].readiness_gaps()


def test_unmapped_obligation_blocks_before_worker(tmp_path, monkeypatch):
    from truth_firewall.cli import main

    monkeypatch.chdir(tmp_path)
    data = {
        "task": "Return 42.",
        "conditions": [
            {
                "id": "behavior",
                "kind": "black_box",
                "path": "active.py",
                "symbol": "answer",
                "cases": [{"args": [], "expected": 42}],
            }
        ],
        "obligations": [
            {
                "id": "unproven",
                "text": "Preserve malformed tokens.",
                "scope": "examples",
                "coverage": "unresolved",
                "condition_ids": [],
            }
        ],
    }
    spec = tmp_path / "task.json"
    spec.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr("truth_firewall.pilot.run_supervised_task", lambda *a, **k: pytest.fail("worker launched"))
    assert main(["run", "--spec", str(spec)]) == 2
