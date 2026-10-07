"""Bounded semantic routing of source-anchored repository assertions.

This module proposes propositions. It neither observes repository state nor
assigns verdicts. Every proposal is checked against the exact assertion span;
only the normal evidence contracts can subsequently establish its truth.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from pathlib import Path

from truth_firewall.claims.proposition import AtomicProposition, parse_atomic_proposition, parse_text
from truth_firewall.evidence_requirements import MINIMUM_EVIDENCE_TYPES
from truth_firewall.safety import is_secret_path, resolve_under_root
from truth_firewall.schemas import Claim, EpistemicStatus, json_dumps

_PATH = re.compile(r"(?<![\w/])(?:[\w.-]+/)*[\w.-]+\.(?:py|json|toml|txt|md|graphql|yaml|yml)\b", re.I)
_IDENT = r"[A-Za-z_][A-Za-z_0-9]*"
_SCALAR = re.compile(r"(?<![\w.])(?:true|false|null|-?\d+(?:\.\d+)?|\"[^\"\r\n]*\")", re.I)
_NUMBER_WORDS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
                 "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12}
_NEGATION = re.compile(
    r"\b(?:not|never|neither|nor|without|lacks?|is\s+absent|is\s+missing)\b", re.I,
)
_SECOND_ASSERTION = re.compile(
    r"\b(?:and|but|while|although)\b\s+(?:(?:the|a|an|this)\s+)?"
    r"(?:[\w./-]+\s+){0,4}(?:defines?|declares?|imports?|sets?|returns?|yields?|"
    r"contains?|has|provides?|exposes?|is|are|exists?)\b", re.I,
)
_STRUCTURAL_WORDS = {
    "a", "an", "the", "in", "inside", "within", "on", "at", "of", "from", "by", "as", "to", "for",
    "its", "this", "that", "there", "according", "local", "repository", "repo", "source", "module",
    "file", "artifact", "entry", "setting", "field", "value", "literal", "declared", "declaration",
    "defined", "defines", "define", "contains", "contain", "provides", "provide", "exposes", "expose",
    "has", "have", "is", "are", "was", "were", "registered", "registers", "register", "exists",
    "exist", "present", "available", "lives", "located", "named", "called", "class", "function",
    "test", "coroutine", "async", "imports", "import", "imported", "brings", "bring", "sets", "set",
    "equals", "equal", "parameters", "parameter", "arguments", "argument", "signature", "accepts",
    "accept", "takes", "take", "including", "self", "method", "config", "configuration", "dependency", "dependencies",
    "lists", "list", "pins", "pin", "version", "minimum", "requires", "require", "project", "metadata",
    "limit", "least", "release", "true", "false", "null", "s", *_NUMBER_WORDS,
}


@dataclass(frozen=True)
class PropositionCandidate:
    proposition: AtomicProposition
    source_span: tuple[int, int]
    context_span: tuple[int, int] | None = None

    def to_dict(self) -> dict[str, object]:
        return {"kind": self.proposition.kind, "terms": self.proposition.terms,
                "source_span": list(self.source_span),
                "context_span": list(self.context_span) if self.context_span else None}


def normalize_claims(
    claims: list[Claim], response_text: str, workspace_root: Path,
) -> tuple[list[Claim], dict[str, PropositionCandidate]]:
    """Keep deterministic proofs and propose bounded, source-faithful alternatives."""
    normalized: list[Claim] = []
    candidates: dict[str, PropositionCandidate] = {}
    for claim in claims:
        if claim.epistemic_status != EpistemicStatus.ASSERTED_FACT.value:
            normalized.append(claim)
            continue
        start, end = claim.source_start, claim.source_end
        if start is None or end is None or response_text[start:end] != claim.raw_text:
            normalized.append(claim)
            continue
        exact = parse_atomic_proposition(claim)
        if exact is not None:
            normalized.append(claim)
            candidates[claim.claim_id] = PropositionCandidate(exact, (start, end))
            continue
        context = _context(claim, normalized, response_text)
        proposed = parse_text(claim.normalized_claim)
        if proposed is None or not candidate_is_faithful(claim.raw_text, proposed, context[0]):
            proposed = propose(claim.raw_text, context=context[0], workspace_root=workspace_root)
        if proposed is None or not candidate_is_faithful(claim.raw_text, proposed, context[0]):
            normalized.append(claim)
            continue
        candidates[claim.claim_id] = PropositionCandidate(proposed, (start, end), context[1])
        attrs = {key: value for key, value in proposed.terms.items() if isinstance(value, (str, int, float, bool))}
        normalized.append(replace(
            claim,
            claim_type=proposed.kind,
            normalized_claim=claim.raw_text,
            required_evidence_types=MINIMUM_EVIDENCE_TYPES.get(proposed.kind, ()),
            attributes_json=json_dumps(attrs),
        ))
    return normalized, candidates


def _context(claim: Claim, preceding: list[Claim], full: str) -> tuple[str, tuple[int, int] | None]:
    if claim.source_start is None or not preceding:
        return "", None
    previous = preceding[-1]
    if previous.source_end is None or previous.source_start is None:
        return "", None
    gap = full[previous.source_end:claim.source_start]
    if len(gap) > 24 or not re.fullmatch(r"[\s,;:]*(?:and|but)?[\s,;:]*", gap, re.I):
        return "", None
    return previous.raw_text, (previous.source_start, previous.source_end)


def candidate_is_faithful(raw: str, proposition: AtomicProposition, context: str = "") -> bool:
    """Reject a parser's unsupported substitutions, polarity changes, and dropped atoms."""
    text = raw.strip().strip("` ")
    if (not text or ";" in text or _SECOND_ASSERTION.search(text)
            or re.search(r"\b(?:that|which|where|because|although|while)\b", text, re.I)):
        return False
    kind, terms = proposition.kind, proposition.terms
    if kind not in {"source", "config", "dependency", "existence"}:
        return False
    if kind != "existence" and _NEGATION.search(text):
        return False
    path = terms.get("target_path")
    if not isinstance(path, str) or not path or not _surface_fully_accounted(raw, proposition):
        return False
    paths = {match.group().lower() for match in _PATH.finditer(raw)}
    context_paths = {match.group().lower() for match in _PATH.finditer(context)}
    if path.lower() not in paths and path.lower() not in context_paths:
        return False
    if len(paths) > 1 and path.lower() not in paths:
        return False
    lowered = text.lower()
    if kind == "source":
        query = terms.get("query_kind")
        symbol = terms.get("symbol")
        if not isinstance(symbol, str) or not re.search(rf"(?<!\w){re.escape(symbol)}(?!\w)", raw):
            return False
        cues = {
            "class": r"\bclass\b",
            "function": r"\b(?:function|test|coroutine|async\s+def)\b",
            "definition": r"\b(?:defines?|declares?|provides?|exposes?)\b",
            "import": r"\b(?:imports?|imported|brings?\s+in)\b",
            "constant": r"\b(?:value|constant|sets?|equals?)\b",
            "signature": r"\b(?:parameters?|arguments?|signature|accepts?)\b",
            "method": r"\bmethod\b",
        }
        if query not in cues or not re.search(cues[query], lowered):
            return False
        if re.search(r"\bmethod\b", lowered) and query not in {"method", "signature"}:
            return False
        if (re.search(r"\bclass\b", lowered) and not re.search(r"\bmethod\b", lowered)
                and query != "class"):
            return False
        if re.search(r"\b(?:function|test|coroutine)\b", lowered) and query not in {"function", "signature"}:
            return False
        if re.search(r"\b(?:imports?|imported|brings?\s+in)\b", lowered) and query != "import":
            return False
        if re.search(r"\b(?:constant|literal\s+value)\b", lowered) and query != "constant":
            return False
        if query == "class" and not re.search(
            r"\b(?:defines?|declare(?:s|d)?|contains?|provides?|has|register(?:s|ed)?|"
            r"exists?|present|available|lives)\b"
            r"|\bis\s+(?:a|an)\s+class\b", lowered,
        ):
            return False
        if query == "function" and not re.search(
            r"\b(?:defines?|declare(?:s|d)?|contains?|provides?|has|exists?|present|available)\b"
            r"|\bis\s+(?:a|an)\s+(?:async\s+)?(?:function|coroutine)\b", lowered,
        ):
            return False
        if query == "method" and not re.search(
            r"\b(?:defines?|declares?|contains?|provides?|exposes?|has|exists?|present)\b", lowered,
        ):
            return False
        if query == "class" and re.search(r"\band\s+(?:a\s+)?(?:function|method|import)\b", lowered):
            return False
        if query != "signature" and re.search(r"\bwith\b", lowered):
            return False
        if query == "import" and re.search(r"\b(?:alias|as)\b", lowered):
            return False  # The current import contract does not prove aliases.
        if query in {"method", "signature"} and "class_name" in terms:
            class_name = terms["class_name"]
            if not isinstance(class_name, str) or not re.search(rf"\b{re.escape(class_name)}\b", raw + " " + context):
                return False
        if query in {"constant", "signature"} and not _value_is_anchored(raw, terms.get("value", terms.get("count"))):
            return False
        return True
    if kind == "config":
        if not re.search(r"\b(?:setting|field|value|config|configuration|sets?|equals?|is|records?)\b", lowered):
            return False
        key = terms.get("key")
        if not isinstance(key, str) or not key:
            return False
        leaf = key.split(".")[-1]
        if not re.search(rf"\b{re.escape(leaf)}\b", raw, re.I):
            return False
        return _value_is_anchored(raw, terms.get("value"))
    if kind == "dependency":
        package = terms.get("package")
        version = terms.get("version")
        operator = terms.get("operator")
        explicit = re.search(r"(?:==|>=|<=|~=|!=)", raw)
        if explicit and explicit.group() != operator:
            return False
        if re.search(r"\b(?:minimum|at\s+least)\b", lowered) and operator != ">=":
            return False
        if re.search(r"\b(?:pins?|exactly)\b", lowered) and operator != "==":
            return False
        return (isinstance(package, str) and isinstance(version, str)
                and re.search(rf"\b{re.escape(package)}\b", raw, re.I) is not None
                and version in raw and re.search(r"\b(?:dependenc|require|pin|list|declar)", lowered) is not None)
    polarity = terms.get("polarity")
    if re.search(r"\b(?:class|function|method|imports?|dependency|requires?|setting|field|constant|value)\b", lowered):
        return False
    absent = bool(_NEGATION.search(text))
    if absent != (polarity == "absent"):
        return False
    return re.search(r"\b(?:exists?|present|available|missing|absent|included|inside|contains?)\b", lowered) is not None


