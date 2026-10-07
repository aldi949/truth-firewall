from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from truth_firewall.completion import (
    AcceptanceCondition,
    CompletionDecision,
    TaskCompletionContract,
    TaskCompletionVerifier,
)
from truth_firewall.lifecycle import TerminalOutcome, run_supervised_task
from truth_firewall.supervised_worker import (
    WorkerProcessSpec,
    WorkerProcessStatus,
    run_worker_process,
)


def make_layout(tmp_path: Path) -> tuple[Path, Path, Path]:
    workspace = tmp_path / "worker-workspace"
    controller = tmp_path / "controller-artifacts"
    workspace.mkdir()
    controller.mkdir()
    return workspace, controller, controller / "worker.py"


def write_worker(path: Path, source: str) -> tuple[str, ...]:
    path.write_text(source, encoding="utf-8")
    return (sys.executable, "-I", str(path))


def output_json(*, claim: str | None = "DONE", status: str = "DONE") -> str:
    return """import json, sys
request = json.load(sys.stdin)
print(json.dumps({
    'attempt_id': request['attempt_id'],
    'status': %r,
    'exit_code': 0,
    'claimed_completion_state': %r,
    'workspace_state_after_execution': 'worker-reported-state',
}))
print('worker diagnostic on stderr', file=sys.stderr)
""" % (status, claim)


def simple_contract() -> TaskCompletionContract:
    return TaskCompletionContract(
        "simple-worker-test",
        "Create a module exporting greet(name).",
        (
            AcceptanceCondition("api", "python_function", "app.py", "greet", ["name"]),
            AcceptanceCondition("behavior", "black_box", "app.py", "greet", cases=((("Ada",), "hello Ada"),)),
        ),
    )


def test_process_contract_captures_streams_exit_and_external_artifacts(tmp_path: Path):
    workspace, controller, worker_path = make_layout(tmp_path)
    (workspace / "stable.txt").write_text("protected task state", encoding="utf-8")
    protected = TaskCompletionContract(
        "artifact-isolation",
        "Keep the protected file present.",
        (AcceptanceCondition("file", "file_exists", "stable.txt"),),
    )
    prior_evidence = TaskCompletionVerifier(workspace, protected).attempt_done("DONE")
    assert prior_evidence.is_current(workspace)
    command = write_worker(worker_path, output_json())
    result = run_worker_process(
        WorkerProcessSpec(command), task_run_id="capture", attempt_number=1,
        workspace=workspace, task_instructions="Create greet(name).", artifact_root=controller,
    )

    assert result.status == WorkerProcessStatus.SUCCEEDED
    assert result.exit_code == 0
    assert result.worker_declared_status == "DONE"
    assert result.claimed_completion_state == "DONE"
    assert result.process_terminated
    assert "attempt_id" in Path(result.stdout_reference).read_text(encoding="utf-8")
    assert "worker diagnostic" in Path(result.stderr_reference).read_text(encoding="utf-8")
    assert not Path(result.stdout_reference).is_relative_to(workspace)
    assert prior_evidence.is_current(workspace)
    request = json.loads((Path(result.stdout_reference).parent / "request.json").read_text(encoding="utf-8"))
    assert request["task_run_id"] == "capture"
    assert request["attempt_number"] == 1
    assert request["workspace_path"] == str(workspace.resolve())
    assert "acceptance_contract" not in request
    assert "verifier" not in request


def test_worker_process_spec_is_narrow_python_only():
    with pytest.raises(ValueError, match="isolated Python script"):
        WorkerProcessSpec(("node", "worker.js"))
    with pytest.raises(ValueError, match="isolated Python script"):
        WorkerProcessSpec((sys.executable, "worker.py"))


def test_worker_timeout_terminates_child_and_cannot_verify(tmp_path: Path):
    workspace, controller, worker_path = make_layout(tmp_path)
    command = write_worker(worker_path, "import os, time\nprint(os.getpid(), flush=True)\ntime.sleep(20)\n")
    result = run_worker_process(
        WorkerProcessSpec(command, timeout_seconds=0.25), task_run_id="timeout", attempt_number=1,
        workspace=workspace, task_instructions="Wait.", artifact_root=controller,
    )
    assert result.status == WorkerProcessStatus.TIMEOUT
    assert result.process_id is not None
    assert result.process_terminated
    assert result.exit_code is not None
    assert result.claimed_completion_state is None

    run = run_supervised_task(
        workspace, simple_contract(), WorkerProcessSpec(command, timeout_seconds=0.25),
        task_instructions="Wait.", retry_budget=2, task_run_id="timeout-task",
        artifact_root=controller / "lifecycle",
    )
    assert run.terminal_outcome == TerminalOutcome.TIMEOUT
    assert run.history[0].decision == "TIMEOUT"


