"""Bounded local worker and independently verified completion lifecycle.

This is a logical in-process boundary, not an OS security sandbox. The worker
callback receives a workspace and sanitized continuation feedback; it never
receives the verifier, contract, evidence objects, or decision setters.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable
from uuid import uuid4

from truth_firewall.completion import (
    CompletionDecision,
    TaskCompletionContract,
    TaskCompletionVerifier,
)
from truth_firewall.supervised_worker import (
    WorkerProcessSpec,
    WorkerProcessStatus,
    run_worker_process,
)


class TerminalOutcome(str, Enum):
    VERIFIED_DONE = "VERIFIED_DONE"
    HUMAN_REQUIRED = "HUMAN_REQUIRED"
    RETRY_EXHAUSTED = "RETRY_EXHAUSTED"
    ERROR = "ERROR"
    TIMEOUT = "TIMEOUT"


@dataclass(frozen=True)
class ContinuationItem:
    condition_id: str
    condition_kind: str
    target: str
    acceptance_condition: str
    observed_failure: str
    evidence_ref: str
    reverification_required: bool = True


@dataclass(frozen=True)
class ContinuationRequest:
    task_run_id: str
    attempt_number: int
    items: tuple[ContinuationItem, ...]


@dataclass(frozen=True)
class AttemptRecord:
    task_run_id: str
    contract_version: str
    attempt_number: int
    state_identity: str | None
    done_attempt: str
    condition_verdicts: tuple[tuple[str, str], ...]
    failed_conditions: tuple[str, ...]
    unknown_conditions: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    decision: str
    continuation: ContinuationRequest | None
    invalidated_prior_state: bool
    error: str | None = None
    worker_execution_status: str | None = None
    worker_declared_status: str | None = None
    worker_exit_code: int | None = None
    worker_stdout_reference: str | None = None
    worker_stderr_reference: str | None = None
    worker_claimed_completion_state: str | None = None
    worker_reported_workspace_state: str | None = None
    workspace_state_after_worker: str | None = None
    worker_process_id: int | None = None
    worker_process_terminated: bool | None = None
    condition_observations: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class TaskRun:
    task_run_id: str
    contract_version: str
    retry_budget: int
    history: tuple[AttemptRecord, ...]
    terminal_outcome: TerminalOutcome
    terminal_reason: str | None = None

    @property
    def retry_count(self) -> int:
        return max(0, len(self.history) - 1)


Worker = Callable[[Path, ContinuationRequest | None], str]


def _continuation(contract: TaskCompletionContract, attempt, run_id: str, number: int) -> ContinuationRequest:
    conditions = {condition.condition_id: condition for condition in contract.conditions}
    items = []
    for evidence in attempt.evidence:
        if evidence.verdict.value == "VERIFIED":
            continue
        condition = conditions[evidence.condition_id]
        # Never send expected outputs/cases; identify only the independently observed
        # mismatch or lack of observability and the target the worker can inspect.
        target = condition.symbol if condition.symbol else condition.path
        acceptance = (
            f"Provide the requested top-level function {condition.symbol} with its contracted signature."
            if condition.kind == "python_function" else
            f"Satisfy the protected behavioral acceptance checks for {condition.symbol}."
            if condition.kind == "black_box" else
            f"Meet the contracted file-presence requirement for {condition.path}."
            if condition.kind == "file_exists" else
            f"Satisfy mandatory condition {condition.condition_id}; independent verification is unavailable."
        )
        items.append(ContinuationItem(
            condition_id=condition.condition_id,
            condition_kind=condition.kind,
            target=target,
            acceptance_condition=acceptance,
            observed_failure=evidence.observation,
            evidence_ref=f"{run_id}:{number}:{evidence.condition_id}:{evidence.state_seal or 'unsealed'}",
        ))
    return ContinuationRequest(run_id, number, tuple(items))


def run_task(
    root: Path,
    contract: TaskCompletionContract,
    worker: Worker,
    *,
    retry_budget: int = 3,
    contract_version: str = "1",
    task_run_id: str | None = None,
    verifier_factory: Callable[[Path, TaskCompletionContract], TaskCompletionVerifier] = TaskCompletionVerifier,
) -> TaskRun:
    """Run up to retry_budget DONE attempts; every DONE is freshly verified."""
    if retry_budget < 1:
        raise ValueError("retry_budget must be at least one")
    run_id = task_run_id or str(uuid4())
    verifier: TaskCompletionVerifier | None = None
    records: list[AttemptRecord] = []
    feedback: ContinuationRequest | None = None
    prior_state: str | None = None
    for number in range(1, retry_budget + 1):
        try:
            response = worker(root, feedback)
        except Exception as exc:  # Worker failure is terminal non-success.
            records.append(AttemptRecord(run_id, contract_version, number, None, "<worker error>", (), (), (), (),
                                         "ERROR", None, False, f"worker raised {type(exc).__name__}"))
            return TaskRun(run_id, contract_version, retry_budget, tuple(records), TerminalOutcome.ERROR)
        try:
            if verifier is None:
                verifier = verifier_factory(root, contract)
            attempt = verifier.attempt_done(response)
        except Exception as exc:  # Verifier failure must never be interpreted as completion.
            records.append(AttemptRecord(run_id, contract_version, number, None, response, (), (), (), (),
                                         "ERROR", None, False, f"verifier raised {type(exc).__name__}"))
            return TaskRun(run_id, contract_version, retry_budget, tuple(records), TerminalOutcome.ERROR)

        verdicts = tuple((e.condition_id, e.verdict.value) for e in attempt.evidence)
        failed = tuple(e.condition_id for e in attempt.evidence if e.verdict.value == "FAILED")
        unknown = tuple(e.condition_id for e in attempt.evidence if e.verdict.value == "UNKNOWN")
        refs = tuple(f"{run_id}:{number}:{e.condition_id}:{e.state_seal or 'unsealed'}" for e in attempt.evidence)
        decision = attempt.decision
        invalidated = prior_state is not None and prior_state != attempt.state_seal
        state_identity = attempt.state_seal
        if decision == CompletionDecision.VERIFIED_DONE and not attempt.is_current(root):
            decision = CompletionDecision.HUMAN_REQUIRED
        continuation = _continuation(contract, attempt, run_id, number) if decision == CompletionDecision.REJECT_DONE else None
        records.append(AttemptRecord(run_id, contract_version, number, state_identity, response, verdicts, failed,
                                     unknown, refs, decision.value, continuation, invalidated,
                                     condition_observations=tuple((e.condition_id, e.observation)
                                                                  for e in attempt.evidence)))
        if decision == CompletionDecision.VERIFIED_DONE:
            return TaskRun(run_id, contract_version, retry_budget, tuple(records), TerminalOutcome.VERIFIED_DONE)
        if decision == CompletionDecision.HUMAN_REQUIRED:
            return TaskRun(run_id, contract_version, retry_budget, tuple(records), TerminalOutcome.HUMAN_REQUIRED)
        if decision != CompletionDecision.REJECT_DONE:
            return TaskRun(run_id, contract_version, retry_budget, tuple(records), TerminalOutcome.ERROR)
        prior_state = attempt.state_seal
        feedback = continuation
    return TaskRun(
        run_id, contract_version, retry_budget, tuple(records), TerminalOutcome.HUMAN_REQUIRED,
        "retry limit reached while completion remains unproven",
    )


def run_supervised_task(
    root: Path,
    contract: TaskCompletionContract,
    worker_spec: WorkerProcessSpec,
    *,
    task_instructions: str,
    retry_budget: int = 3,
    contract_version: str = "1",
    task_run_id: str | None = None,
    artifact_root: Path | None = None,
    verifier_factory: Callable[[Path, TaskCompletionContract], TaskCompletionVerifier] = TaskCompletionVerifier,
) -> TaskRun:
    """Run fresh OS-process attempts while keeping completion authority in-parent."""
    if retry_budget < 1:
        raise ValueError("retry_budget must be at least one")
    workspace = root.resolve(strict=True)
    if not workspace.is_dir():
        raise ValueError("worker workspace must be a directory")
    run_id = task_run_id or str(uuid4())
    verifier: TaskCompletionVerifier | None = None
    records: list[AttemptRecord] = []
    feedback: ContinuationRequest | None = None
    prior_state: str | None = None

    for number in range(1, retry_budget + 1):
        try:
            execution = run_worker_process(
                worker_spec,
                task_run_id=run_id,
                attempt_number=number,
                workspace=workspace,
                task_instructions=task_instructions,
                continuation=feedback,
                artifact_root=artifact_root,
            )
        except Exception as exc:
            records.append(AttemptRecord(
                run_id, contract_version, number, None, "<worker supervision error>", (), (), (), (),
                "ERROR", None, False, f"worker supervisor raised {type(exc).__name__}",
            ))
            return TaskRun(run_id, contract_version, retry_budget, tuple(records), TerminalOutcome.ERROR)

        worker_fields = {
            "worker_execution_status": execution.status.value,
            "worker_declared_status": execution.worker_declared_status,
            "worker_exit_code": execution.exit_code,
            "worker_stdout_reference": execution.stdout_reference,
            "worker_stderr_reference": execution.stderr_reference,
            "worker_claimed_completion_state": execution.claimed_completion_state,
            "worker_reported_workspace_state": execution.worker_reported_workspace_state,
            "workspace_state_after_worker": execution.workspace_state_after_execution,
            "worker_process_id": execution.process_id,
            "worker_process_terminated": execution.process_terminated,
        }
        if execution.status != WorkerProcessStatus.SUCCEEDED:
            error = execution.protocol_error or f"worker attempt ended as {execution.status.value}"
            records.append(AttemptRecord(
                run_id, contract_version, number, execution.workspace_state_after_execution,
                execution.claimed_completion_state or "<no accepted worker result>", (), (), (), (),
                execution.status.value, None, False, error, **worker_fields,
            ))
            terminal = TerminalOutcome.TIMEOUT if execution.status == WorkerProcessStatus.TIMEOUT else TerminalOutcome.ERROR
            return TaskRun(run_id, contract_version, retry_budget, tuple(records), terminal)

        try:
            if verifier is None:
                verifier = verifier_factory(workspace, contract)
            # This string is retained as untrusted provenance only. The verifier
            # evaluates the protected contract against the current workspace.
            response = execution.claimed_completion_state or execution.worker_declared_status or "worker exited"
            attempt = verifier.attempt_done(response)
        except Exception as exc:
            records.append(AttemptRecord(
                run_id, contract_version, number, None,
                execution.claimed_completion_state or "<worker result>", (), (), (), (),
                "ERROR", None, False, f"verifier raised {type(exc).__name__}", **worker_fields,
            ))
            return TaskRun(run_id, contract_version, retry_budget, tuple(records), TerminalOutcome.ERROR)

        verdicts = tuple((e.condition_id, e.verdict.value) for e in attempt.evidence)
        failed = tuple(e.condition_id for e in attempt.evidence if e.verdict.value == "FAILED")
        unknown = tuple(e.condition_id for e in attempt.evidence if e.verdict.value == "UNKNOWN")
        refs = tuple(f"{run_id}:{number}:{e.condition_id}:{e.state_seal or 'unsealed'}" for e in attempt.evidence)
        decision = attempt.decision
        invalidated = prior_state is not None and prior_state != attempt.state_seal
        if decision == CompletionDecision.VERIFIED_DONE and not attempt.is_current(workspace):
            decision = CompletionDecision.HUMAN_REQUIRED
        continuation = _continuation(contract, attempt, run_id, number) if decision == CompletionDecision.REJECT_DONE else None
        records.append(AttemptRecord(
            run_id, contract_version, number, attempt.state_seal,
            execution.claimed_completion_state or execution.worker_declared_status or "<no completion claim>",
            verdicts, failed, unknown, refs, decision.value, continuation, invalidated,
            condition_observations=tuple((e.condition_id, e.observation) for e in attempt.evidence),
            **worker_fields,
        ))
        if decision == CompletionDecision.VERIFIED_DONE:
            return TaskRun(run_id, contract_version, retry_budget, tuple(records), TerminalOutcome.VERIFIED_DONE)
        if decision == CompletionDecision.HUMAN_REQUIRED:
            return TaskRun(run_id, contract_version, retry_budget, tuple(records), TerminalOutcome.HUMAN_REQUIRED)
        if decision != CompletionDecision.REJECT_DONE:
            return TaskRun(run_id, contract_version, retry_budget, tuple(records), TerminalOutcome.ERROR)
        prior_state = attempt.state_seal
        feedback = continuation

    return TaskRun(
        run_id, contract_version, retry_budget, tuple(records), TerminalOutcome.HUMAN_REQUIRED,
        "retry limit reached while completion remains unproven",
    )