def _surface_fully_accounted(raw: str, proposition: AtomicProposition) -> bool:
    """An extra content-bearing modifier is an unproven atom, never free proof."""
    remaining = raw
    terms = proposition.terms
    entities = [terms.get("target_path"), terms.get("symbol"), terms.get("class_name"),
                terms.get("package"), terms.get("version"), terms.get("key")]
    key = terms.get("key")
    if isinstance(key, str):
        entities.extend(key.split("."))
    for value in entities:
        if isinstance(value, str) and value:
            remaining = re.sub(re.escape(value), " ", remaining, flags=re.I)
    value = terms.get("value", terms.get("count"))
    if isinstance(value, bool):
        remaining = re.sub(rf"\b{str(value).lower()}\b", " ", remaining, flags=re.I)
    elif isinstance(value, (str, int, float)):
        remaining = re.sub(re.escape(str(value)), " ", remaining, flags=re.I)
    return all(word.lower() in _STRUCTURAL_WORDS for word in re.findall(r"[A-Za-z][A-Za-z_0-9]*", remaining))


def _value_is_anchored(raw: str, value: object) -> bool:
    if value is None:
        return re.search(r"\bnull\b", raw, re.I) is not None
    if isinstance(value, bool):
        return re.search(rf"\b{str(value).lower()}\b", raw, re.I) is not None
    if isinstance(value, (int, float)):
        if re.search(rf"(?<![\w.]){re.escape(str(value))}(?![\w.])", raw):
            return True
        return isinstance(value, int) and any(number == value and re.search(rf"\b{word}\b", raw, re.I)
                                              for word, number in _NUMBER_WORDS.items())
    return isinstance(value, str) and value in raw


