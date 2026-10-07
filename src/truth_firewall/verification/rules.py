"""General evidence rules. Verdicts come from records, not from claim wording."""

from __future__ import annotations

import re
from datetime import datetime

from truth_firewall.evidence.collectors.test_runner import (
    _is_test_result_provenance,
    is_atomic_test_result,
    is_code_edit,
    parse_test_stdout,
    recognize_test_invocation,
    recognized_test_execution,
)
from truth_firewall.evidence_requirements import MINIMUM_EVIDENCE_TYPES
from truth_firewall.schemas import (
    Claim,
    ClaimAssessment,
    ClaimType,
    EpistemicStatus,
    EvidenceRecord,
    Verdict,
    parse_time,
)
from truth_firewall.workspace import normalize_workspace_path


def assess(
    claim: Claim,
    evidence: list[EvidenceRecord],
    *,
    now: datetime,
    freshness_seconds: int,
) -> ClaimAssessment:
    if claim.epistemic_status != EpistemicStatus.ASSERTED_FACT.value:
        return _assessment(
            claim,
            Verdict.NOT_CHECKABLE,
            [],
            "This statement is not presented as an established fact.",
            scope_match="not_applicable",
            freshness_ok=True,
            confidence=1.0,
        )
    required = MINIMUM_EVIDENCE_TYPES.get(claim.claim_type)
    evidence = [
        item
        for item in evidence
        if required is None or item.evidence_type in required or item.evidence_type == "process"
    ]
    if _is_lint_claim(claim):
        return _lint_result(claim, evidence, now, freshness_seconds)
    if claim.claim_type == ClaimType.EXTERNAL_FACT.value:
        return _assessment(
            claim,
            Verdict.NOT_CHECKABLE,
            [],
            "External facts are outside the local evidence plane.",
            scope_match="not_applicable",
            freshness_ok=True,
            confidence=0.4,
        )
    handler = {
        ClaimType.TEST_RESULT.value: _test_result,
        ClaimType.TEST_EXECUTION.value: _test_execution,
        ClaimType.EXISTENCE.value: _existence,
        ClaimType.FILE_STATE.value: _existence,
        ClaimType.ACTION_CHANGE.value: _action,
        ClaimType.ABSENCE.value: _absence,
        ClaimType.BEHAVIOR.value: _behavior,
        ClaimType.CAUSAL.value: _causal,
        ClaimType.COMPLETION.value: _completion,
        ClaimType.ENVIRONMENT.value: _environment,
    }.get(claim.claim_type, _unsupported)
    return handler(claim, evidence, now, freshness_seconds)


def _test_result(claim: Claim, evidence: list[EvidenceRecord], now: datetime, window: int) -> ClaimAssessment:
    if not is_atomic_test_result(claim.normalized_claim):
        return _unknown(claim, "A test result can verify only an atomic test outcome, not a compound response clause.")
    rows = []
    stale_receipts = []
    for record in evidence:
        if record.evidence_type not in {"test", "process"}:
            continue
        if not _is_test_result_provenance(record):
            continue
        summary = _summary(record)
        if summary is None and record.evidence_type != "test":
            continue
        fresh = _fresh(record, now, window)
        if fresh and _receipt_predates_edit(claim, record, evidence):
            stale_receipts.append(record)
            continue
        rows.append((record, summary, fresh))
    if not rows and stale_receipts:
        return _unknown(
            claim,
            "The test receipt predates a later relevant code edit, so it cannot establish the current result.",
            freshness_ok=False,
            limitations=("test_receipt_predates_code_edit",),
        )
    if not rows:
        return _unknown(claim, "No test evidence was retrieved for this claim.")
    fresh_rows = [row for row in rows if row[2]]
    if not fresh_rows:
        return _unknown(
            claim,
            "Test evidence is missing a usable timestamp or is stale.",
            freshness_ok=False,
            limitations=("stale_or_untimed_evidence",),
        )
    contradicted = []
    supported = []
    partial = []
    for record, summary, _ in fresh_rows:
        kind = _test_relation(claim, record, summary)
        if record.provenance.startswith(("codex_hook:", "cursor_hook:")) and kind in {"support", "contradict"}:
            # Host-captured shell output proves an invocation occurred, but a runner
            # name in argv does not authenticate an arbitrary local executable.
            kind = "partial"
        if kind == "contradict":
            if record.provenance.startswith(("codex_hook:", "cursor_hook:")):
                partial.append(record)
                continue
            contradicted.append(record)
        elif kind == "support":
            if record.provenance.startswith(("codex_hook:", "cursor_hook:")):
                partial.append(record)
            else:
                supported.append(record)
        elif kind == "partial":
            partial.append(record)
    if contradicted:
        passed = _summary(contradicted[0]) or {}
        shown = passed.get("passed")
        claimed = claim.attributes().get("quantity")
        detail = "The recorded test run conflicts with the claim."
        if isinstance(shown, int) and isinstance(claimed, int) and shown != claimed:
            detail = f"The recorded test run shows {shown} passed, not {claimed}."
        exit_code = contradicted[0].exit_code
        if exit_code not in (None, 0) and claim.attributes().get("outcome") == "pass":
            detail = f"The test command exited {exit_code}, which does not support a passing result."
        return _assessment(
            claim,
            Verdict.CONTRADICTED,
            [item.evidence_id for item in contradicted],
            detail,
            contradiction=True,
            confidence=0.9,
            limitations=("conflicting_test_record",),
        )
    if supported:
        return _assessment(
            claim,
            Verdict.VERIFIED,
            [item.evidence_id for item in supported],
            "A fresh test record matches the claimed outcome.",
            confidence=0.9,
            limitations=_test_limitations(supported[0]),
        )
    if partial:
        return _assessment(
            claim,
            Verdict.INFERRED,
            [item.evidence_id for item in partial],
            "Test evidence is related but does not fully match the claim.",
            confidence=0.5,
        )
    return _unknown(claim, "Test evidence does not match this claim.")


