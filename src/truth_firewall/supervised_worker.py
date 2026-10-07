"""Supervise one local worker process; this is not a security sandbox."""

from __future__ import annotations

import json
import hashlib
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Mapping, Sequence
from uuid import uuid4

from truth_firewall.evidence.provenance import workspace_snapshot


class WorkerProcessStatus(str, Enum):
    SUCCEEDED = "SUCCEEDED"
    TIMEOUT = "TIMEOUT"
    NONZERO_EXIT = "NONZERO_EXIT"
    MALFORMED_OUTPUT = "MALFORMED_OUTPUT"
    NO_RESULT = "NO_RESULT"
    OUTPUT_LIMIT = "OUTPUT_LIMIT"
    SPAWN_ERROR = "SPAWN_ERROR"


@dataclass(frozen=True)
class WorkerProcessSpec:
    command: tuple[str, ...]
    timeout_seconds: float = 30.0
    max_output_bytes: int = 256 * 1024
    environment: Mapping[str, str] | None = None

    def __post_init__(self) -> None:
        if not self.command or any(not isinstance(arg, str) or not arg for arg in self.command):
            raise ValueError("worker command must contain nonempty arguments")
        python_names = {"python", "python.exe", "python3", "python3.exe"}
        if (len(self.command) != 3 or Path(self.command[0]).name.lower() not in python_names
                or self.command[1] != "-I" or Path(self.command[2]).suffix.lower() != ".py"):
            raise ValueError("worker must be a single isolated Python script invocation")
        if self.timeout_seconds <= 0:
            raise ValueError("worker timeout must be positive")
        if self.max_output_bytes < 1024:
            raise ValueError("worker output limit must be at least 1024 bytes")


@dataclass(frozen=True)
class WorkerProcessResult:
    task_run_id: str
    attempt_id: str
    attempt_number: int
    status: WorkerProcessStatus
    exit_code: int | None
    stdout_reference: str
    stderr_reference: str
    worker_declared_status: str | None
    claimed_completion_state: str | None
    worker_reported_workspace_state: str | None
    workspace_state_after_execution: str | None
    process_id: int | None
    process_terminated: bool
    protocol_error: str | None = None


class _BoundedCapture:
    def __init__(self, stream, limit: int, exceeded: threading.Event) -> None:
        self.stream = stream
        self.limit = limit
        self.exceeded = exceeded
        self.data = bytearray()

    def drain(self) -> None:
        while True:
            chunk = self.stream.read(65536)
            if not chunk:
                return
            remaining = self.limit - len(self.data)
            if remaining > 0:
                self.data.extend(chunk[:remaining])
            if len(chunk) > remaining:
                self.exceeded.set()


def _minimal_environment(extra: Mapping[str, str] | None) -> dict[str, str]:
    allowed = ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "COMSPEC")
    env = {key: os.environ[key] for key in allowed if key in os.environ}
    env["PYTHONIOENCODING"] = "utf-8"
    if extra:
        for key, value in extra.items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise ValueError("worker environment keys and values must be strings")
            env[key] = value
    return env


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _artifact_directory(workspace: Path, artifact_root: Path | None, task_run_id: str, attempt_id: str) -> Path:
    base = (artifact_root or (Path(tempfile.gettempdir()) / "truth-firewall-worker-artifacts")).resolve()
    if _inside(base, workspace) or base == workspace:
        raise ValueError("controller artifacts must be outside the worker workspace")
    base.mkdir(parents=True, exist_ok=True)
    task_key = hashlib.sha256(task_run_id.encode("utf-8")).hexdigest()[:24]
    attempt_key = hashlib.sha256(attempt_id.encode("utf-8")).hexdigest()[:24]
    directory = base / task_key / attempt_key
    directory.mkdir(parents=True, exist_ok=False)
    return directory


def _terminate_process(process: subprocess.Popen[bytes]) -> bool:
    if process.poll() is not None:
        return True
    if os.name == "nt":
        # taskkill is available on supported Windows hosts and requests tree
        # termination. This is best-effort process cleanup, not containment.
        try:
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=3, check=False)
        except (OSError, subprocess.TimeoutExpired):
            pass
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            process.kill()
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except OSError:
            process.terminate()
        try:
            process.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except OSError:
                process.kill()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
    return process.poll() is not None


def _continuation_payload(continuation) -> dict | None:
    if continuation is None:
        return None
    return asdict(continuation)