def test_worker_output_capture_is_bounded(tmp_path: Path):
    workspace, controller, worker_path = make_layout(tmp_path)
    command = write_worker(worker_path, "import sys\nwhile True:\n    sys.stdout.write('x' * 4096)\n    sys.stdout.flush()\n")
    result = run_worker_process(
        WorkerProcessSpec(command, timeout_seconds=5, max_output_bytes=1024),
        task_run_id="output-limit", attempt_number=1, workspace=workspace,
        task_instructions="Write output.", artifact_root=controller,
    )
    assert result.status == WorkerProcessStatus.OUTPUT_LIMIT
    assert result.process_terminated
    assert Path(result.stdout_reference).stat().st_size <= 1024


@pytest.mark.parametrize(
    ("source", "expected_status"),
    [
        ("print('{not-json}')", WorkerProcessStatus.MALFORMED_OUTPUT),
        ("pass", WorkerProcessStatus.NO_RESULT),
    ],
)
def test_malformed_or_missing_result_is_safe_non_success(tmp_path: Path, source: str, expected_status):
    workspace, controller, worker_path = make_layout(tmp_path)
    command = write_worker(worker_path, source)
    run = run_supervised_task(
        workspace, simple_contract(), WorkerProcessSpec(command),
        task_instructions="Implement the API.", retry_budget=2, task_run_id=f"bad-{expected_status.value}",
        artifact_root=controller,
    )
    assert run.terminal_outcome == TerminalOutcome.ERROR
    assert len(run.history) == 1
    assert run.history[0].worker_execution_status == expected_status.value
    assert run.history[0].decision == expected_status.value


def test_nonzero_exit_is_safe_even_with_done_json(tmp_path: Path):
    workspace, controller, worker_path = make_layout(tmp_path)
    source = output_json() + "\nsys.exit(9)\n"
    command = write_worker(worker_path, source)
    run = run_supervised_task(
        workspace, simple_contract(), WorkerProcessSpec(command), task_instructions="Implement greet.",
        retry_budget=2, task_run_id="nonzero", artifact_root=controller,
    )
    assert run.terminal_outcome == TerminalOutcome.ERROR
    assert run.history[0].worker_execution_status == "NONZERO_EXIT"
    assert run.history[0].worker_exit_code == 9
    assert run.history[0].decision == "NONZERO_EXIT"


def test_worker_crash_is_safe_non_success(tmp_path: Path):
    workspace, controller, worker_path = make_layout(tmp_path)
    command = write_worker(worker_path, "raise RuntimeError('intentional worker crash')\n")
    run = run_supervised_task(
        workspace, simple_contract(), WorkerProcessSpec(command), task_instructions="Implement greet.",
        retry_budget=2, task_run_id="crashed-worker", artifact_root=controller,
    )
    assert run.terminal_outcome == TerminalOutcome.ERROR
    assert run.history[0].worker_execution_status == "NONZERO_EXIT"
    assert run.history[0].worker_exit_code != 0
    assert "intentional worker crash" in Path(run.history[0].worker_stderr_reference).read_bytes().decode(
        "utf-8", errors="replace"
    )


def test_worker_printing_verified_done_cannot_authorize_incomplete_task(tmp_path: Path):
    workspace, controller, worker_path = make_layout(tmp_path)
    source = output_json(claim="VERIFIED_DONE")
    command = write_worker(worker_path, source)
    run = run_supervised_task(
        workspace, simple_contract(), WorkerProcessSpec(command), task_instructions="Implement greet.",
        retry_budget=1, task_run_id="fake-verified", artifact_root=controller,
    )
    assert run.terminal_outcome == TerminalOutcome.HUMAN_REQUIRED, run.history
    assert run.history[0].worker_claimed_completion_state == "VERIFIED_DONE"
    assert run.history[0].decision == CompletionDecision.REJECT_DONE.value
    assert run.history[0].condition_verdicts == (("api", "FAILED"), ("behavior", "FAILED"))