def _test_relation(claim: Claim, record: EvidenceRecord, summary: dict | None) -> str:
    scope = _run_scope(claim)
    invocation = recognize_test_invocation(record.command)
    if invocation is None:
        return "none"
    target_path = _claim_test_target(claim)
    if scope == "full" and invocation.scope != "full":
        return "none"
    if scope == "targeted":
        if invocation.scope != "scoped" or not target_path:
            return "none"
        if not any(_same_scope_path(target_path, target) for target in invocation.targets):
            return "none"
    elif scope == "partial" and invocation.scope != "scoped":
        return "none"
    elif scope not in {"full", "partial", "targeted"} or invocation.scope == "unknown":
        return "none"
    if summary is None:
        return "none"
    payload = record.structured_payload()
    stdout_summary = parse_test_stdout(record.stdout, command=record.command)
    if _counts_disagree(payload, stdout_summary):
        return "contradict"
    claimed = _claimed_counts(claim)
    passed = summary.get("passed")
    failed = summary.get("failed")
    exit_code = record.exit_code
    if claimed.get("outcome", "pass") == "pass":
        conflicts = False
        if isinstance(failed, int) and failed > 0 and claimed.get("failed") in (None, 0):
            conflicts = True
        if isinstance(claimed.get("failed"), int) and claimed["failed"] != failed and isinstance(failed, int):
            conflicts = True
        if exit_code not in (None, 0) and claimed.get("outcome") == "pass":
            conflicts = True
        if isinstance(claimed.get("passed"), int) and isinstance(passed, int) and passed != claimed["passed"]:
            conflicts = True
        if isinstance(claimed.get("deselected"), int) and summary.get("deselected") != claimed["deselected"]:
            conflicts = True
        if conflicts:
            return "contradict"
        if not _claim_is_aggregate(claim) and "passed" not in claimed:
            return "none"
        has_signal = isinstance(passed, int) or summary.get("unittest_ok") is True
        if not has_signal or not record.command:
            return "none"
        if "passed" in claimed and not isinstance(passed, int):
            return "partial"
        if "passed" not in claimed and not isinstance(passed, int) and summary.get("unittest_ok") is not True:
            return "partial" if exit_code == 0 else "none"
        failed_ok = claimed.get("failed", 0) == 0 and (failed in (0, None) or failed == claimed.get("failed"))
        if isinstance(failed, int) and failed > 0:
            return "contradict"
        status_ok = exit_code == 0
        if failed_ok and status_ok and scope != "full":
            return "support"
        if failed_ok and status_ok and scope == "full" and "passed" in claimed:
            return "support"
        if (
            failed_ok
            and status_ok
            and scope == "full"
            and summary.get("unittest_ok") is True
            and "passed" not in claimed
        ):
            return "partial"
        if exit_code == 0 and "passed" not in claimed:
            return "partial"
    return "none"


