"""One full-proposition verification path: parse, admit, then entail."""

from __future__ import annotations

from datetime import datetime

from truth_firewall.claims.proposition import AtomicProposition, parse_atomic_proposition
from truth_firewall.evidence.collectors.test_runner import (
    is_code_edit,
    parse_test_stdout,
    recognized_test_execution,
)
from truth_firewall.schemas import Claim, ClaimAssessment, EpistemicStatus, EvidenceRecord, Verdict, parse_time
from truth_firewall.verification.contracts import admissible, contract_for
from truth_firewall.workspace import normalize_workspace_path


def assess(claim: Claim, evidence: list[EvidenceRecord], *, now: datetime,
           freshness_seconds: int, workspace_root: str,
           current_test_record_ids: set[int] | None = None,
           proposition: AtomicProposition | None = None) -> ClaimAssessment:
    if claim.epistemic_status != EpistemicStatus.ASSERTED_FACT.value:
        return _result(claim, Verdict.NOT_CHECKABLE, "This is not an asserted fact.")
    if claim.claim_type == "behavior":
        from truth_firewall.verification.rules import _behavior

        # Related evidence may justify an inference, never authoritative proof.
        return _behavior(claim, evidence, now, freshness_seconds)
    proposition = proposition or parse_atomic_proposition(claim)
    if proposition is None:
        return _result(claim, Verdict.UNKNOWN, "The complete proposition is outside the supported proof grammar.")
    contract = contract_for(claim, proposition)
    if contract is None:
        return _result(claim, Verdict.UNKNOWN, "Claim type and complete proposition are incompatible.")
    proving = []
    for record in evidence:
        if (proposition.kind == "test_result" and proposition.terms.get("temporal") == "current"
                and id(record) not in (current_test_record_ids or set())):
            continue
        allowed, _reason = admissible(record, contract, workspace_root=workspace_root,
                                      now=now, freshness_seconds=freshness_seconds)
        if allowed:
            proving.append(record)
    if not proving:
        detail = ("No controlled test observation was made in this verification cycle."
                  if proposition.kind == "test_result" and proposition.terms.get("temporal") == "current"
                  else "No trusted, fresh, in-scope evidence satisfies the contract.")
        return _result(claim, Verdict.UNKNOWN, detail,
                       freshness_ok=False)
    evaluator = {
        "existence": _existence, "absence": _absence, "action_change": _action,
        "test_execution": _test_execution, "test_result": _test_result, "lint": _lint,
        "process": _process, "git": _git, "config": _config, "runtime": _runtime,
        "source": _structure, "dependency": _structure,
        "causal": _causal,
    }.get(proposition.kind)
    if evaluator is None:
        return _result(claim, Verdict.UNKNOWN, "No entailment rule exists for this proposition.")
    return evaluator(claim, proposition, proving, evidence)


def _path(record: EvidenceRecord, target: object) -> bool:
    if not isinstance(target, str) or not target:
        return False
    wanted = normalize_workspace_path(target, record.workspace_root)
    actual = normalize_workspace_path(record.file_path, record.workspace_root)
    return wanted is not None and wanted == actual


def _latest(records: list[EvidenceRecord]) -> EvidenceRecord | None:
    """Resolve current observations by ledger sequence, never wall-clock claims."""
    if not records:
        return None
    if len(records) == 1:
        return records[0]
    if any(type(item.sequence) is not int for item in records):
        return None
    newest = max(item.sequence for item in records)
    selected = [item for item in records if item.sequence == newest]
    return selected[0] if len(selected) == 1 else None


def _existence(claim, proposition, proving, _all):
    wanted = proposition.terms
    observed = [r for r in proving if _path(r, wanted["target_path"])
                and type(r.structured_payload().get("exists")) is bool]
    if not observed:
        return _result(claim, Verdict.UNKNOWN, "No matching filesystem observation.")
    values = {r.structured_payload()["exists"] for r in observed}
    if len(values) != 1:
        return _result(claim, Verdict.UNKNOWN, "Conflicting filesystem observations.")
    agrees = values == {wanted["polarity"] == "present"}
    return _result(claim, Verdict.VERIFIED if agrees else Verdict.CONTRADICTED,
                   "The observed path state matches the complete claim."
                   if agrees else "The observed path state conflicts.",
                   observed)