def run_worker_process(
    spec: WorkerProcessSpec,
    *,
    task_run_id: str,
    attempt_number: int,
    workspace: Path,
    task_instructions: str,
    continuation=None,
    artifact_root: Path | None = None,
    attempt_id: str | None = None,
) -> WorkerProcessResult:
    """Run one worker attempt and retain bounded raw streams outside its workspace.

    The worker JSON is untrusted. Only its transport shape is checked here; its
    status and claimed completion state never determine a task verdict.
    """
    root = workspace.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("worker workspace must be a directory")
    attempt_id = attempt_id or str(uuid4())
    artifacts = _artifact_directory(root, artifact_root, task_run_id, attempt_id)
    stdout_path = artifacts / "stdout.bin"
    stderr_path = artifacts / "stderr.bin"
    request_path = artifacts / "request.json"
    request = {
        "protocol_version": 1,
        "task_run_id": task_run_id,
        "attempt_id": attempt_id,
        "attempt_number": attempt_number,
        "workspace_path": str(root),
        "task_instructions": task_instructions,
        "continuation_request": _continuation_payload(continuation),
        "allowed_execution_metadata": {
            "timeout_seconds": spec.timeout_seconds,
            "max_output_bytes": spec.max_output_bytes,
        },
    }
    # Keep stdin protocol transport ASCII so Windows locale encodings cannot
    # corrupt non-ASCII workspace paths before the worker parses the JSON.
    request_bytes = json.dumps(request, ensure_ascii=True).encode("ascii")
    if len(request_bytes) > 64 * 1024:
        raise ValueError("worker request exceeds the 64 KiB protocol limit")
    request_path.write_bytes(request_bytes)
    stdout_exceeded = threading.Event()
    stderr_exceeded = threading.Event()
    process: subprocess.Popen[bytes] | None = None
    stdout_capture = _BoundedCapture(None, spec.max_output_bytes, stdout_exceeded)
    stderr_capture = _BoundedCapture(None, spec.max_output_bytes, stderr_exceeded)
    status = WorkerProcessStatus.SPAWN_ERROR
    exit_code: int | None = None
    process_terminated = False
    protocol_error: str | None = None
    claimed: str | None = None
    reported_state: str | None = None
    declared_status: str | None = None
    pid: int | None = None
    try:
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        process = subprocess.Popen(
            list(spec.command), cwd=root, env=_minimal_environment(spec.environment),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=creationflags, start_new_session=(os.name != "nt"),
        )
        pid = process.pid
        stdout_capture.stream = process.stdout
        stderr_capture.stream = process.stderr
        stdout_thread = threading.Thread(target=stdout_capture.drain, daemon=True)
        stderr_thread = threading.Thread(target=stderr_capture.drain, daemon=True)
        stdout_thread.start()
        stderr_thread.start()
        deadline = time.monotonic() + spec.timeout_seconds

        def send_request() -> None:
            try:
                assert process is not None and process.stdin is not None
                process.stdin.write(request_bytes)
                process.stdin.close()
            except (OSError, ValueError):
                # The child may exit early; process status and output validation
                # below decide whether that is a valid attempt.
                pass

        input_thread = threading.Thread(target=send_request, daemon=True)
        input_thread.start()
        timed_out = False
        output_limited = False
        while process.poll() is None:
            if stdout_exceeded.is_set() or stderr_exceeded.is_set():
                output_limited = True
                process_terminated = _terminate_process(process)
                break
            if time.monotonic() >= deadline:
                timed_out = True
                process_terminated = _terminate_process(process)
                break
            time.sleep(0.01)
        if process.poll() is None:
            process_terminated = _terminate_process(process)
        exit_code = process.wait()
        stdout_thread.join(timeout=2)
        stderr_thread.join(timeout=2)
        input_thread.join(timeout=1)
        if process.stdin is not None and not input_thread.is_alive():
            process.stdin.close()
        if process.stdout is not None:
            process.stdout.close()
        if process.stderr is not None:
            process.stderr.close()
        if output_limited:
            status = WorkerProcessStatus.OUTPUT_LIMIT
        elif timed_out:
            status = WorkerProcessStatus.TIMEOUT
        elif exit_code != 0:
            status = WorkerProcessStatus.NONZERO_EXIT
        else:
            try:
                raw_output = bytes(stdout_capture.data)
                if not raw_output.strip():
                    status = WorkerProcessStatus.NO_RESULT
                    protocol_error = "worker produced no structured result"
                else:
                    parsed = json.loads(raw_output.decode("utf-8"))
                    if not isinstance(parsed, dict):
                        raise ValueError("result must be a JSON object")
                    if parsed.get("attempt_id") != attempt_id:
                        raise ValueError("attempt_id mismatch")
                    if parsed.get("status") not in {"DONE", "CONTINUE"}:
                        raise ValueError("status must be DONE or CONTINUE")
                    if type(parsed.get("exit_code")) is not int or parsed["exit_code"] != exit_code:
                        raise ValueError("reported exit_code does not match process exit")
                    claim = parsed.get("claimed_completion_state")
                    if claim is not None and not isinstance(claim, str):
                        raise ValueError("claimed_completion_state must be a string or null")
                    reported = parsed.get("workspace_state_after_execution")
                    if not isinstance(reported, str):
                        raise ValueError("workspace_state_after_execution must be a string")
                    claimed = claim
                    reported_state = reported
                    declared_status = parsed["status"]
                    status = WorkerProcessStatus.SUCCEEDED
            except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
                status = WorkerProcessStatus.MALFORMED_OUTPUT
                protocol_error = f"invalid worker result: {type(exc).__name__}: {exc}"
        process_terminated = process.poll() is not None
    except (OSError, ValueError, BrokenPipeError) as exc:
        protocol_error = f"worker launch or protocol I/O failed: {type(exc).__name__}"
        if process is not None and process.poll() is None:
            process_terminated = _terminate_process(process)
            exit_code = process.poll()
    finally:
        stdout_path.write_bytes(bytes(stdout_capture.data))
        stderr_path.write_bytes(bytes(stderr_capture.data))

    observed_state = workspace_snapshot(root)
    return WorkerProcessResult(
        task_run_id=task_run_id,
        attempt_id=attempt_id,
        attempt_number=attempt_number,
        status=status,
        exit_code=exit_code,
        stdout_reference=str(stdout_path),
        stderr_reference=str(stderr_path),
        worker_declared_status=declared_status,
        claimed_completion_state=claimed,
        worker_reported_workspace_state=reported_state,
        workspace_state_after_execution=observed_state,
        process_id=pid,
        process_terminated=process_terminated,
        protocol_error=protocol_error,
    )