def _lint_result(claim: Claim, evidence: list[EvidenceRecord], now: datetime, window: int) -> ClaimAssessment:
    rows = []
    for record in evidence:
        if record.evidence_type not in {"test", "process"}:
            continue
        identity = record.structured_payload().get("runner_identity")
        if record.tool_attestation is not True or not isinstance(identity, dict) or identity.get("trusted") is not True:
            continue
        summary = _summary(record) or {}
        if "lint_tool" not in summary:
            continue
        if not _fresh(record, now, window):
            continue
        rows.append((record, summary))
    if not rows:
        return _unknown(claim, "No recognized lint command output was retrieved.")
    clean = []
    dirty = []
    uncertain = []
    for record, summary in rows:
        if summary.get("parse_confidence") == "low" or "lint_success" not in summary:
            uncertain.append(record)
            continue
        if (
            summary.get("lint_success") is True
            and record.exit_code in (0, None)
            and not _lint_violation_text(record.stdout)
        ):
            if record.exit_code == 0:
                clean.append(record)
            else:
                uncertain.append(record)
        elif summary.get("lint_success") is False or (record.exit_code not in (0, None)):
            dirty.append(record)
        else:
            uncertain.append(record)
    if dirty and not clean:
        return _contradict(claim, dirty, "The lint command reported violations or a failing status.")
    if clean:
        return _assessment(
            claim,
            Verdict.VERIFIED,
            [item.evidence_id for item in clean],
            "A recognized lint command finished with no violations.",
            confidence=0.9,
        )
    if uncertain:
        return _assessment(
            claim,
            Verdict.INFERRED,
            [item.evidence_id for item in uncertain],
            "A lint command was recorded, but its output is not a clear success or failure.",
            confidence=0.4,
        )
    return _unknown(claim, "Lint output does not confirm this claim.")


def _is_lint_claim(claim: Claim) -> bool:
    text = f"{claim.normalized_claim} {claim.subject}"
    return re.search(r"\b(lint|ruff|flake8|pylint|mypy|eslint)\b", text, re.IGNORECASE) is not None


def _lint_violation_text(stdout: str) -> bool:
    cleaned = re.sub(r"\x1b\[[0-9;]*m", "", stdout or "")
    if re.search(r"All checks passed!?", cleaned, re.IGNORECASE):
        return False
    return re.search(r"\b[A-Z]\d{3,4}\b", cleaned) is not None and "Found" in cleaned


def _claimed_counts(claim: Claim) -> dict:
    attrs = claim.attributes()
    text = f"{claim.normalized_claim} {claim.claim_object}"
    parsed = parse_test_stdout(text)
    counts: dict = {}
    tests_passed = re.search(r"(\d+)\s+tests?\s+passed\b", text, re.IGNORECASE)
    if isinstance(attrs.get("quantity"), int):
        counts["passed"] = attrs["quantity"]
    elif isinstance(parsed.get("passed"), int):
        counts["passed"] = parsed["passed"]
    elif tests_passed:
        counts["passed"] = int(tests_passed.group(1))
    if isinstance(parsed.get("failed"), int):
        counts["failed"] = parsed["failed"]
    elif isinstance(attrs.get("failed"), int):
        counts["failed"] = attrs["failed"]
    elif attrs.get("outcome") == "fail":
        counts["outcome"] = "fail"
    if isinstance(parsed.get("deselected"), int):
        counts["deselected"] = parsed["deselected"]
    if "outcome" not in counts:
        counts["outcome"] = "fail" if attrs.get("outcome") == "fail" else "pass"
    return counts


def _claim_is_aggregate(claim: Claim) -> bool:
    text = f"{claim.subject} {claim.normalized_claim}".lower()
    if re.search(r"\b(tests?|suite|run)\b", text):
        return True
    return False


def _run_scope(claim: Claim) -> str:
    text = f"{claim.raw_text} {claim.normalized_claim}".lower()
    if _claim_test_target(claim):
        return "targeted"
    if re.search(r"\b(follow-up|follow up|rerun|re-run|selected|filtered|subset)\b", text):
        return "partial"
    # An unqualified test-result assertion speaks for the suite as a whole.
    return "full"