def propose(raw: str, *, context: str = "", workspace_root: Path | None = None) -> AtomicProposition | None:
    """Identify relation and entity slots, independent of sentence word order."""
    text = raw.strip().strip("| ")
    paths = list(dict.fromkeys(match.group().strip("`") for match in _PATH.finditer(text)))
    if not paths and context:
        paths = list(dict.fromkeys(match.group() for match in _PATH.finditer(context)))
    if len(paths) != 1:
        return None
    path = paths[0]
    lower = text.lower()
    if _NEGATION.search(text):
        if re.search(r"\b(?:missing|absent|does not exist)\b", lower):
            return AtomicProposition("existence", {"target_path": path, "polarity": "absent"})
        return None
    if re.search(r"\b(?:import|brings?\s+in)\b", lower):
        if re.search(r"\b(?:alias|as)\b", lower):
            return None
        match = re.search(rf"\b(?:imports?|brings?\s+in)\s+(?P<symbol>{_IDENT}(?:\.{_IDENT})*)", text, re.I)
        if match:
            return AtomicProposition("source", {"target_path": path, "query_kind": "import",
                                                 "symbol": match["symbol"]})
    if path.lower().endswith((".txt", ".toml")) and re.search(
        r"\b(?:dependenc\w*|require\w*|pin\w*|list\w*|declar\w*)\b", lower,
    ):
        dependency = _dependency_candidate(text, path)
        if dependency is not None:
            return dependency
    if re.search(r"\b(?:class|subclass)\b", lower):
        symbol = _near_label(text, "class")
        if symbol:
            return AtomicProposition("source", {"target_path": path, "query_kind": "class", "symbol": symbol})
    if re.search(r"\b(?:method)\b", lower):
        method = _near_label(text, "method")
        owner = _owner(text, context)
        if method and owner:
            return AtomicProposition("source", {"target_path": path, "query_kind": "method",
                                                 "class_name": owner, "symbol": method})
    if re.search(r"\b(?:function|test|coroutine)\b", lower):
        label = "function" if "function" in lower else "test" if "test" in lower else "coroutine"
        symbol = _near_label(text, label)
        if symbol:
            query = ("signature" if re.search(r"\b(?:parameters?|arguments?|signature|accepts?)\b", lower)
                     else "function")
            terms: dict[str, object] = {"target_path": path, "query_kind": query, "symbol": symbol}
            if query == "signature":
                count = _count(text)
                if count is None:
                    return None
                terms["count"] = count
            return AtomicProposition("source", terms)
    if path.lower().endswith((".json", ".toml")) and workspace_root is not None:
        config = _config_candidate(text, path, workspace_root)
        if config is not None:
            return config
    if path.lower().endswith(".py"):
        constant = next((item for item in re.findall(r"\b[A-Z][A-Z_0-9]+\b", text)
                         if item not in {"AST", "JSON", "TOML"}), None)
        value = _scalar_value(text)
        if constant and value is not _NO_VALUE and re.search(r"\b(?:value|constant|sets?|equals?)\b", lower):
            return AtomicProposition("source", {"target_path": path, "query_kind": "constant",
                                                 "symbol": constant, "value": value})
    if re.search(r"\b(?:exists?|present|available|included|inside|contains?)\b", lower):
        return AtomicProposition("existence", {"target_path": path, "polarity": "present"})
    return None


