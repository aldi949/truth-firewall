"""A closed, full-span language for operational propositions.

Extraction may be approximate; this parser is the separate proof boundary.  A
successful parse accounts for every token, including counts and scope.  It never
uses provider-supplied attributes to decide that a proposition is complete.
Unrecognized prose is deliberately outside this small language.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from truth_firewall.schemas import Claim


@dataclass(frozen=True)
class AtomicProposition:
    kind: str
    terms: dict[str, object]


_NUMBER_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
}
_NUMBER = r"(?:\d+|" + "|".join(_NUMBER_WORDS) + r")"
_PATH = r"(?:`[^`\r\n]+`|[\w./\\:-]+)"
_IDENTIFIER = r"(?:`[\w.:-]+`|[\w.:-]+)"
_VALUE = r'(?:true|false|null|-?\d+(?:\.\d+)?|"(?:[^"\\\r\n]|\\["\\/bfnrt]|\\u[0-9a-fA-F]{4})*")'
_TEST_STATUS = r"(?:passed|pass|passes|failed|fail|fails|failing|reported\s+ok)"
_COUNT_STATUS = r"(?:passed|failed|skipped|xfailed|xpassed|errors?|deselected|collected)"


def normalize_presentation(text: str) -> str:
    value = text.strip()
    value = re.sub(r"^(?:[-*+•]\s+|\d+[.)]\s+)", "", value)
    if value.startswith("**") and value.endswith("**"):
        value = value[2:-2].strip()
    return value


def _clean(text: str) -> str | None:
    if not isinstance(text, str) or not text or any(ord(char) < 32 and char not in "\t" for char in text):
        return None
    value = normalize_presentation(text)
    if value.endswith((".", "!")):
        value = value[:-1].rstrip()
    if not value or value.endswith((".", "!", "?")):
        return None
    return re.sub(r"[ \t]+", " ", value)


def _number(text: str) -> int:
    return int(text) if text.isdecimal() else _NUMBER_WORDS[text.lower()]


def _path(text: str) -> str:
    return text.strip("`").replace("\\", "/").removeprefix("./").rstrip("/")


def _explicit_path(text: str) -> bool:
    """Require path syntax; a bare noun can name a concept instead of a file."""
    return text.startswith("`") or any(char in text for char in ("/", "\\", "."))


def _match(pattern: str, text: str):
    return re.fullmatch(pattern, text, re.I)


def parse_text(text: str) -> AtomicProposition | None:
    """Parse one complete assertion; unsupported tails cause failure, not truncation."""
    value = _clean(text)
    if value is None:
        return None
    tests = _test_proposition(value)
    if tests is not None:
        return tests
    match = _match(
        rf"(?P<file>(?:(?:the|a) file )?)(?P<path>{_PATH}) "
        r"(?P<state>exists?|is present|does not exist|do not exist|is missing|is absent)"
        r"(?: in (?:the|this) (?:workspace|repository|repo))?", value,
    )
    if match and (match["file"] or _explicit_path(match["path"])):
        return AtomicProposition("existence", {
            "target_path": _path(match["path"]),
            "polarity": "absent" if match["state"].lower() in {
                "does not exist", "do not exist", "is missing", "is absent",
            } else "present",
        })
    match = _match(
        rf"no (?P<query>{_IDENTIFIER}) (?:references|matches|occurrences|usages)"
        rf"(?: exist| remain| were found)? (?:in|under|within) "
        rf"(?P<scope>{_PATH}|(?:this|the) (?:workspace|repository|repo))",
        value,
    )
    if match:
        scope = match["scope"]
        workspace = bool(_match(r"(?:this|the) (?:workspace|repository|repo)", scope))
        return AtomicProposition("absence", {
            "query": match["query"].strip("`"), "scope_kind": "workspace" if workspace else "path",
            "scope_path": "" if workspace else _path(scope),
        })
    match = _match(
        rf"(?:i|we) (?P<operation>created|added|wrote|edited|modified|updated|changed|deleted|removed) "
        rf"(?:(?:the|a) file )?(?P<path>{_PATH})", value,
    )
    if not match:
        match = _match(
            rf"(?:(?:the|a) file )?(?P<path>{_PATH}) was "
            r"(?P<operation>created|added|written|edited|modified|updated|changed|deleted|removed)", value,
        )
    if match and _explicit_path(match["path"]):
        operation = match["operation"].lower()
        operation = (
            "created" if operation in {"created", "added"} else
            "deleted" if operation in {"deleted", "removed"} else "edited"
        )
        return AtomicProposition("action_change", {"target_path": _path(match["path"]), "operation": operation})
    match = _match(
        r"(?:(?:i|we) (?:ran|executed) (?P<runner>pytest|unittest|cargo test|go test|npm test)"
        r"|(?P<subject>pytest|unittest|cargo test|go test|npm test) (?:ran|executed))", value,
    )
    if match:
        runner = (match["runner"] or match["subject"]).lower()
        return AtomicProposition("test_execution", {"framework": runner, "command": runner})
    match = _match(
        r"(?:(?:the )?lint(?: check)?|(?P<tool>ruff|flake8|pylint|mypy|eslint)(?: lint(?: check)?)?) "
        r"(?P<status>passed|passes|failed|fails)", value,
    )
    if match:
        return AtomicProposition("lint", {
            "tool": (match["tool"] or "").lower(),
            "outcome": "pass" if match["status"].lower().startswith("pass") else "fail",
        })
    match = _match(r"(?:the )?command `(?P<command>[^`\r\n]+)` exited(?: with(?: code)?)? (?P<exit>-?\d+)", value)
    if match:
        return AtomicProposition("process", {"command": match["command"], "exit_code": int(match["exit"])})
    match = _match(r"(?:the )?git working tree is (?P<state>clean|dirty)", value)
    if match:
        return AtomicProposition("git", {"clean": match["state"].lower() == "clean"})
    match = _match(
        rf"(?:config|configuration) (?P<key>{_IDENTIFIER}) in (?P<path>{_PATH}) "
        rf"(?:is|equals) (?P<value>{_VALUE})", value,
    )
    if match:
        return AtomicProposition("config", {
            "target_path": _path(match["path"]), "key": match["key"].strip("`"),
            "value": json.loads(match["value"]),
        })
    match = _match(
        rf"(?:the )?(?P<key>{_IDENTIFIER}) setting in (?P<path>{_PATH}) "
        rf"(?:is|equals) (?P<value>{_VALUE})", value,
    ) or _match(
        rf"(?P<path>{_PATH}) sets (?P<key>{_IDENTIFIER}) to (?P<value>{_VALUE})", value,
    )
    if match and _explicit_path(match["path"]) and _path(match["path"]).lower().endswith((".toml", ".json")):
        return AtomicProposition("config", {
            "target_path": _path(match["path"]), "key": match["key"].strip("`"),
            "value": json.loads(match["value"]),
        })
    local = _local_source_proposition(value)
    if local is not None:
        return local
    match = _match(rf"runtime (?P<key>{_IDENTIFIER}) (?:is|equals) (?P<value>{_VALUE})", value)
    if match:
        return AtomicProposition("runtime", {"key": match["key"].strip("`"), "value": json.loads(match["value"])})
    match = _match(rf"(?:the )?(?P<effect>{_IDENTIFIER}) is caused by (?:the )?(?P<factor>{_IDENTIFIER})", value)
    if match:
        return AtomicProposition("causal", {"effect": match["effect"].strip("`"), "factor": match["factor"].strip("`")})
    return None


def _local_source_proposition(value: str) -> AtomicProposition | None:
    """Admit only complete, structurally inspectable local-file assertions."""
    match = _match(
        rf"(?P<path>{_PATH}) (?:defines|contains) (?:a |an )?(?P<kind>class|function|test) "
        rf"(?:named )?(?P<name>{_IDENTIFIER})", value,
    ) or _match(
        rf"(?P<path>{_PATH}) (?:defines|contains) (?:a |an )?(?P<name>{_IDENTIFIER}) "
        rf"(?P<kind>class|function|test)", value,
    )
    if match and _explicit_path(match["path"]):
        return AtomicProposition("source", {
            "target_path": _path(match["path"]),
            "query_kind": "class" if match["kind"].lower() == "class" else "function",
            "symbol": match["name"].strip("`"),
        })
    match = _match(rf"(?P<path>{_PATH}) defines (?P<name>{_IDENTIFIER})", value)
    if match and _explicit_path(match["path"]):
        return AtomicProposition("source", {
            "target_path": _path(match["path"]), "query_kind": "definition",
            "symbol": match["name"].strip("`"),
        })
    match = _match(
        rf"(?P<path>{_PATH}) has a (?P<name>{_IDENTIFIER}) function", value,
    )
    if match and _explicit_path(match["path"]):
        return AtomicProposition("source", {
            "target_path": _path(match["path"]), "query_kind": "function",
            "symbol": match["name"].strip("`"),
        })
    match = _match(
        rf"(?P<path>{_PATH}) (?:contains|defines) (?:a |an )?(?P<name>{_IDENTIFIER}) test", value,
    )
    if match and _explicit_path(match["path"]):
        return AtomicProposition("source", {
            "target_path": _path(match["path"]), "query_kind": "function",
            "symbol": match["name"].strip("`"),
        })
    match = _match(
        rf"(?P<path>{_PATH}) defines (?P<name>{_IDENTIFIER}) with (?P<count>{_NUMBER}) parameters?", value,
    )
    if match and _explicit_path(match["path"]):
        return AtomicProposition("source", {
            "target_path": _path(match["path"]), "query_kind": "signature",
            "symbol": match["name"].strip("`"), "count": _number(match["count"]),
        })
    match = _match(rf"(?P<path>{_PATH}) imports (?P<module>{_IDENTIFIER})", value)
    if match and _explicit_path(match["path"]):
        return AtomicProposition("source", {
            "target_path": _path(match["path"]), "query_kind": "import",
            "symbol": match["module"].strip("`"),
        })
    match = _match(
        rf"(?P<path>{_PATH}) sets (?P<name>{_IDENTIFIER}) to (?P<value>{_VALUE})", value,
    )
    if match and _explicit_path(match["path"]):
        return AtomicProposition("source", {
            "target_path": _path(match["path"]), "query_kind": "constant",
            "symbol": match["name"].strip("`"), "value": json.loads(match["value"]),
        })
    match = _match(
        rf"(?P<path>{_PATH}) declares (?P<package>[A-Za-z][A-Za-z0-9_.-]*)"
        r"(?P<operator>==|>=|<=|~=|!=)(?P<version>[A-Za-z0-9][A-Za-z0-9_.+!-]*)", value,
    )
    if match and _explicit_path(match["path"]):
        return AtomicProposition("dependency", {
            "target_path": _path(match["path"]), "package": match["package"].lower().replace("_", "-"),
            "operator": match["operator"], "version": match["version"],
        })
    return None


def _test_proposition(value: str) -> AtomicProposition | None:
    terms: dict[str, object] = {"counts": {}, "universal": False, "suite": False,
                                "scope": "full", "temporal": "current"}
    # The temporal frame is part of the complete proposition. Unknown frames
    # do not get promoted to a historical assertion by provider attributes.
    frame = _match(r"(?:the )?(?:last|recorded|previous) (?:controlled )?(?:pytest |test )?run (?P<body>.+)", value)
    if frame:
        value = frame["body"]
        terms["temporal"] = "historical"
        if value.lower() in {"passed", "failed"}:
            terms.update(suite=True, outcome="pass" if value.lower() == "passed" else "fail")
            return AtomicProposition("test_result", terms)
    match = _match(r"(?:no|zero|none of (?:the )?)\s*tests? (?P<status>failed|fail)", value)
    if match:
        terms.update(counts={"failed": 0}, outcome="no_failures")
        if match["status"].lower() == "failed":
            terms["temporal"] = "historical"
        return AtomicProposition("test_result", terms)
    match = _match(
        rf"(?:(?P<quantifier>all|every|the) )?(?:(?P<count>{_NUMBER}) )?tests? "
        rf"(?:(?:in|under|within) (?P<path>{_PATH}) )?(?P<status>{_TEST_STATUS})", value,
    )
    if match:
        passed = not match["status"].lower().startswith("fail")
        counts = {"passed" if passed else "failed": _number(match["count"])} if match["count"] else {}
        terms.update(counts=counts, outcome="pass" if passed else "fail",
                     universal=(match["quantifier"] or "").lower() in {"all", "every"})
        if match["status"].lower() in {"passed", "failed"}:
            terms["temporal"] = "historical"
        if match["path"]:
            terms.update(scope="targeted", target_path=_path(match["path"]))
        return AtomicProposition("test_result", terms)
    match = _match(rf"(?:the |all )?test suite (?P<status>{_TEST_STATUS})", value)
    if match:
        terms.update(suite=True, outcome="fail" if match["status"].lower().startswith("fail") else "pass")
        if match["status"].lower() in {"passed", "failed"}:
            terms["temporal"] = "historical"
        return AtomicProposition("test_result", terms)
    match = _match(
        rf"(?:the )?selected tests? (?:finished|completed|ran) with (?P<count>{_NUMBER}) passed", value,
    )
    if match:
        terms.update(counts={"passed": _number(match["count"])}, outcome="pass", scope="partial")
        terms["temporal"] = "historical"
        return AtomicProposition("test_result", terms)
    # A summary is one structured outcome vector. Duplicate coordinates are
    # ambiguous and rejected, including duplicate coordinates with equal values.
    if _match(rf"{_NUMBER} {_COUNT_STATUS}(?:,\s*{_NUMBER} {_COUNT_STATUS})*", value):
        counts: dict[str, int] = {}
        for part in value.split(","):
            number, status = part.strip().rsplit(" ", 1)
            status = "errors" if status.lower() in {"error", "errors"} else status.lower()
            if status in counts:
                return None
            counts[status] = _number(number)
        terms.update(counts=counts, outcome="counts")
        terms["temporal"] = "historical"
        return AtomicProposition("test_result", terms)
    return None


def parse_atomic_proposition(claim: Claim) -> AtomicProposition | None:
    """Prevent a provider normalization from dropping unsupported source content."""
    raw = parse_text(claim.raw_text)
    normalized = parse_text(claim.normalized_claim)
    return raw if raw is not None and raw == normalized else None


def proposition_attributes(proposition: AtomicProposition) -> dict[str, object]:
    """Flatten parser terms for the existing public claim schema, never as proof."""
    attrs = dict(proposition.terms)
    if proposition.kind == "test_result":
        attrs.update(attrs.pop("counts"))
        attrs["test_scope"] = attrs.pop("scope")
        outcome = attrs.get("outcome")
        coordinate = "passed" if outcome == "pass" else "failed" if outcome == "fail" else None
        if coordinate and coordinate in attrs:
            attrs["quantity"] = attrs[coordinate]
    return attrs