def _command_is_partial(command: str) -> bool:
    invocation = recognize_test_invocation(command)
    return invocation is None or invocation.scope != "full"


def _claim_test_target(claim: Claim) -> str:
    text = f"{claim.raw_text} {claim.normalized_claim}"
    match = re.search(r"\b(?:in|within|under)\s+([A-Za-z0-9_./\\-]+\.[A-Za-z0-9]+)(?=\b|/)", text)
    if match:
        return match.group(1).rstrip(".")
    target = claim.attributes().get("target_path")
    return str(target) if isinstance(target, str) and target else ""


def _same_scope_path(claim_target: str, command_target: str) -> bool:
    def normalize(value: str) -> str:
        return value.replace("\\", "/").removeprefix("./").rstrip("/").lower()

    wanted = normalize(claim_target)
    actual = normalize(command_target)
    return bool(wanted and actual and (actual == wanted or actual.endswith("/" + wanted)))


def _receipt_predates_edit(
    claim: Claim,
    receipt: EvidenceRecord,
    evidence: list[EvidenceRecord],
) -> bool:
    receipt_time = parse_time(receipt.timestamp)
    if receipt_time is None:
        return False
    for edit in evidence:
        if not is_code_edit(edit):
            continue
        edit_time = parse_time(edit.timestamp)
        if edit_time is None:
            continue
        if edit_time > receipt_time:
            return True
        if edit_time == receipt_time:
            if edit.sequence is None or receipt.sequence is None:
                return True
            if edit.sequence > receipt.sequence:
                return True
    return False


def _counts_disagree(payload: dict, stdout_summary: dict) -> bool:
    for key in ("passed", "failed"):
        if key in payload and key in stdout_summary and payload[key] != stdout_summary[key]:
            return True
    return False


def _summary(record: EvidenceRecord) -> dict | None:
    payload = record.structured_payload()
    parsed = parse_test_stdout(record.stdout, command=record.command)
    summary = dict(parsed)
    for key in (
        "passed",
        "failed",
        "skipped",
        "deselected",
        "xfailed",
        "errors",
        "unittest_ok",
        "framework",
        "collected",
        "lint_success",
        "lint_tool",
        "lint_violations",
        "parser_name",
        "parse_confidence",
        "success",
    ):
        if key in payload and payload[key] is not None:
            summary[key] = payload[key]
    return summary or None


def _test_limitations(record: EvidenceRecord) -> tuple[str, ...]:
    if record.exit_code is None:
        return ("exit_code_not_recorded",)
    return ()


def _test_execution(claim: Claim, evidence: list[EvidenceRecord], now: datetime, window: int) -> ClaimAssessment:
    command = str(claim.attributes().get("command") or "")
    claimed_runner = recognize_test_invocation(command)
    if claimed_runner is None or claimed_runner.scope == "unknown":
        return _unknown(claim, "The claimed command is not a recognized direct test-runner invocation.")
    matches = []
    for record in evidence:
        if record.evidence_type not in {"test", "process"}:
            continue
        observed_runner = recognized_test_execution(record)
        if observed_runner is None or observed_runner.framework != claimed_runner.framework:
            continue
        observed_target = set(observed_runner.targets)
        claimed_target = set(claimed_runner.targets)
        if claimed_target and not claimed_target.issubset(observed_target):
            continue
        if not _fresh(record, now, window):
            continue
        matches.append(record)
    if not matches:
        return _unknown(claim, "No fresh record shows this command running.")
    finished = [record for record in matches if record.exit_code is not None]
    chosen = finished or matches
    if finished:
        return _assessment(
            claim,
            Verdict.VERIFIED,
            [chosen[0].evidence_id],
            "A fresh process record shows the command ran.",
            confidence=0.85,
        )
    return _assessment(
        claim,
        Verdict.INFERRED,
        [chosen[0].evidence_id],
        "A command record exists, but its exit status was not recorded.",
        confidence=0.45,
        limitations=("exit_code_not_recorded",),
    )