_NO_VALUE = object()


def _scalar_value(text: str) -> object:
    matches = list(_SCALAR.finditer(text))
    # Paths contain digits; take a value only after a value-bearing predicate.
    for match in reversed(matches):
        prior = text[:match.start()]
        if re.search(r"\b(?:is|equals?|to|value|of)\s*$", prior, re.I):
            try:
                return json.loads(match.group().lower() if match.group().lower() in {"true", "false", "null"}
                                  else match.group())
            except ValueError:
                return _NO_VALUE
    return _NO_VALUE


def _near_label(text: str, label: str) -> str | None:
    before = re.search(rf"(?P<name>{_IDENT})\s+(?:is\s+\w+\s+as\s+a\s+|is\s+a\s+|as\s+a\s+|a\s+)?{label}\b", text, re.I)
    if before and before["name"].lower() not in {
        "a", "an", "the", "as", "is", "this", "that", "async", "defines", "define",
        "contains", "contain", "declares", "declare", "provides", "provide", "exposes", "expose",
    }:
        return before["name"]
    after = re.search(rf"\b{label}\s+(?:named\s+)?(?P<name>{_IDENT})\b", text, re.I)
    if after:
        return after["name"]
    return None


def _owner(text: str, context: str) -> str | None:
    match = re.search(rf"\b(?:on|of|in)\s+(?P<name>{_IDENT})\b", text, re.I)
    if match:
        return match["name"]
    match = re.search(rf"\b(?P<name>{_IDENT})\s+(?:has|exposes|provides)\b", text, re.I)
    if match:
        return match["name"]
    match = re.search(rf"\b(?P<name>{_IDENT})\s+(?:class\s+)?(?:has|exposes|provides)\b", context, re.I)
    if match:
        return match["name"]
    match = re.search(rf"\bdefines\s+(?:class\s+)?(?P<name>{_IDENT})\b", context, re.I)
    return match["name"] if match else None


