"""Codex lifecycle adapter that reuses the Truth Firewall verification pipeline."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from truth_firewall.errors import UnsafePathError
from truth_firewall.evidence.collectors.test_runner import parse_test_stdout, recognize_test_invocation
from truth_firewall.evidence.io import load_evidence_path
from truth_firewall.integrations.local_test_results import collect_trusted_local_test_results
from truth_firewall.pipeline import check_response
from truth_firewall.policy.engine import is_enforced, requires_correction
from truth_firewall.providers.config import load_provider_from_env
from truth_firewall.redaction import redact_text, redact_value
from truth_firewall.schemas import EvidenceType, TrustLevel, parse_time, utc_now
from truth_firewall.storage import checked_path, ensure_directory, read_text, safe_component, write_text


def handle_codex_event(event: dict[str, Any], *, workspace: Path, raw_payload: str | None = None) -> dict[str, Any]:
    """Handle UserPromptSubmit, PostToolUse, and Stop hook events."""
    try:
        return _handle_event(event, workspace=workspace, raw_payload=raw_payload)
    except (UnsafePathError, OSError, ValueError):
        if event.get("hook_event_name") == "Stop":
            return {"decision": "block", "reason": "Truth Firewall refused unsafe or unavailable session storage."}
        return {}


def _handle_event(event: dict[str, Any], *, workspace: Path, raw_payload: str | None = None) -> dict[str, Any]:
    name = event.get("hook_event_name")
    if name not in {"UserPromptSubmit", "PostToolUse", "Stop"}:
        return {}
    session_id = _safe_component(event.get("session_id"))
    if not session_id:
        return _fail_stop(event, workspace, "The hook payload has no session_id.") if name == "Stop" else {}
    run_id = _safe_component(event.get("turn_id"))
    if name == "UserPromptSubmit":
        if not run_id:
            return {}
        try:
            run = _run_dir(workspace, session_id, run_id)
        except UnsafePathError:
            return {}
        ensure_directory(workspace, run)
        _append_jsonl(run / "hook_events.jsonl", _event_envelope(event, raw_payload))
        return {}
    if not run_id:
        return _fail_stop(event, workspace, "The hook payload has no turn_id or active run.") if name == "Stop" else {}
    try:
        run = _run_dir(workspace, session_id, run_id)
    except UnsafePathError:
        return {
            "decision": "block",
            "reason": "Truth Firewall refused unsafe Codex session storage outside the workspace.",
        }
    if name == "PostToolUse":
        ensure_directory(workspace, run)
        _append_jsonl(run / "hook_events.jsonl", _event_envelope(event, raw_payload))
        return {}
    return _check_stop(event, raw_payload, run, session_id, run_id, workspace)


def _check_stop(
    event: dict[str, Any], raw_payload: str | None, run: Path, session_id: str, run_id: str, workspace: Path
) -> dict[str, Any]:
    ensure_directory(workspace, run)
    _append_jsonl(run / "hook_events.jsonl", _event_envelope(event, raw_payload))
    _materialize_tool_evidence(run)
    response = event.get("last_assistant_message")
    if not isinstance(response, str) or not response.strip():
        return _fail_stop(event, workspace, "Codex did not provide last_assistant_message.", run=run)

    evidence, load_errors = (
        load_evidence_path(checked_path(workspace, run / "evidence.jsonl"))
        if checked_path(workspace, run / "evidence.jsonl").exists() else ([], [])
    )
    provider_error = None
    try:
        provider = load_provider_from_env()
    except Exception as exc:
        provider = None
        provider_error = f"{type(exc).__name__}: {exc}"
    # No API key or provider value is included in the audit. The existing client reads it from env.
    safe_response = redact_text(response)
    result = check_response(
        safe_response,
        evidence,
        provider=provider,
        offline=provider is None,
        runs_dir=ensure_directory(workspace, run / "audit"),
        now=utc_now(),
        workspace_root=workspace,
        collect_local=True,
        fresh_test_collector=lambda: collect_trusted_local_test_results(evidence, workspace, source="codex"),
    )
    provider_failed = provider_error is not None or any(
        "semantic extractor failed" in warning for warning in result.warnings
    )
    enforced_ids = {decision.claim_id for decision in result.decisions if is_enforced(decision)}
    unsupported = [
        {
            "claim": redact_text(claim.raw_text),
            "verdict": assessment.verdict,
            "epistemic_status": claim.epistemic_status,
        }
        for claim, assessment in zip(result.claims, result.assessments, strict=False)
        if claim.claim_id in enforced_ids
    ]
    denied = requires_correction(
        result.decisions, verification_available=result.verification_available, extraction_failed=provider_failed
    )
    already_continued = event.get("stop_hook_active") is True
    if denied and already_continued:
        decision = "BLOCK_LOOP_LIMIT"
        output: dict[str, Any] = {
            "systemMessage": (
                "Truth Firewall still found unsupported factual wording after the one allowed correction cycle. "
                "The result is recorded for review."
            ),
        }
    elif denied:
        decision = "BLOCK"
        output = {"decision": "block", "reason": _continuation_reason(unsupported, provider_failed, result)}
    else:
        decision = "ALLOW"
        output = {}

    audit = {
        "integration": "codex_stop",
        "session_id": session_id,
        "turn_id": run_id,
        "stop_hook_active": already_continued,
        "decision": decision,
        "provider_mode": "semantic"
        if provider is not None and result.extractor_mode == "semantic"
        else "offline_or_failed",
        "provider_failed": provider_failed,
        "provider_env": {
            "TRUTH_FIREWALL_PROVIDER": bool(os.environ.get("TRUTH_FIREWALL_PROVIDER")),
            "TRUTH_FIREWALL_API_KEY": bool(
                os.environ.get("TRUTH_FIREWALL_API_KEY") or os.environ.get("OPENAI_API_KEY")
            ),
            "TRUTH_FIREWALL_MODEL": bool(os.environ.get("TRUTH_FIREWALL_MODEL")),
        },
        "claim_count": len(result.claims),
        "verdicts": [
            {"claim": redact_text(claim.raw_text), "verdict": assessment.verdict}
            for claim, assessment in zip(result.claims, result.assessments, strict=False)
        ],
        "unsupported_claims": unsupported,
        "policy_decisions": [item.to_dict() for item in result.decisions],
        "pipeline_available": result.verification_available,
        "pipeline_errors": result.errors + result.warnings + load_errors,
        "audit_dir": result.audit_dir,
    }
    write_text(workspace, run / "stop_result.json", json.dumps(audit, indent=2, ensure_ascii=False))
    return output


def _materialize_tool_evidence(run: Path) -> None:
    """Turn the current run's raw PostToolUse events into verifier input at Stop time."""
    events = _read_jsonl(run / "hook_events.jsonl")
    path = checked_path(_storage_root(run), run / "evidence.jsonl")
    if path.exists():
        path.unlink()
    for envelope in events:
        event = envelope.get("payload")
        if isinstance(event, dict) and event.get("hook_event_name") == "PostToolUse":
            _record_tool_evidence(event, run)