def _existence(claim: Claim, evidence: list[EvidenceRecord], now: datetime, window: int) -> ClaimAssessment:
    path = str(claim.attributes().get("target_path") or "")
    if not path:
        return _unknown(claim, "The existence claim has no target path.")
    matches = [record for record in evidence if record.evidence_type == "filesystem" and _same_path(path, record)]
    distinct = {record.file_path for record in matches if record.file_path}
    if len(distinct) > 1:
        return _unknown(claim, "More than one filesystem record matches this path.")
    fresh = [record for record in matches if _fresh(record, now, window)]
    if not fresh:
        return _unknown(claim, "No fresh filesystem observation matches this path.", freshness_ok=False)
    polarity = claim.attributes().get("polarity") or "present"
    observed = [record.structured_payload().get("exists") for record in fresh]
    if any(not isinstance(value, bool) for value in observed):
        return _unknown(
            claim,
            "The filesystem observation has no schema-valid boolean existence value.",
            evidence_ids=[record.evidence_id for record in fresh],
            limitations=("invalid_filesystem_schema",),
        )
    exists_values = observed
    if polarity == "absent":
        if any(exists_values) and not all(exists_values):
            return _contradict(claim, fresh, "Filesystem records disagree about existence.")
        if any(exists_values):
            return _contradict(claim, fresh, "The file was observed and the claim says it is absent.")
        if exists_values and not any(exists_values):
            return _assessment(
                claim,
                Verdict.VERIFIED,
                [fresh[0].evidence_id],
                "A filesystem observation shows the path is absent.",
                confidence=0.9,
            )
    if any(exists_values) and not all(exists_values):
        return _contradict(claim, fresh, "Filesystem records disagree about existence.")
    if exists_values and not any(exists_values):
        return _contradict(claim, fresh, "The path was observed as absent.")
    if any(exists_values):
        return _assessment(
            claim,
            Verdict.VERIFIED,
            [item.evidence_id for item in fresh if item.structured_payload().get("exists")],
            "A filesystem observation shows the path exists.",
            confidence=0.9,
        )
    return _unknown(claim, "The filesystem record does not show existence.")


def _action(claim: Claim, evidence: list[EvidenceRecord], now: datetime, window: int) -> ClaimAssessment:
    path = str(claim.attributes().get("target_path") or "")
    if not path:
        return _unknown(claim, "The action claim has no target path.")
    related = [record for record in evidence if _same_path(path, record) and _fresh(record, now, window)]
    if not related:
        return _unknown(claim, "No fresh change evidence matches this path.")
    creation = _is_creation(claim)
    if _has_semantic_goal(claim.normalized_claim):
        return _unknown(
            claim,
            "File edits or creation do not establish that the described semantic goal or content was achieved.",
            evidence_ids=[record.evidence_id for record in related],
            limitations=("semantic_goal_not_proven",),
        )
    if not creation and re.search(
        r"\b(?:implemented|fixed|repaired|removed|renamed|disabled|enabled|added)\b",
        claim.normalized_claim,
        re.I,
    ):
        return _unknown(
            claim,
            "An edit event proves that a file changed, not that the described behavior or feature was implemented.",
            evidence_ids=[record.evidence_id for record in related],
            limitations=("implementation_semantics_not_proven",),
        )
    symbol = str(claim.attributes().get("symbol") or "")
    if creation:
        if any(_proves_creation(record) for record in related):
            return _assessment(
                claim,
                Verdict.VERIFIED,
                [record.evidence_id for record in related if _proves_creation(record)],
                "A before/after or create event shows the file was created.",
                confidence=0.85,
            )
        if any(_is_change_event(record) for record in related):
            return _assessment(
                claim,
                Verdict.INFERRED,
                [record.evidence_id for record in related if _is_change_event(record)],
                "A write event exists, but creation was not established.",
                confidence=0.4,
                limitations=("creation_not_proven",),
            )
        return _unknown(
            claim,
            "File existence does not prove creation.",
            evidence_ids=[record.evidence_id for record in related],
            limitations=("existence_is_not_creation",),
        )
    if not _has_semantic_goal(claim.normalized_claim) and re.search(
        r"\b(?:edited|modified|updated)\b", claim.normalized_claim, re.I
    ):
        editors = [record for record in related if _is_change_event(record)]
    else:
        editors = [record for record in related if _edit_supports(record, symbol)]
    if editors:
        semantic = _semantic_change_intent(claim.normalized_claim)
        if semantic is None:
            return _assessment(
                claim,
                Verdict.VERIFIED,
                [record.evidence_id for record in editors],
                "A recorded edit matches this path.",
                confidence=0.8,
            )
        direction, terms = semantic
        entailing = [record for record in editors if _edit_entails_change(record, direction, terms)]
        if entailing:
            return _assessment(
                claim,
                Verdict.VERIFIED,
                [record.evidence_id for record in entailing],
                "Structured before/after content establishes the claimed semantic change.",
                confidence=0.8,
            )
        return _unknown(
            claim,
            "The edit is related, but its before/after content does not establish the claimed semantic change.",
            evidence_ids=[record.evidence_id for record in editors],
            limitations=("semantic_change_not_proven",),
        )
    if any(record.evidence_type == "filesystem" for record in related):
        return _unknown(
            claim,
            "File existence does not prove this action.",
            evidence_ids=[record.evidence_id for record in related],
            limitations=("existence_is_not_action",),
        )
    return _unknown(claim, "No edit event supports this action.")