def _action(claim, proposition, proving, _all):
    terms = proposition.terms
    matches = [r for r in proving if _path(r, terms["target_path"])
               and r.structured_payload().get("change_type") == terms["operation"]
               and r.structured_payload().get("transition_observed") is True]
    if not matches:
        return _result(claim, Verdict.UNKNOWN,
                       "Current existence does not prove creation or modification; no observed transition matches.")
    return _result(claim, Verdict.VERIFIED, "An observed path transition entails this operation only.", matches)


def _absence(claim, proposition, proving, _all):
    terms = proposition.terms
    matches = []
    for record in proving:
        payload = record.structured_payload()
        if payload.get("kind") != "search" or payload.get("query") != terms["query"]:
            continue
        if payload.get("complete") is not True or type(payload.get("match_count")) is not int:
            continue
        if terms["scope_kind"] == "workspace":
            scope_ok = payload.get("covers_workspace") is True
        else:
            scope_ok = normalize_workspace_path(payload.get("root"), record.workspace_root) == normalize_workspace_path(
                str(terms["scope_path"]), record.workspace_root)
        if scope_ok:
            matches.append(record)
    if not matches:
        return _result(claim, Verdict.UNKNOWN, "No complete literal search covers the claimed scope.")
    if any(r.structured_payload()["match_count"] > 0 for r in matches):
        return _result(claim, Verdict.CONTRADICTED, "The scoped search found a match.", matches)
    return _result(claim, Verdict.VERIFIED, "A complete scoped search found zero matches.", matches)


def _test_execution(claim, proposition, proving, _all):
    framework = proposition.terms["framework"].split()[0]
    matches = [r for r in proving if r.exit_code is not None and r.tool_attestation is True
               and (invocation := recognized_test_execution(r)) is not None
               and invocation.framework == framework]
    if not matches:
        return _result(claim, Verdict.UNKNOWN, "No authenticated direct runner execution matches.")
    return _result(claim, Verdict.VERIFIED, "An authenticated runner executed with a recorded exit code.", matches[:1])


_COORDINATES = ("passed", "failed", "skipped", "xfailed", "xpassed", "errors", "deselected", "collected")


def _test_result(claim, proposition, proving, all_evidence):
    terms = proposition.terms
    support, contradiction = [], []
    stale = False
    for record in proving:
        invocation = recognized_test_execution(record)
        if invocation is None or record.tool_attestation is not True:
            continue
        scope = terms["scope"]
        if scope == "full" and invocation.scope != "full":
            continue
        if scope in {"partial", "targeted"} and invocation.scope != "scoped":
            continue
        if scope == "targeted":
            target = str(terms.get("target_path") or "")
            if not any(normalize_workspace_path(path, record.workspace_root) ==
                       normalize_workspace_path(target, record.workspace_root) for path in invocation.targets):
                continue
        if _predates_edit(record, all_evidence):
            stale = True
            continue
        payload = record.structured_payload()
        if payload.get("trusted_result") is not True:
            continue
        summary = payload.get("result_counts")
        if not isinstance(summary, dict):
            continue
        if not any(key in summary for key in _COORDINATES):
            continue
        relation = _test_relation(terms, summary, record.exit_code)
        if relation == "support":
            support.append(record)
        elif relation == "contradict":
            contradiction.append(record)
    chosen = _latest(support + contradiction)
    if chosen in contradiction:
        shown = parse_test_stdout(chosen.stdout, command=chosen.command).get("passed")
        detail = (f"The authenticated result reports {shown} passed, conflicting with the claim."
                  if type(shown) is int else "Authenticated result conflicts with the claim.")
        return _result(claim, Verdict.CONTRADICTED, detail, [chosen])
    if chosen in support:
        return _result(claim, Verdict.VERIFIED, "Authenticated outcome entails the claimed counts and scope.", [chosen])
    return _result(claim, Verdict.UNKNOWN,
                   "Test receipt predates a relevant edit or does not cover the claimed outcome.",
                   freshness_ok=not stale)