def _record_tool_evidence(event: dict[str, Any], run: Path) -> None:
    tool_name = str(event.get("tool_name") or "unknown")
    tool_input = event.get("tool_input") if isinstance(event.get("tool_input"), dict) else {}
    tool_response = event.get("tool_response", event.get("tool_output", ""))
    output = (
        json.dumps(redact_value(tool_response), ensure_ascii=False, default=str)
        if not isinstance(tool_response, str)
        else redact_text(tool_response)
    )
    output = output[:20_000]
    structured_output = tool_response if isinstance(tool_response, dict) else {}
    wrapped_output = structured_output.get("stdout") or structured_output.get("output")
    output = redact_text(str(wrapped_output or output))[:20_000]
    command_value = tool_input.get("command") or tool_input.get("cmd")
    command = str(command_value) if command_value is not None else tool_name
    observed_runner = recognize_test_invocation(command)
    test_like = observed_runner is not None
    payload: dict[str, Any] = {
        "kind": "process" if observed_runner else "codex_tool_result",
        "tool_result_shape": "process"
        if isinstance(wrapped_output, str) or isinstance(tool_response, str)
        else "opaque",
        "tool_name": tool_name,
        "tool_input": redact_value(tool_input),
        "raw_event": _event_envelope(event, None)["payload"],
    }
    if test_like:
        payload.update(parse_test_stdout(output, command=command))
        payload["kind"] = "test_run"
    elif tool_name in {"exec_command", "terminal", "Bash"}:
        payload["kind"] = "process"
    timestamp = _event_time(event) or utc_now().isoformat()
    record = {
        "evidence_id": f"codex-{len(_read_jsonl(run / 'evidence.jsonl')) + 1:04d}",
        "evidence_type": EvidenceType.TEST.value if test_like else EvidenceType.RUNTIME_EVENT.value,
        "trust_level": TrustLevel.B.value,
        "source": "codex-hook",
        "timestamp": timestamp,
        "command": command,
        "cwd": _evidence_cwd(event.get("cwd"), run),
        "exit_code": _exit_code(tool_response),
        "stdout": output,
        "stderr": "",
        "provenance": f"codex_hook:PostToolUse:{tool_name}",
        "structured_payload": payload,
    }
    _append_jsonl(run / "evidence.jsonl", record)