def _absence(claim: Claim, evidence: list[EvidenceRecord], now: datetime, window: int) -> ClaimAssessment:
    query = str(claim.attributes().get("query") or "")
    if not query:
        return _unknown(claim, "The absence claim has no search query.")
    searches = []
    for record in evidence:
        payload = record.structured_payload()
        if payload.get("kind") != "search" or payload.get("query") != query:
            continue
        searches.append(record)
    if not searches:
        return _unknown(claim, "No search evidence matches this query.")
    scope_kind = str(claim.attributes().get("scope_kind") or "unspecified")
    if scope_kind == "unspecified":
        return _unknown(claim, "The absence scope is unspecified, so a search cannot verify it.")
    contradicted = []
    supported = []
    narrower = False
    for record in searches:
        payload = record.structured_payload()
        count = payload.get("match_count")
        matches = payload.get("matches") or []
        if not isinstance(count, int):
            continue
        if count == 0 and matches:
            continue
        if count > 0 or matches:
            contradicted.append(record)
            continue
        if not _fresh(record, now, window):
            continue
        if _search_covers(payload, claim):
            supported.append(record)
        else:
            narrower = True
    if contradicted:
        return _contradict(claim, contradicted, "The search recorded matches for this query.")
    if supported:
        return _assessment(
            claim,
            Verdict.VERIFIED,
            [record.evidence_id for record in supported],
            "A scoped search recorded zero matches.",
            confidence=0.8,
            limitations=("Only the searched root and query are covered.",),
            scope_match="matched",
        )
    if narrower:
        return _unknown(
            claim,
            "The search root is narrower than the claim scope.",
            scope_match="narrower_evidence",
            limitations=("search_scope_too_narrow",),
        )
    return _unknown(claim, "Search evidence does not cover this absence claim.", freshness_ok=False)


def _behavior(claim: Claim, evidence: list[EvidenceRecord], now: datetime, window: int) -> ClaimAssessment:
    symbol = str(claim.attributes().get("symbol") or claim.subject or "")
    related = [record for record in evidence if symbol and symbol in _blob(record) and _fresh(record, now, window)]
    if not related:
        return _unknown(claim, "No evidence tied to this behavior was retrieved.")
    return _assessment(
        claim,
        Verdict.INFERRED,
        [record.evidence_id for record in related],
        "Finite checks do not verify a universal behavior claim."
        if claim.attributes().get("universal")
        else "Related tests or code support this only as an inference.",
        confidence=0.45,
        limitations=("behavior_not_exhaustively_verified",),
    )


def _causal(claim: Claim, evidence: list[EvidenceRecord], now: datetime, window: int) -> ClaimAssessment:
    factor = str(claim.attributes().get("factor") or claim.claim_object or "")
    experiments = []
    related = []
    for record in evidence:
        if not _fresh(record, now, window):
            continue
        payload = record.structured_payload()
        if payload.get("kind") == "causal_experiment" and payload.get("controlled") is True:
            if factor and factor.casefold() == str(payload.get("factor") or "").casefold():
                with_factor = _causal_outcome(payload.get("outcome_with_factor"))
                without_factor = _causal_outcome(payload.get("outcome_without_factor"))
                if with_factor == "failure" and without_factor == "success":
                    experiments.append(record)
        elif factor and factor.lower() in _blob(record).lower():
            related.append(record)
    if experiments:
        return _assessment(
            claim,
            Verdict.VERIFIED,
            [record.evidence_id for record in experiments],
            "A recorded discriminating experiment matches this factor.",
            confidence=0.8,
        )
    if related:
        return _assessment(
            claim,
            Verdict.INFERRED,
            [record.evidence_id for record in related],
            "Related artifacts are not a discriminating causal test.",
            confidence=0.35,
            limitations=("correlation_is_not_causation",),
        )
    return _unknown(claim, "No discriminating causal evidence was retrieved.")


