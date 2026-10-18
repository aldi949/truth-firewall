"""Small local runner for bounded Python tasks using the Codex CLI."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from uuid import uuid4

from truth_firewall.completion import (
    AcceptanceCondition,
    CompletionObligation,
    TaskCompletionContract,
    acceptance_content_digest,
)
from truth_firewall.lifecycle import TaskRun, TerminalOutcome, run_supervised_task
from truth_firewall.platform_runtime import discover_codex
from truth_firewall.supervised_worker import WorkerProcessSpec


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def _known_fields(row: dict, allowed: set[str], context: str) -> None:
    unknown = set(row) - allowed
    if unknown:
        raise ValueError(f"unsupported {context} field(s): {', '.join(sorted(unknown))}")


def _load_spec(path: Path) -> tuple[dict, TaskCompletionContract]:
    data = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_json_object)
    if not isinstance(data, dict):
        raise ValueError("task spec must be a JSON object")
    _known_fields(data, {"task_id", "task_type", "task", "conditions", "acceptance_review",
                         "acceptance_digest", "onboarding_note", "obligations"}, "task")
    task = data.get("task")
    task_type = data.get("task_type", "python")
    task_id = data.get("task_id", "pilot-task")
    rows = data.get("conditions")
    if task_type != "python":
        raise ValueError("only task_type 'python' is supported in this pilot")
    if not isinstance(task, str) or not task.strip():
        raise ValueError("task must be a nonempty string")
    if not isinstance(task_id, str) or not task_id.strip():
        raise ValueError("task_id must be a nonempty string")
    if not isinstance(rows, list) or not rows:
        raise ValueError("conditions must be a nonempty list of mandatory checks")

    conditions: list[AcceptanceCondition] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f"condition {index + 1} must be a JSON object")
        condition_id = row.get("id")
        kind = row.get("kind")
        file_path = row.get("path", "")
        symbol = row.get("symbol", "")
        if not isinstance(condition_id, str) or not condition_id.strip():
            raise ValueError(f"condition {index + 1} needs a nonempty id")
        supported_kinds = {"file_exists", "file_changed", "python_function", "black_box", "unittest"}
        if not isinstance(kind, str) or kind not in supported_kinds:
            raise ValueError(f"condition {condition_id!r} has unsupported kind {kind!r}")
        common_fields = {"id", "kind", "path", "description"}
        kind_fields = {
            "file_exists": {"exists"}, "file_changed": {"baseline_sha256"},
            "python_function": {"symbol", "parameters"},
            "black_box": {"symbol", "cases"}, "unittest": {"suite"},
        }
        _known_fields(row, common_fields | kind_fields[kind], f"condition {condition_id!r}")
        if not isinstance(file_path, str) or not file_path:
            raise ValueError(f"condition {condition_id!r} needs a repository-relative path")
        expected = row.get("expected")
        cases: tuple[tuple[object, object], ...] = ()
        if kind == "file_exists":
            expected = row.get("exists", True)
            if type(expected) is not bool:
                raise ValueError(f"condition {condition_id!r} exists must be true or false")
        elif kind in {"file_changed", "unittest"}:
            # Detailed evidence readiness is checked fail-closed by the verifier.
            expected = row.get("baseline_sha256") if kind == "file_changed" else row.get("suite")
            if kind == "unittest" and isinstance(expected, dict):
                _known_fields(expected, {"sha256", "test_ids"}, f"condition {condition_id!r} suite")
        elif kind == "python_function":
            parameters = row.get("parameters")
            if not isinstance(symbol, str) or not symbol.isidentifier():
                raise ValueError(f"condition {condition_id!r} needs a Python function symbol")
            if not isinstance(parameters, list) or not all(isinstance(item, str) for item in parameters):
                raise ValueError(f"condition {condition_id!r} parameters must be a list of names")
            expected = parameters
        else:
            if not isinstance(symbol, str) or not symbol.isidentifier():
                raise ValueError(f"condition {condition_id!r} needs a Python function symbol")
            raw_cases = row.get("cases")
            if not isinstance(raw_cases, list) or not raw_cases:
                raise ValueError(f"condition {condition_id!r} needs at least one args/expected case")
            parsed_cases = []
            for case_number, case in enumerate(raw_cases):
                if (not isinstance(case, dict) or not isinstance(case.get("args"), list)
                        or "expected" not in case):
                    raise ValueError(f"condition {condition_id!r} case {case_number + 1} needs args and expected")
                _known_fields(case, {"args", "expected"}, f"condition {condition_id!r} case {case_number + 1}")
                parsed_cases.append((tuple(case["args"]), case["expected"]))
            cases = tuple(parsed_cases)
        conditions.append(AcceptanceCondition(condition_id, kind, file_path, symbol, expected, cases, True))

    review = data.get("acceptance_review")
    if review is None:
        note = data.get("onboarding_note", "")
        review = ("unreviewed" if isinstance(note, str) and note.startswith(
            ("EDITABLE TEMPLATE:", "STARTER CHECK ONLY:", "GUIDED CHECKS:")) else "legacy")
    obligations = []
    if "obligations" in data:
        raw_obligations = data["obligations"]
        if not isinstance(raw_obligations, list) or not raw_obligations:
            raise ValueError("explicit obligations must be a nonempty list")
        for row in raw_obligations:
            if isinstance(row, dict):
                _known_fields(row, {"id", "text", "scope", "coverage", "condition_ids",
                                    "target_path", "mandatory"}, "obligation")
            if (not isinstance(row, dict) or not all(isinstance(row.get(key), str) and row[key].strip()
                    for key in ("id", "text", "scope", "coverage"))
                    or not isinstance(row.get("condition_ids"), list)
                    or not all(isinstance(key, str) and key for key in row["condition_ids"])
                    or type(row.get("mandatory", True)) is not bool
                    or not isinstance(row.get("target_path", ""), str)):
                raise ValueError("malformed explicit obligation")
            obligations.append(CompletionObligation(row["id"], row["text"], row["scope"],
                                                   tuple(row["condition_ids"]), row["coverage"],
                                                   row.get("target_path", ""), row.get("mandatory", True)))
    contract = TaskCompletionContract(task_id, task, tuple(conditions), review,
                                      data.get("acceptance_digest"),
                                      acceptance_content_digest(data) if review == "reviewed" else None,
                                      tuple(obligations))
    return data, contract


def _worker_environment() -> dict[str, str]:
    names = ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "COMSPEC", "HOME",
             "USERPROFILE", "APPDATA", "CODEX_HOME")
    return {name: os.environ[name] for name in names if name in os.environ}


def _error_category(run: TaskRun) -> str | None:
    if run.terminal_outcome not in {TerminalOutcome.ERROR, TerminalOutcome.TIMEOUT}:
        return None
    if not run.history:
        return "runner_error"
    status = run.history[-1].worker_execution_status
    if status:
        return status.lower()
    detail = run.history[-1].error or ""
    if "worker raised" in detail:
        return "worker_runtime_error"
    if "verifier raised" in detail:
        return "verifier_runtime_error"
    return "runner_error"


def _human_reason(run: TaskRun) -> str | None:
    if run.terminal_outcome != TerminalOutcome.HUMAN_REQUIRED:
        return None
    if run.terminal_reason and "retry limit" in run.terminal_reason:
        return "attempt_limit_reached_unproven"
    if run.history and run.history[-1].unknown_conditions:
        return "mandatory_condition_unverifiable"
    return "completion_not_proven"


def _write_summary(path: Path, *, run_id: str, task_type: str, outcome: str,
                   attempts: int, mandatory: int, rejects: int, continued: bool,
                   human_reason: str | None, error_category: str | None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "run_id": run_id,
        "task_type": task_type,
        "final_decision": outcome,
        "attempts": attempts,
        "mandatory_requirements": mandatory,
        "reject_done_count": rejects,
        "automatic_continuation_occurred": continued,
        "human_required_reason_category": human_reason,
        "runtime_error_category": error_category,
    }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _review_required(summary_path: Path, *, run_id: str, task_type: str, mandatory: int,
                     gaps: tuple[str, ...], spec_path: Path, spec_value: str,
                     reason: str) -> int:
    _write_summary(summary_path, run_id=run_id, task_type=task_type,
                   outcome="HUMAN_REQUIRED", attempts=0, mandatory=mandatory,
                   rejects=0, continued=False, human_reason=reason, error_category=None)
    print("\nHUMAN_REQUIRED")
    print("Acceptance contract is not ready; Codex was not started.")
    for gap in gaps:
        print(f"- {gap}")
    print(f"Review the original task and every mandatory check in {spec_path}, "
          f"then run truth-firewall activate --spec {spec_value}")
    print("Explicit review does not prove that omitted requirements are covered.")
    print(f"Local run summary: {summary_path}")
    return 2


def _print_result(run: TaskRun, contract: TaskCompletionContract, summary_path: Path) -> int:
    display_outcome = (
        "ERROR" if run.terminal_outcome in {TerminalOutcome.ERROR, TerminalOutcome.TIMEOUT}
        else run.terminal_outcome.value
    )
    print(f"\n{display_outcome}")
    print(f"Attempts used: {len(run.history)}")
    if run.terminal_outcome == TerminalOutcome.VERIFIED_DONE:
        print(f"Original task: {contract.original_task}")
        final = run.history[-1]
        print(f"Mandatory requirements checked: {len(final.condition_verdicts)}/{len(contract.conditions)}")
        print("Evidence / check summary:")
        observations = dict(final.condition_observations)
        for condition_id, verdict in final.condition_verdicts:
            note = observations.get(condition_id, "")
            print(f"- {condition_id}: {verdict}" + (f" | {note}" if note else ""))
    elif run.terminal_outcome == TerminalOutcome.HUMAN_REQUIRED:
        final = run.history[-1] if run.history else None
        print("Could not prove:")
        if final:
            verdicts = dict(final.condition_verdicts)
            observations = dict(final.condition_observations)
            for condition_id in (*final.unknown_conditions, *final.failed_conditions):
                note = observations.get(condition_id, "")
                print(f"- {condition_id}: {verdicts.get(condition_id, 'UNPROVEN')}" +
                      (f" | {note}" if note else ""))
        if not final or not (final.unknown_conditions or final.failed_conditions):
            print(f"- {run.terminal_reason or 'one or more mandatory checks remain unproven'}")
        print("Human action needed: review the listed requirement(s), resolve any ambiguity or implementation gap, "
              "then start a new run.")
    else:
        detail = run.history[-1].error if run.history else run.terminal_reason
        category = _error_category(run) or "runner_error"
        print(f"Runtime / infrastructure reason: {category}" + (f" ({detail})" if detail else ""))
        print("This is an execution error, not a finding that the coding task failed.")
    print(f"Local run summary: {summary_path}")
    return {TerminalOutcome.VERIFIED_DONE: 0, TerminalOutcome.HUMAN_REQUIRED: 2}.get(run.terminal_outcome, 1)


def run_pilot(spec_value: str, *, attempts: int = 3) -> int:
    root = Path.cwd().resolve()
    run_id = uuid4().hex
    summary_path = root / ".truth-firewall" / "runs" / run_id / "summary.json"
    try:
        spec_path = Path(spec_value)
        if not spec_path.is_absolute():
            spec_path = root / spec_path
        spec_path = spec_path.resolve(strict=True)
        raw = json.loads(spec_path.read_text(encoding="utf-8"))
        if isinstance(raw, dict) and raw.get("acceptance_review") == "reviewed":
            try:
                review_valid = raw.get("acceptance_digest") == acceptance_content_digest(raw)
            except (TypeError, ValueError):
                review_valid = False
            if not review_valid:
                rows = raw.get("conditions")
                return _review_required(
                    summary_path, run_id=run_id, task_type=str(raw.get("task_type", "python")),
                    mandatory=len(rows) if isinstance(rows, list) else 0,
                    gaps=("acceptance review is invalid because the task or checks changed; "
                          "review and activate again",), spec_path=spec_path,
                    spec_value=spec_value, reason="acceptance_review_invalid")
        data, contract = _load_spec(spec_path)
        task_type = data.get("task_type", "python")
        gaps = contract.readiness_gaps()
        if gaps:
            return _review_required(
                summary_path, run_id=run_id, task_type=task_type,
                mandatory=len(contract.conditions), gaps=gaps, spec_path=spec_path,
                spec_value=spec_value,
                reason=("acceptance_review_invalid" if contract.acceptance_review == "reviewed"
                        else "acceptance_contract_unreviewed"))
        runtime = discover_codex()
        private_root = Path(tempfile.gettempdir()) / "truth-firewall-controller" / run_id
        raw_root = private_root / "codex-invocations"
        raw_root.mkdir(parents=True)
        env = _worker_environment()
        env.update({"TF_PILOT_RAW_ROOT": str(raw_root.resolve()),
                    "TF_CODEX_COMMAND": json.dumps(runtime.command)})
        worker = Path(__file__).with_name("codex_worker.py").resolve()
        spec = WorkerProcessSpec((sys.executable, "-I", str(worker)), timeout_seconds=320,
                                 max_output_bytes=1_000_000, environment=env)
        run = run_supervised_task(
            root, contract, spec, task_instructions=contract.original_task, retry_budget=attempts,
            contract_version="pilot-v1", task_run_id=run_id, artifact_root=private_root / "supervisor",
        )
        reject_count = sum(record.decision == "REJECT_DONE" for record in run.history)
        continued = any(record.decision == "REJECT_DONE" and record.attempt_number < len(run.history)
                        for record in run.history)
        summary_path = root / ".truth-firewall" / "runs" / run_id / "summary.json"
        _write_summary(summary_path, run_id=run_id, task_type=task_type,
                       outcome=("ERROR" if run.terminal_outcome in {TerminalOutcome.ERROR, TerminalOutcome.TIMEOUT}
                                else run.terminal_outcome.value), attempts=len(run.history),
                       mandatory=len(contract.conditions), rejects=reject_count, continued=continued,
                       human_reason=_human_reason(run), error_category=_error_category(run))
        return _print_result(run, contract, summary_path)
    except (OSError, ValueError, json.JSONDecodeError, RuntimeError) as exc:
        print("\nERROR")
        print(f"Runtime / infrastructure reason: {type(exc).__name__}: {exc}")
        print("This is an execution/setup error, not a finding that the coding task failed.")
        _write_summary(summary_path, run_id=run_id, task_type="python", outcome="ERROR",
                       attempts=0, mandatory=0, rejects=0, continued=False,
                       human_reason=None, error_category="setup_error")
        print(f"Local run summary: {summary_path}")
        return 1
