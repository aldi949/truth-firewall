"""Cursor integration.

Strength: POST_RESPONSE_CORRECTION.

afterAgentResponse is observe-only, so the message already on screen cannot be replaced.
stop may submit one follow-up user message. That is a correction after delivery, not a hard gate.
Shell text from the assistant is recorded and never executed.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from truth_firewall.errors import UnsafePathError
from truth_firewall.evidence.collectors.test_runner import parse_test_stdout, recognize_test_invocation
from truth_firewall.integrations.local_test_results import collect_trusted_local_test_results
from truth_firewall.pipeline import UNAVAILABLE, check_response
from truth_firewall.policy.engine import requires_correction
from truth_firewall.providers.config import load_provider_from_env
from truth_firewall.redaction import redact_text, redact_value
from truth_firewall.safety import is_secret_path, truncate_text
from truth_firewall.schemas import EvidenceRecord, EvidenceType, TrustLevel, json_dumps, utc_now
from truth_firewall.storage import checked_path, ensure_directory, read_text, safe_component, write_text

INTEGRATION_NAME = "cursor"
INTEGRATION_STRENGTH = "POST_RESPONSE_CORRECTION"
_NON_EXECUTORS = {"echo", "type", "cat", "printf", "write-output"}


def handle_cursor_event(event: dict, *, workspace: Path) -> dict:
    name = str(event.get("hook_event_name") or "")
    root = workspace.resolve()
    try:
        root = _workspace(event, workspace)
        if name == "afterShellExecution":
            _record_shell(event, root)
            return {}
        if name == "afterFileEdit":
            _record_edit(event, root)
            return {}
        if name == "postToolUse":
            _record_tool(event, root)
            return {}
        if name == "afterAgentResponse":
            _save_response(event, root)
            return {}
        if name == "stop":
            return _on_stop(event, root)
    except Exception as exc:
        _write_error(root, event, exc)
        if name == "stop":
            return {"followup_message": UNAVAILABLE}
        return {}
    return {}


def _on_stop(event: dict, root: Path) -> dict:
    _append_raw_event(event, root)
    if event.get("status") not in {None, "completed"}:
        return {}
    loop_count = event.get("loop_count", 0)
    if type(loop_count) is not int or loop_count < 0:
        return {"followup_message": UNAVAILABLE}
    if loop_count >= 1:
        return {}
    session = _session_dir(root, event)
    response_path = checked_path(root, session / "response.txt")
    if not response_path.is_file():
        return {}
    response = read_text(root, response_path)
    if response.startswith("Truth Firewall post-response correction."):
        return {}
    evidence = _load_session_evidence(checked_path(root, session / "evidence.jsonl"))
    provider, provider_error = _configured_provider()
    if provider_error is not None:
        _write_error(root, event, provider_error)
        return {"followup_message": UNAVAILABLE}
    result = check_response(
        response,
        evidence,
        provider=provider,
        offline=provider is None,
        runs_dir=ensure_directory(root, root / ".truth-firewall" / "runs"),
        now=utc_now(),
        workspace_root=root,
        collect_local=True,
        fresh_test_collector=lambda: collect_trusted_local_test_results(evidence, root, source="cursor"),
    )
    write_text(
        root,
        session / "last_result.json",
        json.dumps(result.to_dict(), indent=2, ensure_ascii=False),
    )
    if any("semantic extractor failed" in item for item in result.warnings):
        return {"followup_message": UNAVAILABLE}
    if not result.verification_available:
        return {"followup_message": UNAVAILABLE}
    if requires_correction(result.decisions, verification_available=result.verification_available):
        return {
            "followup_message": (
                "Truth Firewall post-response correction. "
                "The previous message was already shown; this is not a pre-display gate.\n\n"
                "Grounded revision:\n"
                f"{result.grounded_response}\n\n"
                "Use this revision for factual claims that were not verified."
            )
        }
    return {}


def _configured_provider():
    try:
        return load_provider_from_env(), None
    except Exception as exc:
        return None, exc


def _record_shell(event: dict, root: Path) -> None:
    command = str(event.get("command") or "")
    output = redact_text(str(event.get("output") or ""))
    output, truncated = truncate_text(output)
    first = command.strip().split(" ", 1)[0].lower()
    if first in _NON_EXECUTORS:
        return
    evidence_type = EvidenceType.TEST.value if _looks_like_test(command) else EvidenceType.PROCESS.value
    payload = {"kind": "cursor_shell", "sandbox": event.get("sandbox") is True, "stdout_truncated": truncated}
    if evidence_type == EvidenceType.TEST.value:
        payload.update(parse_test_stdout(output, command=command))
    record = _base_record(
        event,
        evidence_id=_next_id(root, event),
        evidence_type=evidence_type,
        command=command,
        stdout=output,
        stderr=redact_text(str(event.get("stderr") or "")),
        cwd=_cwd(event, root),
        payload=payload,
        provenance="cursor_hook:afterShellExecution",
        exit_code=_exit_code(event),
    )
    _append(root, event, record)


def _record_edit(event: dict, root: Path) -> None:
    file_path = str(event.get("file_path") or "")
    if not file_path or ".truth-firewall" in file_path.replace("\\", "/"):
        return
    if is_secret_path(Path(file_path)):
        return
    edits = event.get("edits") if isinstance(event.get("edits"), list) else []
    snippets = []
    for edit in edits[:20]:
        if isinstance(edit, dict):
            snippets.append(str(edit.get("new_string") or "")[:500])
    snippet, truncated = truncate_text("\n".join(snippets), 2000)
    payload = {
        "kind": "file_edit",
        "change_type": "edited",
        "file_path": file_path,
        "snippet_new": redact_text(snippet),
        "edit_count": len(edits),
        "snippet_truncated": truncated,
    }
    record = _base_record(
        event,
        evidence_id=_next_id(root, event),
        evidence_type=EvidenceType.RUNTIME_EVENT.value,
        command=None,
        stdout="",
        cwd=_cwd(event, root),
        payload=payload,
        provenance="cursor_hook:afterFileEdit",
        file_path=file_path,
    )
    _append(root, event, record)


def _record_tool(event: dict, root: Path) -> None:
    tool_name = str(event.get("tool_name") or "")
    raw_output = event.get("tool_output")
    text = raw_output if isinstance(raw_output, str) else json.dumps(raw_output or "", default=str)
    text, truncated = truncate_text(redact_text(text), 4000)
    payload = {
        "kind": "tool_result",
        "tool_name": tool_name,
        "original_truncated": truncated,
        "tool_input": redact_text(json.dumps(event.get("tool_input"), default=str)[:1000]),
    }
    record = _base_record(
        event,
        evidence_id=_next_id(root, event),
        evidence_type=EvidenceType.RUNTIME_EVENT.value,
        command=tool_name,
        stdout=text,
        cwd=_cwd(event, root),
        payload=payload,
        provenance="cursor_hook:postToolUse",
    )
    _append(root, event, record)


def _save_response(event: dict, root: Path) -> None:
    _append_raw_event(event, root)
    text = event.get("text")
    if not isinstance(text, str):
        return
    session = _session_dir(root, event)
    write_text(root, session / "response.txt", redact_text(text))


def _base_record(
    event: dict,
    *,
    evidence_id: str,
    evidence_type: str,
    command: str | None,
    stdout: str,
    cwd: str | None,
    payload: dict,
    provenance: str,
    file_path: str | None = None,
    stderr: str = "",
    exit_code: int | None = None,
) -> EvidenceRecord:
    stored = dict(payload)
    stored["event_name"] = str(event.get("hook_event_name") or "")
    stored["raw_event"] = _safe_raw_event(event)
    return EvidenceRecord(
        evidence_id=evidence_id,
        evidence_type=evidence_type,
        trust_level=TrustLevel.B.value,
        source="cursor-hook",
        timestamp=_timestamp(event),
        command=command,
        cwd=cwd,
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        file_path=file_path,
        file_hash=None,
        before_hash=None,
        after_hash=None,
        git_metadata_json=json_dumps({}),
        payload_json=json_dumps(stored),
        provenance=provenance,
        workspace_root=cwd,
    )


def _looks_like_test(command: str) -> bool:
    return recognize_test_invocation(command) is not None


def _append(root: Path, event: dict, record: EvidenceRecord) -> None:
    _append_raw_event(event, root)
    session = _session_dir(root, event)
    write_text(root, session / "evidence.jsonl", json.dumps(record.to_dict(), ensure_ascii=False) + "\n", append=True)


def _append_raw_event(event: dict, root: Path) -> None:
    session = _session_dir(root, event)
    line = json.dumps(_safe_raw_event(event), ensure_ascii=False)
    write_text(root, session / "events.jsonl", line + "\n", append=True)


def _load_session_evidence(path: Path) -> list[EvidenceRecord]:
    if not path.is_file():
        return []
    from truth_firewall.evidence.io import load_evidence_path

    records, _errors = load_evidence_path(path)
    return records


def _session_dir(root: Path, event: dict) -> Path:
    path = root.resolve() / ".truth-firewall" / "sessions" / _turn_key(event)
    return ensure_directory(root, path)


def _turn_key(event: dict) -> str:
    for field in ("generation_id", "conversation_id", "session_id"):
        value = event.get(field)
        if isinstance(value, str) and value.strip():
            safe = safe_component(value, limit=80)
            if safe:
                return safe
    raise UnsafePathError("The hook payload has no session identity")


def _exit_code(event: dict) -> int | None:
    for key in ("exit_code", "exitCode"):
        value = event.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return None


def _timestamp(event: dict) -> str:
    for key in ("timestamp", "time"):
        value = event.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return utc_now().isoformat()


def _safe_raw_event(event: dict) -> dict:
    raw = _scrub(event)
    encoded = json.dumps(raw, ensure_ascii=False, default=str)
    if len(encoded) > 8000:
        return {"hook_event_name": event.get("hook_event_name"), "truncated": True, "preview": encoded[:8000]}
    return raw if isinstance(raw, dict) else {"value": raw}


def _scrub(value, key=""):
    if re.search(r"(?i)(user_email|api[_-]?key|secret|password|passwd|token|authorization|credential)", key):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(name): _scrub(item, str(name)) for name, item in value.items()}
    if isinstance(value, list):
        return [_scrub(item) for item in value]
    return redact_value(value)


def _next_id(root: Path, event: dict) -> str:
    session = _session_dir(root, event)
    existing = checked_path(root, session / "evidence.jsonl")
    count = 0
    if existing.is_file():
        count = sum(1 for line in read_text(root, existing).splitlines() if line.strip())
    return f"b-{count + 1:03d}"


def _workspace(event: dict, fallback: Path) -> Path:
    root = fallback.resolve(strict=True)
    roots = event.get("workspace_roots")
    if isinstance(roots, list) and roots and isinstance(roots[0], str):
        candidate = _normalize_workspace_root(roots[0])
        if candidate.resolve(strict=False) != root:
            raise UnsafePathError("Hook workspace identity differs from the permitted workspace")
    return root


def _normalize_workspace_root(raw: str) -> Path:
    text = raw.strip()
    # Cursor on Windows sends "/C:/..." which Path does not treat as a drive root.
    if len(text) >= 4 and text[0] == "/" and text[2] == ":":
        text = text[1:]
    return Path(text)


def _cwd(event: dict, root: Path) -> str:
    cwd = event.get("cwd")
    if isinstance(cwd, str) and cwd:
        candidate = Path(cwd)
        return str((candidate if candidate.is_absolute() else root / candidate).resolve())
    return str(_workspace(event, root))


def _write_error(root: Path, event: dict, exc: Exception) -> None:
    try:
        session = _session_dir(root, event)
        write_text(root, session / "hook-error.txt", redact_text(str(exc)))
    except (OSError, UnsafePathError):
        return