def _causal_outcome(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().casefold()
    if normalized in {"fail", "fails", "failed", "failure", "error", "broken"}:
        return "failure"
    if normalized in {"pass", "passes", "passed", "success", "succeeds", "healthy"}:
        return "success"
    return None


def _completion(claim: Claim, evidence: list[EvidenceRecord], now: datetime, window: int) -> ClaimAssessment:
    tests = [
        record for record in evidence if record.evidence_type in {"test", "process"} and _fresh(record, now, window)
    ]
    if not tests:
        return _unknown(claim, "No fresh test evidence was retrieved for this completion claim.")
    return _unknown(
        claim,
        "A passing test run does not verify that the work is complete or the bug is fixed.",
        evidence_ids=[tests[0].evidence_id],
        limitations=("passing_tests_are_not_completion",),
    )


def _environment(claim: Claim, evidence: list[EvidenceRecord], now: datetime, window: int) -> ClaimAssessment:
    token = (claim.claim_object or claim.normalized_claim).strip()
    for record in evidence:
        if record.evidence_type != "process" or not _fresh(record, now, window):
            continue
        if token and token in record.stdout and record.exit_code == 0:
            return _assessment(
                claim,
                Verdict.VERIFIED,
                [record.evidence_id],
                "Process output matches the environment claim.",
                confidence=0.8,
            )
    return _unknown(claim, "No process output matches this environment claim.")


def _unsupported(claim: Claim, evidence: list[EvidenceRecord], now: datetime, window: int) -> ClaimAssessment:
    return _unknown(claim, "No general rule verifies this claim type.")


def _is_creation(claim: Claim) -> bool:
    text = f"{claim.predicate} {claim.normalized_claim}".lower()
    return any(verb in text for verb in ("created", "added", "wrote"))


def _proves_creation(record: EvidenceRecord) -> bool:
    payload = record.structured_payload()
    # A snapshot can directly prove current existence, but caller-supplied
    # `existed_before` is not an independently observed historical fact.
    # Creation requires a structured change event from the evidence plane.
    return payload.get("change_type") == "created" and payload.get("exists") is True


def _is_change_event(record: EvidenceRecord) -> bool:
    payload = record.structured_payload()
    return payload.get("kind") in {"file_edit", "git_status"} or payload.get("change_type") in {
        "edited",
        "created",
    }


def _edit_supports(record: EvidenceRecord, symbol: str) -> bool:
    payload = record.structured_payload()
    if payload.get("kind") not in {"file_edit", "diff"} and payload.get("change_type") not in {
        "edited",
        "created",
    }:
        return False
    if not symbol:
        return True
    blob = _blob(record)
    if symbol:
        return re.search(rf"(?<![\w]){re.escape(symbol)}(?![\w])", blob, re.I) is not None
    return True


def _semantic_change_intent(text: str) -> tuple[str, tuple[str, ...]] | None:
    match = re.search(r"\bto\s+(remove|delete|disable|add|introduce|enable)\s+(.+?)(?:[.!?]|$)", text, re.I)
    if not match:
        return None
    direction = "remove" if match.group(1).lower() in {"remove", "delete", "disable"} else "add"
    ignored = {"the", "a", "an", "and", "or", "from", "into", "in", "on", "of", "for", "to"}
    terms = tuple(
        token.lower() for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", match.group(2)) if token.lower() not in ignored
    )
    return (direction, terms) if terms else None


def _has_semantic_goal(text: str) -> bool:
    # A plain edit/create assertion can be proven by an operation receipt.
    # Any attached purpose/content/result clause is a separate proposition and
    # must not inherit VERIFIED merely because the file changed.
    return bool(
        re.search(r"\bto\s+[A-Za-z][A-Za-z_-]*\b", text, re.I)
        or re.search(r"\b(?:with|containing|includes?|documents?|documenting|explaining)\s+\w+", text, re.I)
        or re.search(
            r",\s*(?:fixing|repairing|renaming|removing|deleting|adding|introducing|enabling|disabling|"
            r"documenting|explaining|configuring|changing|updating|implementing)\b",
            text,
            re.I,
        )
    )


def _edit_entails_change(record: EvidenceRecord, direction: str, terms: tuple[str, ...]) -> bool:
    payload = record.structured_payload()
    before = str(payload.get("snippet_old") or payload.get("before") or "")
    after = str(payload.get("snippet_new") or payload.get("after") or "")
    if not before or not after:
        return False
    before_tokens = {token.lower() for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", before)}
    after_tokens = {token.lower() for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", after)}
    present_before = all(term in before_tokens for term in terms)
    present_after = all(term in after_tokens for term in terms)
    return present_before and not present_after if direction == "remove" else present_after and not present_before


def _search_covers(payload: dict, claim: Claim) -> bool:
    scope_kind = claim.attributes().get("scope_kind")
    scope_path = str(claim.attributes().get("scope_path") or "").replace("\\", "/").strip("./")
    root = str(payload.get("root") or "").replace("\\", "/").strip("./")
    if scope_kind == "workspace":
        return (
            bool(payload.get("covers_workspace"))
            and isinstance(payload.get("include_patterns"), list)
            and isinstance(payload.get("exclude_patterns"), list)
            and bool(root)
        )
    if not scope_path:
        return False
    if root in {"", "."}:
        return True
    return scope_path == root or scope_path.startswith(root.rstrip("/") + "/")


def _same_path(claim_path: str, record: EvidenceRecord) -> bool:
    claim_norm = normalize_workspace_path(claim_path, record.workspace_root)
    if claim_norm is None:
        return False
    payload = record.structured_payload()
    for candidate in (record.file_path, payload.get("path"), payload.get("file_path")):
        if not isinstance(candidate, str):
            continue
        norm = normalize_workspace_path(candidate, record.workspace_root)
        if norm is not None and norm == claim_norm:
            return True
    return False


def _blob(record: EvidenceRecord) -> str:
    payload = record.structured_payload()
    return "\n".join(
        [
            record.stdout,
            record.file_path or "",
            str(payload.get("snippet_new") or ""),
            str(payload.get("snippet_old") or ""),
            str(payload.get("diff") or ""),
            str(payload.get("excerpt") or ""),
            str(payload.get("factor") or ""),
            str(payload.get("path") or ""),
        ]
    )


def _fresh(record: EvidenceRecord, now: datetime, window: int) -> bool:
    stamped = parse_time(record.timestamp)
    if stamped is None:
        return False
    age = (now - stamped).total_seconds()
    return 0 <= age <= window


def _test_limitations_ok(record: EvidenceRecord) -> tuple[str, ...]:
    return _test_limitations(record)


def _unknown(
    claim: Claim,
    explanation: str,
    *,
    freshness_ok: bool = True,
    evidence_ids: list[str] | None = None,
    limitations: tuple[str, ...] = (),
    scope_match: str = "missing",
) -> ClaimAssessment:
    return _assessment(
        claim,
        Verdict.UNKNOWN,
        evidence_ids or [],
        explanation,
        freshness_ok=freshness_ok,
        confidence=0.2,
        limitations=limitations,
        scope_match=scope_match,
    )


def _contradict(claim: Claim, records: list[EvidenceRecord], explanation: str) -> ClaimAssessment:
    return _assessment(
        claim,
        Verdict.CONTRADICTED,
        [record.evidence_id for record in records],
        explanation,
        contradiction=True,
        confidence=0.9,
    )


def _assessment(
    claim: Claim,
    verdict: Verdict,
    evidence_ids: list[str],
    explanation: str,
    *,
    scope_match: str = "not_applicable",
    freshness_ok: bool = True,
    contradiction: bool = False,
    confidence: float = 0.5,
    limitations: tuple[str, ...] = (),
) -> ClaimAssessment:
    return ClaimAssessment(
        claim_id=claim.claim_id,
        verdict=verdict.value,
        evidence_ids=tuple(evidence_ids),
        explanation=explanation,
        scope_match=scope_match,
        freshness_ok=freshness_ok,
        contradiction=contradiction,
        confidence=confidence,
        limitations=limitations,
    )