def _test_relation(terms: dict, summary: dict, exit_code: int | None) -> str:
    if exit_code is None:
        return "unknown"
    counts = terms["counts"]
    for key, expected in counts.items():
        actual = summary.get(key)
        if type(actual) is int and actual != expected:
            return "contradict"
        if type(actual) is not int:
            return "unknown"
    failed = summary.get("failed", 0)
    errors = summary.get("errors", 0)
    if terms["outcome"] in {"pass", "no_failures"}:
        if exit_code != 0 or failed != 0 or errors != 0:
            return "contradict"
        if terms["outcome"] == "pass" and "passed" not in summary:
            return "unknown"
    if terms["outcome"] == "fail" and not (failed or errors or (exit_code not in (None, 0))):
        return "contradict"
    if terms["universal"]:
        if any(summary.get(key, 0) for key in ("skipped", "xfailed", "xpassed", "errors", "failed", "deselected")):
            return "contradict"
        if "passed" not in summary:
            return "unknown"
        collected = summary.get("collected")
        if collected is not None and collected != summary["passed"]:
            return "contradict"
    if terms.get("suite"):
        if any(summary.get(key, 0) for key in ("skipped", "xfailed", "xpassed", "errors", "failed", "deselected")):
            return "contradict"
        if summary.get("collected") != summary.get("passed"):
            return "unknown"
    return "support" if exit_code is not None else "unknown"


def _predates_edit(receipt: EvidenceRecord, evidence: list[EvidenceRecord]) -> bool:
    stamped = parse_time(receipt.timestamp)
    if stamped is None:
        return True
    for edit in evidence:
        if not is_code_edit(edit) or edit.workspace_root != receipt.workspace_root:
            continue
        if type(edit.sequence) is int and type(receipt.sequence) is int and edit.sequence > receipt.sequence:
            return True
        moment = parse_time(edit.timestamp)
        if moment is None:
            continue
        if moment > stamped or (
            moment == stamped and
            (edit.sequence is None or receipt.sequence is None or edit.sequence > receipt.sequence)
        ):
            return True
    return False


def _lint(claim, proposition, proving, all_evidence):
    outcome = proposition.terms["outcome"]
    matches = []
    for record in proving:
        if record.tool_attestation is not True or _predates_edit(record, all_evidence):
            continue
        identity = record.structured_payload().get("runner_identity")
        parsed = parse_test_stdout(record.stdout, command=record.command)
        if not isinstance(identity, dict) or identity.get("framework") != "ruff":
            continue
        tool = proposition.terms.get("tool")
        if tool and tool != "ruff":
            continue
        if parsed.get("lint_tool") != "ruff" or type(parsed.get("lint_success")) is not bool:
            continue
        agrees = parsed["lint_success"] == (outcome == "pass") and (record.exit_code == 0) == (outcome == "pass")
        matches.append((record, agrees))
    if not matches:
        return _result(claim, Verdict.UNKNOWN, "No authenticated lint result covers the claim.")
    latest = _latest([record for record, _agrees in matches])
    if latest is None:
        return _result(claim, Verdict.UNKNOWN, "Conflicting lint observations lack a reliable order.")
    chosen = next(item for item in matches if item[0] is latest)
    return _result(claim, Verdict.VERIFIED if chosen[1] else Verdict.CONTRADICTED,
                   "Authenticated lint outcome matches." if chosen[1]
                   else "Authenticated lint outcome conflicts.", [chosen[0]])