def test_worker_visible_tests_cannot_hide_behavioral_failure(tmp_path: Path):
    workspace, controller, worker_path = make_layout(tmp_path)
    behavioral_contract = TaskCompletionContract(
        "visible-tests-behavior",
        "Create classify(value) with the required output behavior.",
        (
            AcceptanceCondition("api", "python_function", "app.py", "classify", ["value"]),
                AcceptanceCondition("behavior", "black_box", "app.py", "classify", cases=((("x",), "X"),)),
        ),
    )
    source = """import json, sys
from pathlib import Path
import subprocess
request = json.load(sys.stdin)
root = Path(request['workspace_path'])
(root / 'app.py').write_text('def classify(value): return value\\n', encoding='utf-8')
(root / 'test_app.py').write_text('def test_fake(): assert True\\n', encoding='utf-8')
tests = subprocess.run([sys.executable, '-m', 'pytest', '-q', 'test_app.py'], cwd=root,
                       capture_output=True, text=True, check=False)
print(json.dumps({'attempt_id': request['attempt_id'], 'status': 'DONE', 'exit_code': 0,
                  'claimed_completion_state': 'DONE', 'workspace_state_after_execution': 'claimed',
                  'worker_tests_passed': tests.returncode == 0, 'worker_test_output': tests.stdout}))
"""
    command = write_worker(worker_path, source)
    run = run_supervised_task(
        workspace, behavioral_contract, WorkerProcessSpec(command),
        task_instructions="Implement classify.", retry_budget=1, task_run_id="tests-do-not-authorize",
        artifact_root=controller,
    )
    assert run.terminal_outcome == TerminalOutcome.HUMAN_REQUIRED, run.history
    assert (workspace / "test_app.py").is_file()
    assert run.history[0].condition_verdicts == (("api", "VERIFIED"), ("behavior", "FAILED"))
    payload = json.loads(Path(run.history[0].worker_stdout_reference).read_text(encoding="utf-8"))
    assert payload["worker_tests_passed"] is True


def test_reject_done_launches_new_process_and_independently_reverifies(tmp_path: Path):
    workspace, controller, worker_path = make_layout(tmp_path)
    contract = TaskCompletionContract(
        "slugify-e2e-test",
        "Implement slugify(text) that makes normalized URL slugs.",
        (
            AcceptanceCondition("api", "python_function", "slug.py", "slugify", ["text"]),
            AcceptanceCondition("ordinary", "black_box", "slug.py", "slugify", cases=((("Hello World",), "hello-world"),)),
            AcceptanceCondition("edge", "black_box", "slug.py", "slugify", cases=((("  A___B!  ",), "a-b"),)),
        ),
    )
    source = r"""import json, sys
from pathlib import Path
request = json.load(sys.stdin)
root = Path(request['workspace_path'])
if request['attempt_number'] == 1:
    impl = "def slugify(text):\n    return text.lower().replace(' ', '-')\n"
    test = "from slug import slugify\ndef test_ordinary(): assert slugify('Hello World') == 'hello-world'\n"
    print('implemented the ordinary path; claiming DONE', file=sys.stderr)
else:
    impl = "import re\ndef slugify(text):\n    return re.sub(r'[^a-z0-9]+', '-', text.lower()).strip('-')\n"
    test = "from slug import slugify\ndef test_ordinary(): assert slugify('Hello World') == 'hello-world'\ndef test_edge(): assert slugify('  A___B!  ') == 'a-b'\n"
    print('applied continuation; claiming DONE', file=sys.stderr)
(root / 'slug.py').write_text(impl, encoding='utf-8')
(root / 'test_slug.py').write_text(test, encoding='utf-8')
print(json.dumps({'attempt_id': request['attempt_id'], 'status': 'DONE', 'exit_code': 0,
                  'claimed_completion_state': 'DONE', 'workspace_state_after_execution': 'untrusted-worker-report'}))
"""
    command = write_worker(worker_path, source)
    run = run_supervised_task(
        workspace, contract, WorkerProcessSpec(command),
        task_instructions="Implement slugify(text) for lowercase URL slugs; collapse punctuation runs and trim separators.",
        retry_budget=2, task_run_id="two-process-reverification", artifact_root=controller,
    )
    assert run.terminal_outcome == TerminalOutcome.VERIFIED_DONE, run.history
    assert [record.decision for record in run.history] == ["REJECT_DONE", "VERIFIED_DONE"]
    assert [record.attempt_number for record in run.history] == [1, 2]
    assert run.history[0].condition_verdicts == (("api", "VERIFIED"), ("ordinary", "VERIFIED"), ("edge", "FAILED"))
    assert all(value == "VERIFIED" for _, value in run.history[1].condition_verdicts)
    assert run.history[0].worker_process_id != run.history[1].worker_process_id
    assert run.history[0].worker_stdout_reference != run.history[1].worker_stdout_reference
    assert run.history[1].continuation is None
    assert run.history[0].continuation is not None
    assert [item.condition_id for item in run.history[0].continuation.items] == ["edge"]
    request1 = Path(run.history[0].worker_stdout_reference).parent / "request.json"
    request2 = Path(run.history[1].worker_stdout_reference).parent / "request.json"
    for request_path in (request1, request2):
        request = json.loads(request_path.read_text(encoding="utf-8"))
        assert "cases" not in request and "expected" not in request
        assert "a-b" not in request["task_instructions"]
        if request["continuation_request"] is not None:
            assert "a-b" not in json.dumps(request["continuation_request"])