def _continuation_reason(unsupported: list[dict[str, str]], provider_failed: bool, result: Any) -> str:
    if provider_failed:
        return (
            "Truth Firewall could not complete semantic claim extraction. Qualify factual claims explicitly or "
            "configure a working semantic provider, then make one corrected attempt."
        )
    if not result.verification_available:
        return (
            "Truth Firewall could not verify this response. Qualify factual claims explicitly, "
            "then make one corrected attempt."
        )
    details = "; ".join(f"{item['claim']} ({item['verdict']})" for item in unsupported[:4])
    if len(unsupported) > 4:
        details += f"; and {len(unsupported) - 4} more"
    return redact_text(
        f"Truth Firewall found asserted factual wording without fresh VERIFIED evidence: {details}. "
        "Correct or explicitly qualify those claims, then make one corrected attempt."
    )


def _fail_stop(event: dict[str, Any], workspace: Path, reason: str, *, run: Path | None = None) -> dict[str, Any]:
    run = run or _run_dir(
        workspace,
        _safe_component(event.get("session_id")) or "unknown-session",
        _safe_component(event.get("turn_id")) or "unknown-turn",
    )
    ensure_directory(workspace, run)
    envelope = _event_envelope(event, None)
    _append_jsonl(run / "hook_events.jsonl", envelope)
    write_text(
        workspace,
        run / "stop_result.json",
        json.dumps(
            {"decision": "BLOCK_FAIL_CLOSED", "reason": redact_text(reason), "hook_payload": envelope},
            indent=2,
            ensure_ascii=False,
        ),
    )
    return {
        "decision": "block",
        "reason": f"Truth Firewall failed safely: {reason} Correct or qualify factual claims, then try once more.",
    }


def _event_envelope(event: dict[str, Any], raw_payload: str | None) -> dict[str, Any]:
    safe = _scrub(event)
    raw = None
    if isinstance(raw_payload, str):
        try:
            raw = json.dumps(_scrub(json.loads(raw_payload)), ensure_ascii=False)
        except json.JSONDecodeError:
            raw = redact_text(raw_payload)
    return {"captured_at": utc_now().isoformat(), "timestamp": _event_time(event), "payload": safe, "raw_payload": raw}


def _scrub(value: Any, key: str = "") -> Any:
    if re.search(r"(?i)(api[_-]?key|secret|password|passwd|token|authorization|credential)", key):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(name): _scrub(item, str(name)) for name, item in value.items()}
    if isinstance(value, list):
        return [_scrub(item) for item in value]
    return redact_value(value)


def _event_time(event: dict[str, Any]) -> str | None:
    for key in ("timestamp", "event_timestamp", "created_at"):
        value = event.get(key)
        if isinstance(value, str) and parse_time(value):
            return value
    return None


def _exit_code(response: Any) -> int | None:
    if isinstance(response, dict):
        for key in ("exit_code", "exitCode", "code"):
            value = response.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                return value
    return None


def _evidence_cwd(value: Any, run: Path) -> str:
    workspace = run.parents[5]
    if not isinstance(value, str) or not value.strip():
        return str(workspace.resolve())
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = workspace / candidate
    try:
        return str(candidate.resolve())
    except (OSError, RuntimeError):
        return str(workspace.resolve())


def _safe_component(value: Any) -> str:
    return safe_component(value)


def _session_dir(workspace: Path, session_id: str) -> Path:
    root = workspace.resolve()
    candidate = root / ".truth-firewall" / "codex" / "sessions" / session_id
    return _resolve_under_workspace(root, candidate)


def _run_dir(workspace: Path, session_id: str, run_id: str) -> Path:
    root = workspace.resolve()
    candidate = _session_dir(root, session_id) / "runs" / run_id
    return _resolve_under_workspace(root, candidate)


def _resolve_under_workspace(workspace: Path, candidate: Path) -> Path:
    return checked_path(workspace, candidate)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(read_text(_storage_root(path), path))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    checked_path(_storage_root(path), path)
    if not path.is_file():
        return []
    records = []
    for line in read_text(_storage_root(path), path).splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            records.append(item)
    return records


def _append_jsonl(path: Path, value: dict[str, Any]) -> None:
    write_text(_storage_root(path), path, json.dumps(value, ensure_ascii=False, default=str) + "\n", append=True)


def _storage_root(path: Path) -> Path:
    for parent in path.parents:
        if parent.name == ".truth-firewall":
            return parent.parent
    raise UnsafePathError("Session storage has no workspace boundary")
