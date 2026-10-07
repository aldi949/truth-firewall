"""Load evidence files without executing anything they describe."""

from __future__ import annotations

import json
from pathlib import Path

from truth_firewall.constants import MAX_EVIDENCE_RECORDS
from truth_firewall.errors import EvidenceError
from truth_firewall.safety import read_bounded_bytes
from truth_firewall.schemas import EvidenceRecord, evidence_from_dict


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def load_evidence_paths(paths: list[Path]) -> tuple[list[EvidenceRecord], list[str]]:
    records: list[EvidenceRecord] = []
    errors: list[str] = []
    for path in paths:
        loaded, path_errors = load_evidence_path(path)
        records.extend(loaded)
        errors.extend(path_errors)
        if len(records) > MAX_EVIDENCE_RECORDS:
            errors.append(f"evidence record cap {MAX_EVIDENCE_RECORDS} exceeded")
            return records[: MAX_EVIDENCE_RECORDS + 1], errors
    return records, errors


def load_evidence_path(path: Path) -> tuple[list[EvidenceRecord], list[str]]:
    if path.is_dir():
        records: list[EvidenceRecord] = []
        errors: list[str] = []
        for child in sorted(path.rglob("*")):
            if child.is_file() and child.suffix.lower() in {".json", ".jsonl"}:
                loaded, child_errors = load_evidence_path(child)
                records.extend(loaded)
                errors.extend(child_errors)
                if len(records) > MAX_EVIDENCE_RECORDS:
                    errors.append(f"evidence record cap {MAX_EVIDENCE_RECORDS} exceeded")
                    return records[: MAX_EVIDENCE_RECORDS + 1], errors
        return records, errors
    if not path.is_file():
        return [], [f"evidence path does not exist: {path}"]
    try:
        raw = read_bounded_bytes(path)
        text = raw.decode("utf-8")
    except EvidenceError as exc:
        return [], [str(exc)]
    except UnicodeError:
        return [], [f"evidence file {path.name} is not utf-8 text"]
    items, errors = _parse_text(text, origin=path.name)
    if len(items) > MAX_EVIDENCE_RECORDS:
        errors.append(f"evidence record cap {MAX_EVIDENCE_RECORDS} exceeded")
        items = items[: MAX_EVIDENCE_RECORDS + 1]
    records = []
    for index, item in enumerate(items, start=1):
        evidence_id = item.get("evidence_id")
        if not isinstance(evidence_id, str) or not evidence_id.strip():
            evidence_id = f"e-{len(records) + index:03d}"
        try:
            records.append(evidence_from_dict(item, evidence_id=evidence_id))
        except (TypeError, ValueError) as exc:
            errors.append(f"{path.name}: skipped a record ({exc})")
    return records, errors


def _parse_text(text: str, *, origin: str) -> tuple[list[dict], list[str]]:
    stripped = text.strip()
    if not stripped:
        return [], [f"{origin}: empty evidence file"]
    if origin.endswith(".jsonl") or _looks_like_jsonl(stripped):
        return _parse_jsonl(stripped, origin)
    try:
        loaded = json.loads(stripped, object_pairs_hook=_unique_object)
    except (json.JSONDecodeError, ValueError) as exc:
        return [], [f"{origin}: malformed JSON ({exc})"]
    if isinstance(loaded, dict) and isinstance(loaded.get("records"), list):
        loaded = loaded["records"]
    if isinstance(loaded, dict):
        loaded = [loaded]
    if not isinstance(loaded, list):
        return [], [f"{origin}: evidence JSON must be an object or a list"]
    records = []
    errors = []
    for item in loaded:
        if isinstance(item, dict):
            records.append(item)
        else:
            errors.append(f"{origin}: skipped a non-object evidence item")
    return records, errors


def _looks_like_jsonl(text: str) -> bool:
    lines = [line for line in text.splitlines() if line.strip()]
    return len(lines) > 1 and all(line.lstrip().startswith("{") for line in lines)


def _parse_jsonl(text: str, origin: str) -> tuple[list[dict], list[str]]:
    records = []
    errors = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            loaded = json.loads(line, object_pairs_hook=_unique_object)
        except (json.JSONDecodeError, ValueError) as exc:
            errors.append(f"{origin}:{line_number}: malformed JSON ({exc})")
            continue
        if isinstance(loaded, dict):
            records.append(loaded)
            if len(records) > MAX_EVIDENCE_RECORDS:
                errors.append(f"{origin}: evidence record cap {MAX_EVIDENCE_RECORDS} exceeded")
                break
        else:
            errors.append(f"{origin}:{line_number}: skipped a non-object item")
    return records, errors