def _process(claim, proposition, proving, _all):
    terms = proposition.terms
    matches = [r for r in proving if r.tool_attestation is True and r.command == terms["command"]
               and r.exit_code is not None]
    if not matches:
        return _result(claim, Verdict.UNKNOWN, "No authenticated exact command record.")
    latest = _latest(matches)
    if latest is None:
        return _result(claim, Verdict.UNKNOWN, "Conflicting command observations lack a reliable order.")
    agrees = latest.exit_code == terms["exit_code"]
    return _result(claim, Verdict.VERIFIED if agrees else Verdict.CONTRADICTED,
                   "The recorded command exit code matches." if agrees else "The recorded exit code differs.", [latest])


def _git(claim, proposition, proving, _all):
    matches = [r for r in proving if r.structured_payload().get("kind") == "status"
               and r.command == "git status --porcelain=v1 -b" and r.exit_code == 0]
    if not matches:
        return _result(claim, Verdict.UNKNOWN, "No authenticated git status observation.")
    latest = _latest(matches)
    if latest is None:
        return _result(claim, Verdict.UNKNOWN, "Conflicting Git observations lack a reliable order.")
    clean = not any(line and not line.startswith("##") for line in latest.stdout.splitlines())
    agrees = clean == proposition.terms["clean"]
    return _result(claim, Verdict.VERIFIED if agrees else Verdict.CONTRADICTED,
                   "Git status matches." if agrees else "Git status conflicts.", [latest])


def _config(claim, proposition, proving, _all):
    terms = proposition.terms
    matches = [r for r in proving if _path(r, terms["target_path"])
               and r.structured_payload().get("kind") == "config_value"
               and r.structured_payload().get("key") == terms["key"]]
    if not matches:
        return _result(claim, Verdict.UNKNOWN, "No parsed first-party config observation.")
    latest = _latest(matches)
    if latest is None:
        return _result(claim, Verdict.UNKNOWN, "Conflicting config observations lack a reliable order.")
    actual = latest.structured_payload().get("value")
    agrees = type(actual) is type(terms["value"]) and actual == terms["value"]
    return _result(claim, Verdict.VERIFIED if agrees else Verdict.CONTRADICTED,
                   "Config value matches." if agrees else "Config value differs.", [latest])


def _structure(claim, proposition, proving, _all):
    terms = proposition.terms
    kind = "source_structure" if proposition.kind == "source" else "dependency_declaration"
    query = {key: value for key, value in terms.items() if key != "target_path"}
    matches = [record for record in proving if _path(record, terms["target_path"])
               and record.structured_payload().get("kind") == kind
               and record.structured_payload().get("query") == query
               and record.structured_payload().get("complete") is True
               and type(record.structured_payload().get("observed")) is bool]
    if not matches:
        return _result(claim, Verdict.UNKNOWN, "No complete first-party structural observation matches.")
    latest = _latest(matches)
    if latest is None:
        return _result(claim, Verdict.UNKNOWN, "Structural observations lack a reliable order.")
    observed = latest.structured_payload()["observed"]
    return _result(claim, Verdict.VERIFIED if observed else Verdict.CONTRADICTED,
                   "The local structure supports the proposition." if observed
                   else "The local structure contradicts the proposition.", [latest])


def _runtime(claim, proposition, proving, _all):
    return _result(claim, Verdict.UNKNOWN, "Runtime state requires an authenticated observer contract.")


def _causal(claim, proposition, proving, _all):
    # A pair of declared outcomes is still a report, not an independently observed intervention.
    return _result(claim, Verdict.UNKNOWN, "No independently executed controlled intervention proves this cause.")


def _result(claim: Claim, verdict: Verdict, explanation: str, records: list[EvidenceRecord] | None = None,
            *, freshness_ok: bool = True) -> ClaimAssessment:
    chosen = records or []
    return ClaimAssessment(claim_id=claim.claim_id, verdict=verdict.value,
                           evidence_ids=tuple(r.evidence_id for r in chosen), explanation=explanation,
                           scope_match="matched" if chosen else "missing", freshness_ok=freshness_ok,
                           contradiction=verdict is Verdict.CONTRADICTED,
                           confidence=0.9 if verdict is Verdict.VERIFIED else 0.2,
                           limitations=() if verdict is Verdict.VERIFIED else ("complete_proposition_not_proven",))
