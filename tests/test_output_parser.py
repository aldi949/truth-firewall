from tests.conftest import NOW, evidence
from truth_firewall.evidence.collectors.test_runner import parse_test_stdout
from truth_firewall.pipeline import check_response


def test_pytest_summary_passed_in_duration():
    parsed = parse_test_stdout("35 passed in 0.40s\n", command="pytest -q")
    assert parsed["passed"] == 35
    assert parsed["parser_name"] == "pytest-summary"


def test_pytest_summary_passed_and_deselected():
    parsed = parse_test_stdout("35 passed, 1 deselected in 0.2s\n", command="pytest -q")
    assert parsed["passed"] == 35
    assert parsed["deselected"] == 1


def test_pytest_summary_failed_and_passed():
    parsed = parse_test_stdout("2 failed, 8 passed in 0.3s\n", command="pytest")
    assert parsed["failed"] == 2
    assert parsed["passed"] == 8
    assert parsed["success"] is False


def test_unittest_ok_summary():
    parsed = parse_test_stdout("Ran 10 tests in 0.01s\n\nOK\n", command="python -m unittest")
    assert parsed["collected"] == 10
    assert parsed["passed"] == 10
    assert parsed["failed"] == 0
    assert parsed["unittest_ok"] is True


def test_unittest_failure_summary():
    parsed = parse_test_stdout("Ran 4 tests in 0.02s\n\nFAILED (failures=1)\n", command="python -m unittest")
    assert parsed["failed"] == 1
    assert parsed["unittest_ok"] is False


def test_ruff_clean_success():
    parsed = parse_test_stdout("All checks passed!\n", command="ruff check src tests")
    assert parsed["lint_success"] is True
    assert parsed["lint_violations"] == 0
    assert parsed["lint_tool"] == "ruff"


def test_ruff_violation_output():
    parsed = parse_test_stdout("Found 3 errors.\n", command="ruff check src")
    assert parsed["lint_success"] is False
    assert parsed["lint_violations"] == 3


def test_exit_code_alone_does_not_invent_counts():
    parsed = parse_test_stdout("done\n", command="pytest -q")
    assert "passed" not in parsed
    record = evidence(stdout="done\n", exit_code=0, command="pytest -q", structured_payload={"kind": "test_run"})
    result = check_response("12 tests passed.", [record], offline=True, write_audit_log=False, now=NOW)
    assert result.assessments[0].verdict != "VERIFIED"


def test_exact_count_mismatch_is_not_verified():
    record = evidence(stdout="8 passed in 0.1s\n", command="pytest -q", structured_payload={"kind": "test_run"})
    result = check_response("All 12 tests passed.", [record], offline=True, write_audit_log=False, now=NOW)
    assert result.assessments[0].verdict == "CONTRADICTED"


def test_partial_command_does_not_verify_full_suite_claim():
    record = evidence(
        stdout="8 passed, 4 deselected in 0.2s\n",
        command="pytest -q -k sample",
        structured_payload={"kind": "test_run"},
    )
    result = check_response(
        "The entire test suite passed.",
        [record],
        offline=True,
        write_audit_log=False,
        now=NOW,
        workspace_root=".",
    )
    assert result.assessments[0].verdict != "VERIFIED"


def test_selected_run_count_can_verify_a_scoped_claim():
    record = evidence(
        stdout="8 passed in 0.1s\n",
        command="pytest -q -k sample",
        structured_payload={"kind": "test_run"},
    )
    result = check_response(
        "The selected tests finished with 8 passed.",
        [record],
        offline=True,
        write_audit_log=False,
        now=NOW,
        workspace_root=".",
    )
    assert result.assessments[0].verdict == "VERIFIED"


def test_lint_claim_verifies_from_ruff_not_from_any_zero_exit():
    lint = evidence(
        evidence_id="lint-1",
        evidence_type="process",
        command="ruff check src",
        stdout="All checks passed!\n",
        exit_code=0,
        structured_payload={"kind": "process"},
    )
    other = evidence(
        evidence_id="other-1",
        evidence_type="process",
        command="echo ok",
        stdout="ok\n",
        exit_code=0,
        structured_payload={"kind": "process"},
    )
    verified = check_response("The lint check passed.", [lint], offline=True, write_audit_log=False, now=NOW)
    assert verified.assessments[0].verdict == "VERIFIED"
    not_lint = check_response("The lint check passed.", [other], offline=True, write_audit_log=False, now=NOW)
    assert not_lint.assessments[0].verdict != "VERIFIED"


def test_pytest_progress_line_counts_glyphs_only_for_pytest():
    parsed = parse_test_stdout("..F.  [75%]\n", command="pytest -q")
    assert parsed["passed"] == 3
    assert parsed["failed"] == 1
    padded = parse_test_stdout(".....                                      [100%]\n", command="pytest -q")
    assert padded["passed"] == 5
    assert padded["failed"] == 0
    assert padded["success"] is True
    ignored = parse_test_stdout(".... [100%]\n", command="echo pytest")
    assert "passed" not in ignored