def test_prior_verification_stales_after_later_worker_mutation(tmp_path: Path):
    workspace, controller, worker_path = make_layout(tmp_path)
    (workspace / "app.py").write_text("def greet(name): return 'hello ' + name\n", encoding="utf-8")
    valid = TaskCompletionVerifier(workspace, simple_contract()).attempt_done("DONE")
    assert valid.decision == CompletionDecision.VERIFIED_DONE
    source = """import json, sys
from pathlib import Path
request = json.load(sys.stdin)
Path(request['workspace_path'], 'app.py').write_text("def greet(name): return 'broken'\\n", encoding='utf-8')
print(json.dumps({'attempt_id': request['attempt_id'], 'status': 'DONE', 'exit_code': 0,
                  'claimed_completion_state': 'DONE', 'workspace_state_after_execution': 'claimed'}))
"""
    command = write_worker(worker_path, source)
    process_result = run_worker_process(
        WorkerProcessSpec(command), task_run_id="stale-evidence", attempt_number=2,
        workspace=workspace, task_instructions="Modify app.py.", artifact_root=controller,
    )
    assert process_result.status == WorkerProcessStatus.SUCCEEDED
    assert not valid.is_current(workspace)
    assert valid.current_decision(workspace) == CompletionDecision.HUMAN_REQUIRED
    assert TaskCompletionVerifier(workspace, simple_contract()).attempt_done("DONE").decision == CompletionDecision.REJECT_DONE


def test_artifact_root_inside_worker_workspace_is_refused(tmp_path: Path):
    workspace, _, worker_path = make_layout(tmp_path)
    command = write_worker(worker_path, output_json())
    with pytest.raises(ValueError, match="outside the worker workspace"):
        run_worker_process(
            WorkerProcessSpec(command), task_run_id="unsafe-artifacts", attempt_number=1,
            workspace=workspace, task_instructions="Do nothing.", artifact_root=workspace / "controller",
        )


def test_subjective_contract_stays_human_required(tmp_path: Path):
    workspace, controller, worker_path = make_layout(tmp_path)
    (workspace / "app.py").write_text("def greet(name): return 'hello ' + name\n", encoding="utf-8")
    subjective = TaskCompletionContract(
        "subjective-worker",
        "Create a clear API.",
        (AcceptanceCondition("clarity", "subjective_quality", expected="clear"),),
    )
    command = write_worker(worker_path, output_json())
    run = run_supervised_task(
        workspace, subjective, WorkerProcessSpec(command), task_instructions="Implement API.",
        retry_budget=3, task_run_id="human-required", artifact_root=controller,
    )
    assert run.terminal_outcome == TerminalOutcome.HUMAN_REQUIRED
    assert len(run.history) == 1


def test_supervised_retry_exhaustion_remains_terminal(tmp_path: Path):
    workspace, controller, worker_path = make_layout(tmp_path)
    command = write_worker(worker_path, output_json())
    run = run_supervised_task(
        workspace, simple_contract(), WorkerProcessSpec(command), task_instructions="Implement greet.",
        retry_budget=2, task_run_id="retry-exhaustion", artifact_root=controller,
    )
    assert run.terminal_outcome == TerminalOutcome.HUMAN_REQUIRED
    assert run.terminal_reason == "retry limit reached while completion remains unproven"
    assert [record.decision for record in run.history] == ["REJECT_DONE", "REJECT_DONE"]
    assert len({record.worker_process_id for record in run.history}) == 2


def test_fake_verified_done_never_sets_task_level_verified_done(tmp_path: Path):
    workspace, controller, worker_path = make_layout(tmp_path)
    command = write_worker(worker_path, output_json(claim="VERIFIED_DONE"))
    run = run_supervised_task(
        workspace, simple_contract(), WorkerProcessSpec(command), task_instructions="Implement greet.",
        retry_budget=1, task_run_id="cannot-set-terminal", artifact_root=controller,
    )
    assert run.terminal_outcome == TerminalOutcome.HUMAN_REQUIRED
    assert run.history[0].decision == CompletionDecision.REJECT_DONE.value
    assert run.history[0].worker_claimed_completion_state == "VERIFIED_DONE"
