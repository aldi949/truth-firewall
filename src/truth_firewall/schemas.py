"""Typed records for claims, evidence, assessments, and a check run."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from truth_firewall.constants import MAX_EVIDENCE_PAYLOAD_CHARS, MAX_PATH_CHARS, MAX_TEXT_FIELD_CHARS
from truth_firewall.evidence_requirements import MINIMUM_EVIDENCE_TYPES
from truth_firewall.workspace import canonical_workspace_root


class EpistemicStatus(str, Enum):
    ASSERTED_FACT = "asserted_fact"
    HYPOTHESIS = "hypothesis"
    FUTURE_INTENT = "future_intent"
    INSTRUCTION = "instruction"
    OPINION = "opinion"


class Modality(str, Enum):
    FACTUAL = "factual"
    HYPOTHESIS = "hypothesis"
    INTENT = "intent"
    INSTRUCTION = "instruction"
    OPINION = "opinion"


class ClaimType(str, Enum):
    ACTION_CHANGE = "action_change"
    CREATION = "creation"
    MODIFICATION = "modification"
    DELETION = "deletion"
    LINT = "lint"
    CONFIG = "config"
    SOURCE = "source"
    DEPENDENCY = "dependency"
    RUNTIME = "runtime"
    PROCESS = "process"
    GIT = "git"
    TEST_EXECUTION = "test_execution"
    TEST_RESULT = "test_result"
    FILE_STATE = "file_repository_state"
    EXISTENCE = "existence"
    ABSENCE = "absence"
    BEHAVIOR = "behavior"
    COMPLETION = "completion"
    CAUSAL = "causal"
    EXTERNAL_FACT = "external_fact"
    ENVIRONMENT = "environment"
    DEPLOYMENT = "deployment"
    OTHER = "other"


class TrustLevel(str, Enum):
    A = "A"
    B = "B"
    C = "C"
    D = "D"


class EvidenceType(str, Enum):
    FILESYSTEM = "filesystem"
    GIT = "git"
    PROCESS = "process"
    TEST = "test"
    RUNTIME_EVENT = "runtime_event"
    ASSISTANT_TEXT = "assistant_text"


class Verdict(str, Enum):
    VERIFIED = "VERIFIED"
    INFERRED = "INFERRED"
    UNKNOWN = "UNKNOWN"
    CONTRADICTED = "CONTRADICTED"
    NOT_CHECKABLE = "NOT_CHECKABLE"


class PolicyAction(str, Enum):
    ALLOW = "allow"
    AUDIT_ONLY = "audit_only"
    RELABEL = "relabel"
    BLOCK_OR_REWRITE = "block_or_rewrite"


EPISTEMIC_TO_MODALITY = {
    EpistemicStatus.ASSERTED_FACT.value: Modality.FACTUAL.value,
    EpistemicStatus.HYPOTHESIS.value: Modality.HYPOTHESIS.value,
    EpistemicStatus.FUTURE_INTENT.value: Modality.INTENT.value,
    EpistemicStatus.INSTRUCTION.value: Modality.INSTRUCTION.value,
    EpistemicStatus.OPINION.value: Modality.OPINION.value,
}

CLAIM_TYPE_ALIASES = {
    "action/change": ClaimType.ACTION_CHANGE.value,
    "action": ClaimType.ACTION_CHANGE.value,
    "change": ClaimType.ACTION_CHANGE.value,
    "test execution": ClaimType.TEST_EXECUTION.value,
    "test_result": ClaimType.TEST_RESULT.value,
    "test result": ClaimType.TEST_RESULT.value,
    "file/repository state": ClaimType.FILE_STATE.value,
    "file_state": ClaimType.FILE_STATE.value,
    "root_cause": ClaimType.CAUSAL.value,
    "root-cause": ClaimType.CAUSAL.value,
    "root_cause_claim": ClaimType.CAUSAL.value,
    "causal/root-cause": ClaimType.CAUSAL.value,
    "environment/runtime state": ClaimType.ENVIRONMENT.value,
    "deployment/state claim": ClaimType.DEPLOYMENT.value,
}

REQUIRED_EVIDENCE = MINIMUM_EVIDENCE_TYPES

ATTRIBUTE_KEYS = {
    "quantity",
    "failed",
    "outcome",
    "universal",
    "target_path",
    "symbol",
    "query",
    "scope_kind",
    "scope_path",
    "polarity",
    "command",
    "factor",
    "effect",
    "passed",
    "skipped",
    "xfailed",
    "xpassed",
    "errors",
    "deselected",
    "collected",
    "suite",
    "test_scope",
    "operation",
    "key",
    "value",
    "exit_code",
    "tool",
    "clean",
    "query_kind",
    "package",
    "operator",
    "version",
    "count",
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def json_dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)


def parse_time(value: str | None) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed


@dataclass(frozen=True)
class Claim:
    claim_id: str
    raw_text: str
    normalized_claim: str
    claim_type: str
    subject: str
    predicate: str
    claim_object: str
    scope: str
    time_scope: str
    modality: str
    epistemic_status: str
    source_start: int | None
    source_end: int | None
    qualifiers: tuple[str, ...]
    required_evidence_types: tuple[str, ...]
    attributes_json: str

    def attributes(self) -> dict[str, Any]:
        loaded = json.loads(self.attributes_json or "{}")
        return loaded if isinstance(loaded, dict) else {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "raw_text": self.raw_text,
            "normalized_claim": self.normalized_claim,
            "claim_type": self.claim_type,
            "subject": self.subject,
            "predicate": self.predicate,
            "object": self.claim_object,
            "scope": self.scope,
            "time_scope": self.time_scope,
            "modality": self.modality,
            "epistemic_status": self.epistemic_status,
            "source_span": (
                None
                if self.source_start is None or self.source_end is None
                else [self.source_start, self.source_end]
            ),
            "qualifiers": list(self.qualifiers),
            "required_evidence_types": list(self.required_evidence_types),
            "attributes": self.attributes(),
        }


@dataclass(frozen=True)
class EvidenceRecord:
    evidence_id: str
    evidence_type: str
    trust_level: str
    source: str
    timestamp: str | None
    command: str | None
    cwd: str | None
    exit_code: int | None
    stdout: str
    stderr: str
    file_path: str | None
    file_hash: str | None
    before_hash: str | None
    after_hash: str | None
    git_metadata_json: str
    payload_json: str
    provenance: str
    workspace_root: str | None = None
    sequence: int | None = None
    # In-memory assertion set only by a first-party execution collector. It is never serialized.
    tool_attestation: bool = False
    # Process-local collector seal; deliberately excluded from serialized output.
    collector_attestation: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.evidence_id, str) or len(self.evidence_id) > MAX_PATH_CHARS:
            object.__setattr__(self, "evidence_id", "")
        for name in ("workspace_root", "cwd", "file_path"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or len(value) > MAX_PATH_CHARS):
                object.__setattr__(self, name, None)
        if self.command is not None and (not isinstance(self.command, str) or len(self.command) > MAX_TEXT_FIELD_CHARS):
            object.__setattr__(self, "command", None)
        if not isinstance(self.source, str) or len(self.source) > MAX_PATH_CHARS:
            object.__setattr__(self, "source", "unknown")
        if not isinstance(self.provenance, str) or len(self.provenance) > MAX_PATH_CHARS:
            object.__setattr__(self, "provenance", "unknown")
        canonical = canonical_workspace_root(self.workspace_root or self.cwd)
        object.__setattr__(self, "workspace_root", canonical)
        if isinstance(self.exit_code, bool) or (self.exit_code is not None and not isinstance(self.exit_code, int)):
            object.__setattr__(self, "exit_code", None)
        if not isinstance(self.stdout, str):
            object.__setattr__(self, "stdout", "")
        if not isinstance(self.stderr, str):
            object.__setattr__(self, "stderr", "")
        if not isinstance(self.payload_json, str):
            object.__setattr__(self, "payload_json", "{}")
        if not isinstance(self.git_metadata_json, str):
            object.__setattr__(self, "git_metadata_json", "{}")
        if len(self.stdout) > MAX_TEXT_FIELD_CHARS:
            object.__setattr__(self, "stdout", self.stdout[:MAX_TEXT_FIELD_CHARS])
        if len(self.stderr) > MAX_TEXT_FIELD_CHARS:
            object.__setattr__(self, "stderr", self.stderr[:MAX_TEXT_FIELD_CHARS])
        if len(self.payload_json) > MAX_EVIDENCE_PAYLOAD_CHARS:
            object.__setattr__(self, "payload_json", "{}")
        try:
            payload = json.loads(self.payload_json or "{}")
        except (TypeError, json.JSONDecodeError):
            payload = {}
        if isinstance(payload, dict):
            for key in ("exists", "controlled", "covers_workspace", "success", "lint_success", "complete", "is_file"):
                if key in payload and not isinstance(payload[key], bool):
                    payload.pop(key)
            for key in (
                "passed", "failed", "skipped", "xfailed", "xpassed", "deselected",
                "errors", "match_count", "collected", "executed", "lint_violations",
            ):
                value = payload.get(key)
                if key in payload and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
                    payload.pop(key)
            if "matches" in payload and not isinstance(payload["matches"], list):
                payload.pop("matches")
            object.__setattr__(self, "payload_json", json_dumps(payload))
        else:
            object.__setattr__(self, "payload_json", "{}")
        try:
            meta = json.loads(self.git_metadata_json or "{}")
        except (TypeError, ValueError, RecursionError):
            meta = {}
        object.__setattr__(self, "git_metadata_json", json_dumps(meta if isinstance(meta, dict) else {}))

    def structured_payload(self) -> dict[str, Any]:
        loaded = json.loads(self.payload_json or "{}")
        return loaded if isinstance(loaded, dict) else {"value": loaded}

    def git_metadata(self) -> dict[str, Any]:
        loaded = json.loads(self.git_metadata_json or "{}")
        return loaded if isinstance(loaded, dict) else {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "evidence_type": self.evidence_type,
            "trust_level": self.trust_level,
            "source": self.source,
            "timestamp": self.timestamp,
            "command": self.command,
            "cwd": self.cwd,
            "workspace_root": self.workspace_root,
            "sequence": self.sequence,
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "file_path": self.file_path,
            "file_hash": self.file_hash,
            "before_hash": self.before_hash,
            "after_hash": self.after_hash,
            "git_metadata": self.git_metadata(),
            "structured_payload": self.structured_payload(),
            "provenance": self.provenance,
        }


@dataclass(frozen=True)
class ClaimAssessment:
    claim_id: str
    verdict: str
    evidence_ids: tuple[str, ...]
    explanation: str
    scope_match: str
    freshness_ok: bool
    contradiction: bool
    confidence: float
    limitations: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "verdict": self.verdict,
            "evidence_ids": list(self.evidence_ids),
            "explanation": self.explanation,
            "scope_match": self.scope_match,
            "freshness_ok": self.freshness_ok,
            "contradiction": self.contradiction,
            "confidence": self.confidence,
            "limitations": list(self.limitations),
        }


@dataclass(frozen=True)
class PolicyDecision:
    claim_id: str
    verdict: str
    action: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "verdict": self.verdict,
            "action": self.action,
            "reason": self.reason,
        }


@dataclass
class RunBundle:
    response_text: str
    evidence: tuple[EvidenceRecord, ...]
    source: str = "check"

    def to_dict(self) -> dict[str, Any]:
        return {
            "response_text": self.response_text,
            "evidence": [item.to_dict() for item in self.evidence],
            "source": self.source,
        }


def normalize_claim_type(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip().lower().replace("-", "_")
    text = CLAIM_TYPE_ALIASES.get(text, text)
    text = CLAIM_TYPE_ALIASES.get(value.strip().lower(), text)
    allowed = {item.value for item in ClaimType}
    return text if text in allowed else None


def normalize_epistemic(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        return EpistemicStatus.ASSERTED_FACT.value
    text = value.strip().lower()
    aliases = {
        "fact": EpistemicStatus.ASSERTED_FACT.value,
        "asserted": EpistemicStatus.ASSERTED_FACT.value,
        "asserted_fact": EpistemicStatus.ASSERTED_FACT.value,
        "hypothesis": EpistemicStatus.HYPOTHESIS.value,
        "uncertain": EpistemicStatus.HYPOTHESIS.value,
        "future": EpistemicStatus.FUTURE_INTENT.value,
        "future_intent": EpistemicStatus.FUTURE_INTENT.value,
        "intent": EpistemicStatus.FUTURE_INTENT.value,
        "instruction": EpistemicStatus.INSTRUCTION.value,
        "imperative": EpistemicStatus.INSTRUCTION.value,
        "opinion": EpistemicStatus.OPINION.value,
    }
    return aliases.get(text, EpistemicStatus.ASSERTED_FACT.value)


def sanitize_attributes(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    cleaned: dict[str, Any] = {}
    for key, value in raw.items():
        if key not in ATTRIBUTE_KEYS:
            continue
        if key in {"quantity", "failed", "passed", "skipped", "xfailed", "xpassed",
                   "errors", "deselected", "collected"}:
            cleaned[key] = _parse_quantity(value)
        elif key in {"universal", "suite", "clean"}:
            if isinstance(value, bool):
                cleaned[key] = value
        elif key == "outcome":
            if isinstance(value, str) and value in {"pass", "fail", "no_failures", "counts"}:
                cleaned[key] = value
        elif key == "scope_kind":
            if isinstance(value, str) and value in {"path", "workspace", "unspecified"}:
                cleaned[key] = value
        elif key == "polarity":
            if isinstance(value, str) and value in {"present", "absent"}:
                cleaned[key] = value
        elif isinstance(value, (str, int, float)) and not isinstance(value, bool):
            cleaned[key] = value
    return {key: value for key, value in cleaned.items() if value is not None}


def _parse_quantity(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def make_claim(
    *,
    claim_id: str,
    raw_text: str,
    normalized_claim: str,
    claim_type: str,
    subject: str = "",
    predicate: str = "",
    claim_object: str = "",
    scope: str = "",
    time_scope: str = "unspecified",
    epistemic_status: str = EpistemicStatus.ASSERTED_FACT.value,
    source_start: int | None = None,
    source_end: int | None = None,
    qualifiers: tuple[str, ...] | list[str] = (),
    required_evidence_types: tuple[str, ...] | list[str] | None = None,
    attributes: dict[str, Any] | None = None,
) -> Claim:
    status = normalize_epistemic(epistemic_status)
    ctype = normalize_claim_type(claim_type) or ClaimType.OTHER.value
    required = (
        tuple(required_evidence_types)
        if required_evidence_types
        else REQUIRED_EVIDENCE.get(ctype, ())
    )
    return Claim(
        claim_id=claim_id,
        raw_text=raw_text,
        normalized_claim=normalized_claim.strip(),
        claim_type=ctype,
        subject=subject.strip(),
        predicate=predicate.strip(),
        claim_object=claim_object.strip(),
        scope=scope.strip(),
        time_scope=time_scope or "unspecified",
        modality=EPISTEMIC_TO_MODALITY[status],
        epistemic_status=status,
        source_start=source_start,
        source_end=source_end,
        qualifiers=tuple(qualifiers),
        required_evidence_types=tuple(required),
        attributes_json=json_dumps(sanitize_attributes(attributes or {})),
    )


def claim_from_dict(raw: dict[str, Any], *, claim_id: str, response_text: str) -> Claim | None:
    if not isinstance(raw, dict):
        return None
    ctype = normalize_claim_type(raw.get("claim_type") or raw.get("type"))
    if ctype is None:
        return None
    raw_text = raw.get("raw_text") or raw.get("text") or ""
    if not isinstance(raw_text, str) or not raw_text.strip():
        return None
    normalized = raw.get("normalized_claim") or raw_text
    if not isinstance(normalized, str) or not normalized.strip():
        return None
    start, end = _span(raw.get("source_span"), response_text, raw_text)
    status = normalize_epistemic(raw.get("epistemic_status") or raw.get("modality"))
    if raw.get("epistemic_status") and raw.get("modality"):
        status = normalize_epistemic(raw.get("epistemic_status"))
    qualifiers = raw.get("qualifiers") or []
    if not isinstance(qualifiers, list):
        qualifiers = []
    required = raw.get("required_evidence_types")
    if not isinstance(required, list) or not required:
        required = list(REQUIRED_EVIDENCE.get(ctype, ()))
    return make_claim(
        claim_id=claim_id,
        raw_text=raw_text.strip(),
        normalized_claim=str(normalized).strip(),
        claim_type=ctype,
        subject=str(raw.get("subject") or ""),
        predicate=str(raw.get("predicate") or ""),
        claim_object=str(raw.get("object") or raw.get("claim_object") or ""),
        scope=str(raw.get("scope") or ""),
        time_scope=str(raw.get("time_scope") or "unspecified"),
        epistemic_status=status,
        source_start=start,
        source_end=end,
        qualifiers=tuple(str(item) for item in qualifiers if isinstance(item, str)),
        required_evidence_types=tuple(str(item) for item in required),
        attributes=raw.get("attributes") if isinstance(raw.get("attributes"), dict) else {},
    )


def _span(value: Any, response_text: str, raw_text: str) -> tuple[int | None, int | None]:
    if (
        isinstance(value, (list, tuple))
        and len(value) == 2
        and all(isinstance(item, int) for item in value)
        and 0 <= value[0] <= value[1] <= len(response_text)
        and response_text[value[0] : value[1]] == raw_text
    ):
        return value[0], value[1]
    index = response_text.find(raw_text)
    if index >= 0:
        return index, index + len(raw_text)
    return None, None


def evidence_from_dict(raw: dict[str, Any], *, evidence_id: str) -> EvidenceRecord:
    if not isinstance(raw, dict):
        raise TypeError("evidence must be an object")
    payload = raw.get("structured_payload", raw.get("payload", {}))
    if not isinstance(payload, dict):
        payload = {"value": payload}
    git_meta = raw.get("git_metadata") or {}
    if not isinstance(git_meta, dict):
        git_meta = {"value": git_meta}
    exit_code = raw.get("exit_code")
    if isinstance(exit_code, bool) or not isinstance(exit_code, int):
        exit_code = None
    timestamp = raw.get("timestamp")
    timestamp_text = timestamp if isinstance(timestamp, str) and parse_time(timestamp) else None
    # Serialized trust is a claim by the input, never an attestation by this host.
    declared = str(raw.get("trust_level") or TrustLevel.C.value).upper()
    trust = TrustLevel.D.value if declared == TrustLevel.D.value else TrustLevel.C.value
    evidence_type = str(raw.get("evidence_type") or EvidenceType.RUNTIME_EVENT.value)
    if evidence_type not in {item.value for item in EvidenceType}:
        raise ValueError("unknown evidence type")
    return EvidenceRecord(
        evidence_id=evidence_id,
        evidence_type=evidence_type,
        trust_level=trust,
        source=str(raw.get("source") or "import"),
        timestamp=timestamp_text,
        command=raw.get("command") if isinstance(raw.get("command"), str) else None,
        cwd=raw.get("cwd") if isinstance(raw.get("cwd"), str) else None,
        exit_code=exit_code,
        stdout=str(raw.get("stdout") or ""),
        stderr=str(raw.get("stderr") or ""),
        file_path=raw.get("file_path") if isinstance(raw.get("file_path"), str) else None,
        file_hash=raw.get("file_hash") if isinstance(raw.get("file_hash"), str) else None,
        before_hash=raw.get("before_hash") if isinstance(raw.get("before_hash"), str) else None,
        after_hash=raw.get("after_hash") if isinstance(raw.get("after_hash"), str) else None,
        git_metadata_json=json_dumps(git_meta),
        payload_json=json_dumps(payload),
        provenance=str(raw.get("provenance") or raw.get("source") or "import"),
        workspace_root=raw.get("workspace_root") if isinstance(raw.get("workspace_root"), str) else None,
        sequence=(
            raw.get("sequence")
            if isinstance(raw.get("sequence"), int) and not isinstance(raw.get("sequence"), bool)
            else None
        ),
    )