def _count(text: str) -> int | None:
    match = re.search(r"\b(?P<count>\d+|" + "|".join(_NUMBER_WORDS) + r")\s+(?:declared\s+)?parameters?\b", text, re.I)
    if not match:
        return None
    token = match["count"].lower()
    return int(token) if token.isdecimal() else _NUMBER_WORDS[token]


def _dependency_candidate(text: str, path: str) -> AtomicProposition | None:
    spec = re.search(r"\b(?P<package>[A-Za-z][A-Za-z0-9_.-]*)(?P<operator>==|>=|<=|~=|!=)"
                     r"(?P<version>[A-Za-z0-9][A-Za-z0-9_.+!-]*)", text)
    if spec:
        package, operator, version = spec["package"], spec["operator"], spec["version"]
    else:
        package_match = re.search(
            r"\b(?:pins?|declares?|requires?|lists?)\s+(?P<package>[A-Za-z][A-Za-z0-9_.-]*)", text, re.I,
        )
        version_match = re.search(
            r"\b(?:version|release)\s+(?:of\s+)?(?P<version>\d+(?:\.\d+)+(?:[A-Za-z0-9_.-]*)?)", text, re.I,
        )
        if not package_match or not version_match:
            return None
        package, version = package_match["package"], version_match["version"]
        operator = ">=" if re.search(r"\b(?:minimum|at\s+least)\b", text, re.I) else "=="
    return AtomicProposition("dependency", {"target_path": path,
                                            "package": package.lower().replace("_", "-"),
                                            "operator": operator, "version": version.rstrip(".")})


def _config_candidate(text: str, path: str, root: Path) -> AtomicProposition | None:
    value = _scalar_value(text)
    if value is _NO_VALUE:
        return None
    # Bounded schema lookup is routing only. The later authenticated collector
    # independently reads the chosen key/value and establishes the verdict.
    try:
        target = resolve_under_root(root, path)
        if is_secret_path(target) or not target.is_file() or target.stat().st_size > 1_000_000:
            return None
        if target.suffix.lower() == ".json":
            data = json.loads(target.read_text(encoding="utf-8"))
        else:
            import tomllib

            data = tomllib.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        return None
    keys: list[str] = []

    def walk(value: object, prefix: str = "") -> None:
        if len(keys) > 200:
            return
        if isinstance(value, dict):
            for name, child in value.items():
                if isinstance(name, str) and name.isidentifier():
                    walk(child, f"{prefix}.{name}" if prefix else name)
        elif type(value) in {str, int, float, bool, type(None)} and prefix:
            keys.append(prefix)

    walk(data)
    tokens = set(re.findall(r"[a-z_][a-z_0-9]*", text.lower()))
    matches = [key for key in keys if key.split(".")[-1].lower() in tokens]
    if len(matches) != 1:
        return None
    return AtomicProposition("config", {"target_path": path, "key": matches[0], "value": value})
