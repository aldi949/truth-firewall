"""Offline fallback extractor.

This is a structural baseline for tests and for runs without a provider.
It is not the product's language understanding.
"""

from __future__ import annotations

import re

from truth_firewall.claims.extractor import ExtractionResult
from truth_firewall.claims.proposition import parse_text, proposition_attributes
from truth_firewall.schemas import ClaimType, EpistemicStatus, make_claim

_NUMBER_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
}
_HYPOTHESIS = re.compile(
    r"\b(i think|i believe|my current hypothesis|hypothesis|might|may(?:\s+be)?|could|maybe|"
    r"possibly|likely|probably)\b",
    re.I,
)
_FUTURE = re.compile(r"\b(i will|i'll|i am going to|i'm going to|we should|i should|we ought to)\b", re.I)
_OPINION = re.compile(r"\b(in my opinion|i prefer|i like)\b", re.I)
_ACKNOWLEDGEMENT = re.compile(
    r"^(?:thanks\b|thank you\b|got it\b|sure\b|happy to help\b|i(?:'m| am) happy to help\b|"
    r"okay\b|ok\b|understood\b|(?:that\s+)?makes sense\b|i understand\b|"
    r"that sounds reasonable\b|that seems reasonable\b)",
    re.I,
)
_RESPONSE_NAVIGATION = re.compile(
    r"^(?:(?:next|first|then|overall|generally|for example|for now|in that case|if necessary|"
    r"if you want|in short|in summary|to summarize)\s*,|next steps\s*:|"
    r"here(?:'s| is| are)\b|the\s+(?:next|first|following)\s+step\b)",
    re.I,
)
_FRAMING_PREFIX = re.compile(r"^(?:at this point|as a suggestion|as an option)\s*,?\s*", re.I)
_ASSISTANT_MODAL = re.compile(
    r"^(?:(?:i|we|you)\s+(?:can|want to|plan to|hope to)|if you want\s*,\s*(?:i|we)\s+(?:can|could|would))\b",
    re.I,
)
_HORTATIVE = re.compile(r"^(?:let's|let us)\b", re.I)
_OPTION_OR_RECOMMENDATION = re.compile(
    r"^(?:(?:one|another|a|an|the)\s+)?(?:(?:better|best|possible|other)\s+)?"
    r"(?:option|approach|alternative|choice|recommendation)\b|"
    r"(?:my\s+)?(?:suggestion|recommendation)\s+(?:is\s+to|would\s+be\s+to)\b",
    re.I,
)
_PERSONAL_RECOMMENDATION = re.compile(
    r"^(?:personally\b|i(?:'d| would)\s+(?:keep|use|choose|prefer|recommend)\b)",
    re.I,
)
_ADVICE_SPEECH_ACT = re.compile(
    r"^(?:consider\s+\w+ing\b|i\s+(?:recommend|suggest|advise)\b|my\s+advice\s+is\b|"
    r"a\s+useful\s+next\s+step\s+is\s+to\b|the\s+easiest\s+option\s+is\s+to\b|"
    r"the\s+safest\s+approach\s+is\s+to\b|it\s+is\s+worth\s+\w+ing\b|"
    r"for\s+completeness\s*,|one\s+thing\s+to\s+try\s+is\b|"
    r"please\s+consider\b|it\s+would\s+make\s+sense\s+to\b|"
    r"(?:i|we|you)\s+(?:should|could|would|can)\s+\w+\b|"
    r"it\s+would\s+be\s+(?:useful|helpful|wise|better)\s+to\b|"
    r"(?:one|another|the)\s+option\s+is\s+to\b|my\s+preference\s+is\s+to\b)",
    re.I,
)
_MODAL_EVALUATION = re.compile(
    r"^(?:this|that|it)\s+(?:should|would|could|might|may)\s+be\s+"
    r"(?:enough|easier|simpler|better|best|worse|more\s+useful)\b",
    re.I,
)
_BELOW_COMMAND = re.compile(r"^below\s+(?:is|are)\s+(?:the\s+)?(?:command|instructions?|steps?)\b", re.I)
_PROCEDURAL_PREFIX = re.compile(
    r"^(?:(?:to\b.+,|(?:after that|before continuing|before proceeding|for now|in that case|"
    r"if necessary|generally|then|next)\s*,)\s*"
    r"(?:please\s+)?(?:run|execute|open|check|verify|inspect|restart|save|add|create|update|fix|edit|"
    r"install|leave|keep|stop|try|retry|wait|use|change|remove|delete|write|read|continue|review)\b|"
    r"if you want\s*,\s*(?:i|we)\s+(?:can|could|would)\b)",
    re.I,
)
_POINT_MODAL = re.compile(r"^at this point\s*,\s*(?:i|we|you)\s+(?:can|should|could|would)\b", re.I)
_YOU_SUGGESTION = re.compile(r"^you\s+could\s+(?:run|execute|open|check|verify|inspect|restart|save|retry)\b", re.I)
_ELLIPTICAL_FRAGMENT = re.compile(
    r"^(?:no|some|any|many|few)\s+[A-Za-z][\w-]*\s+(?:needed|required|necessary|available|done|found)\b",
    re.I,
)
_IMPERATIVE = re.compile(
    r"^(?:please\s+\w+\b|(?:run|execute|open|check|verify|add|create|update|fix)\b)",
    re.I,
)
_PATH = re.compile(
    r"\b(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+\b|\b[A-Za-z0-9_.-]+\.(?:py|md|txt|json|toml|yml|yaml|js|ts|tsx|rs|go)\b"
)
_ABSENCE = re.compile(
    r"\bno\s+([A-Za-z_][A-Za-z0-9_]*)\s+(references|matches|occurrences|usages)\b",
    re.I,
)
_CAUSED = re.compile(r"^(?P<subject>.+?)\s+is caused by\s+(?P<object>.+?)\.?$", re.I)
_ROOT = re.compile(r"^the root cause is\s+(?P<object>.+?)\.?$", re.I)
_ROOT_MIGHT = re.compile(r"^the root cause might be\s+(?P<object>.+?)\.?$", re.I)
_SUBJECTIVE_COMPLEMENT = re.compile(
    r"\b(?:better|worse|best|worst|great|good|bad|elegant|useful|interesting|beautiful|awful|"
    r"reasonable|fine|easier|simpler|enough)\b",
    re.I,
)
_NONASSERTIVE_START = re.compile(r"^(?:maybe|perhaps|possibly|probably)\b", re.I)
_REPORTED_SPEECH = re.compile(
    r"^(?:(?:the|a|an)\s+)?(?:[\w-]+\s+){0,4}[\w-]+\s+"
    r"(?:said|says|reported|reports|states?|claimed|claims|wrote|writes|reads?)"
    r"(?:\s+that\b|\s*[:'\"“‘])", re.I,
)
_CONNECTOR = re.compile(
    r"\s*;\s*(?:(?:therefore|thus|hence|consequently|as\s+a\s+result)\b,?\s*)?"
    r"|\s+\band\s+(?:therefore|thus|hence|consequently)\b\s+"
    r"|\s+\b(?:because|and|but|while|although|so|therefore|thus|hence|consequently)\b\s+"
    r"|\s*,\s*(?:therefore|thus|hence|consequently)\b\s*"
    r"|\s*,?\s+\bas\s+a\s+result\b,?\s+",
    re.I,
)


class DeterministicFallbackExtractor:
    name = "deterministic_fallback"

    def extract(self, response_text: str) -> ExtractionResult:
        masked = _mask_quotations(_mask_fences(response_text))
        claims = []
        warnings = ["deterministic fallback is a baseline, not semantic coverage"]
        for start, end in _split_sentences(masked):
            raw = response_text[start:end].strip()
            if not raw or raw.startswith("```"):
                continue
            # Preserve interrogative force when a sentence is split into clauses.
            if raw.rstrip().endswith("?"):
                continue
            pieces = _clause_pieces(raw)
            offsets = _piece_offsets(response_text, start, end, pieces)
            typed_lead = bool(len(pieces) > 1 and (lead := parse_text(pieces[0]))
                              and lead.kind in {"source", "config", "dependency"})
            for index, (piece, (piece_start, piece_end)) in enumerate(zip(pieces, offsets, strict=False)):
                claim = _claim_from_piece(
                    piece,
                    response_text,
                    piece_start,
                    piece_end,
                    claim_id=f"c-{len(claims) + 1:03d}",
                    complete_clause=len(pieces) > 1,
                    elliptical=index > 0 and (typed_lead or _semantic_clause_shape(piece)),
                )
                if claim is not None:
                    claims.append(claim)
        return ExtractionResult(claims=claims, warnings=warnings, mode=self.name)


def may_contain_unextracted_fact(response_text: str) -> bool:
    """Fail closed on unclassified prose unless it is clearly nonfactual.

    This is a delivery safeguard, not a second verifier. It is used only when
    extraction returned no claims, so it cannot promote text to VERIFIED.
    """
    masked = _mask_quotations(_mask_fences(response_text))
    for start, end in _split_sentences(masked):
        clause = _normalize_presentation(masked[start:end]).strip("* ").strip()
        if not clause or not re.search(r"[A-Za-z0-9]", clause):
            continue
        if clause.endswith("?") or _nonfactual_discourse(clause):
            continue
        if _epistemic(clause) != EpistemicStatus.ASSERTED_FACT.value or _IMPERATIVE.match(clause):
            continue
        return True
    return False


def _mask_fences(text: str) -> str:
    return re.sub(r"```.*?```", lambda match: " " * len(match.group(0)), text, flags=re.S)


def _mask_quotations(text: str) -> str:
    """Prevent quoted punctuation and nested quoted claims becoming assertions."""
    chars = list(text)
    stack: list[tuple[str, int]] = []
    pairs = {'"': '"', "'": "'", '“': '”', '‘': '’'}
    closers = set(pairs.values())
    for index, char in enumerate(text):
        if (char == "'" and index > 0 and index + 1 < len(text)
                and text[index - 1].isalnum() and text[index + 1].isalnum()):
            continue
        if stack and char == stack[-1][0]:
            _, start = stack.pop()
            for position in range(start, index + 1):
                if chars[position] != "\n":
                    chars[position] = " "
        elif char in pairs:
            stack.append((pairs[char], index))
        elif char in closers and stack:
            for position in range(stack[-1][1], len(chars)):
                if chars[position] != "\n":
                    chars[position] = " "
            break
    for _, start in stack:
        for position in range(start, len(chars)):
            if chars[position] != "\n":
                chars[position] = " "
    return "".join(chars)


def _split_sentences(text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    start = 0
    depth = 0
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if (char == "." and index > 0 and index + 1 < length and text[index + 1].isalnum()
                and (text[index - 1].isalnum() or text[index - 1] in ")]}")):
            index += 1
            continue
        if char in "([{":
            depth += 1
        elif char in ")]}" and depth:
            depth -= 1
        elif char == "\n" and depth == 0:
            _push(spans, text, start, index)
            start = index + 1
        elif char in ".!?" and depth == 0 and not (char == "." and index + 1 < length and text[index + 1] == "."):
            nxt = index + 1
            while nxt < length and text[nxt] in " \t":
                nxt += 1
            if nxt >= length or text[nxt] == "\n" or text[nxt].isalpha() or text[nxt] in "`.":
                _push(spans, text, start, index + 1)
                start = nxt
                index = start
                continue
        index += 1
    _push(spans, text, start, length)
    return spans


def _push(spans: list[tuple[int, int]], text: str, start: int, end: int) -> None:
    while start < end and text[start] in " \t":
        start += 1
    chunk = text[start:end].strip()
    if len(chunk) >= 2:
        spans.append((start, end))


def _clause_pieces(sentence: str) -> list[str]:
    if sentence.count("(") != sentence.count(")"):
        return [sentence]
    # Split only after the left side is already a complete admitted proposition.
    # The dependent remainder is separately extracted as uncertain unless it
    # independently satisfies a complete proof grammar.
    for match in re.finditer(r"\s+(?:and|that)\s+", sentence, re.I):
        left = sentence[:match.start()].strip(" ,;:")
        right = sentence[match.end():].strip(" ,;:")
        admitted = parse_text(left)
        if (admitted is not None and admitted.kind in {"source", "config", "dependency"}
                and len(right.split()) >= 2):
            return [left, right]
    for match in _CONNECTOR.finditer(sentence):
        left = sentence[: match.start()].strip(" ,;:")
        right = sentence[match.end() :].strip(" ,;:")
        if not left or not right:
            continue
        if (_is_claimable_clause(left, complete_clause=True) and _is_claimable_clause(right, complete_clause=True)
                or _semantic_clause_shape(left) and _semantic_clause_shape(right)):
            return _clause_pieces(left) + _clause_pieces(right)
    return [sentence]


def _semantic_clause_shape(text: str) -> bool:
    """Find two predicate-bearing clauses without splitting noun-list conjunctions."""
    if len(re.findall(r"[A-Za-z][\w-]*", text)) < 2:
        return False
    return re.search(
        r"\b(?:defines?|declares?|contains?|provides?|exposes?|imports?|sets?|returns?|yields?|"
        r"exists?|has|have|is|are|accepts?|inherits?)\b", text, re.I,
    ) is not None


def _is_claimable_clause(text: str, *, complete_clause: bool = False) -> bool:
    clause = text.strip().rstrip(".!?,;:")
    if (
        not clause
        or clause.endswith("?")
        or _nonfactual_discourse(clause)
        or _epistemic(clause) != EpistemicStatus.ASSERTED_FACT.value
    ):
        return False
    if re.search(r"\b(?:def|class|function)\s+[A-Za-z_]\w*\s*\(", clause):
        return False
    if _classify_type(clause) != ClaimType.OTHER.value:
        return True
    return _looks_assertive_factual(clause, complete_clause=complete_clause)


def _piece_offsets(text: str, start: int, end: int, pieces: list[str]) -> list[tuple[int, int]]:
    if len(pieces) == 1:
        return [(start, end)]
    window = text[start:end]
    cursor = 0
    offsets = []
    for piece in pieces:
        found = window.find(piece, cursor)
        if found < 0:
            offsets.append((start, end))
            continue
        offsets.append((start + found, start + found + len(piece)))
        cursor = found + len(piece)
    return offsets


def _claim_from_piece(
    piece: str,
    full: str,
    start: int,
    end: int,
    claim_id: str,
    *,
    complete_clause: bool = False,
    elliptical: bool = False,
):
    stripped = _normalize_presentation(piece)
    if stripped.startswith("|"):
        stripped = re.sub(r"\s*\|\s*", " ", stripped).strip()
    stripped = re.sub(r"^\([^)]{1,80}\)\s*", "", stripped).strip()
    stripped = stripped.strip("* ").strip()
    if not stripped or stripped.endswith("?"):
        return None
    if _IMPERATIVE.match(stripped):
        return _make_nonfactual_claim(stripped, full, start, end, claim_id, EpistemicStatus.INSTRUCTION.value)
    if _FUTURE.search(stripped) or re.search(r"\b(?:i|we)\s+would\s+\w+", stripped, re.I):
        status = EpistemicStatus.FUTURE_INTENT.value if _FUTURE.search(stripped) else EpistemicStatus.OPINION.value
        return _make_nonfactual_claim(stripped, full, start, end, claim_id, status)
    if _HYPOTHESIS.search(stripped):
        return _make_nonfactual_claim(stripped, full, start, end, claim_id, EpistemicStatus.HYPOTHESIS.value)
    if _OPINION.search(stripped):
        return _make_nonfactual_claim(stripped, full, start, end, claim_id, EpistemicStatus.OPINION.value)
    if _nonfactual_discourse(stripped):
        return None
    parsed_proposition = parse_text(stripped)
    if (re.search(r"\b(?:pytest|unittest|cargo test|go test|npm test)\s+(?:ran|runs|run|executed)\b", stripped, re.I)
            and (parsed_proposition is None or parsed_proposition.kind != ClaimType.TEST_RESULT.value)):
        return _make_factual_claim(stripped, full, start, end, claim_id, ClaimType.TEST_EXECUTION.value)
    if re.match(
        r"^(?:i|we)\s+(?:ran|run|executed)\s+(?:pytest|unittest|cargo test|go test|npm test)\b", stripped, re.I
    ):
        return _make_factual_claim(stripped, full, start, end, claim_id, ClaimType.TEST_EXECUTION.value)
    if re.search(r"\bselected\s+tests?\s+(?:finished|completed|ran)\s+with\s+\d+\s+passed\b", stripped, re.I):
        return _make_factual_claim(stripped, full, start, end, claim_id, ClaimType.TEST_RESULT.value)
    if re.match(r"^(def|class|async\s+def|function)\b", stripped):
        return None
    status = _epistemic(stripped)
    if status == "skip":
        return None
    proposition = parsed_proposition
    claim_type = proposition.kind if proposition is not None else _classify_type(stripped)
    if proposition is None and claim_type in {
        ClaimType.TEST_EXECUTION.value, ClaimType.TEST_RESULT.value, ClaimType.LINT.value,
        ClaimType.ACTION_CHANGE.value, ClaimType.ABSENCE.value,
    }:
        # Lexical mention of an operation is not an asserted event. Only the
        # complete proposition grammar can promote an operational claim type.
        claim_type = ClaimType.OTHER.value
    if (claim_type == ClaimType.OTHER.value and not elliptical
            and not _looks_assertive_factual(stripped, complete_clause=complete_clause)):
        return None
    subject, predicate, claim_object = _svo(stripped, claim_type)
    attributes = proposition_attributes(proposition) if proposition is not None else _attributes(stripped, claim_type)
    qualifiers = tuple(_qualifiers(stripped))
    scope = str(attributes.get("scope_path") or attributes.get("scope_kind") or "")
    normalized = re.sub(r"\s+", " ", stripped).strip()
    if not normalized.endswith("."):
        normalized += "."
    return make_claim(
        claim_id=claim_id,
        raw_text=full[start:end].strip(),
        normalized_claim=normalized,
        claim_type=claim_type,
        subject=subject,
        predicate=predicate,
        claim_object=claim_object,
        scope=scope,
        time_scope="future" if status == EpistemicStatus.FUTURE_INTENT.value else "unspecified",
        epistemic_status=status,
        source_start=start,
        source_end=end,
        qualifiers=qualifiers,
        attributes=attributes,
    )


def _make_factual_claim(piece, full, start, end, claim_id, claim_type):
    subject, predicate, claim_object = _svo(piece, claim_type)
    attributes = _attributes(piece, claim_type)
    normalized = re.sub(r"\s+", " ", piece).strip()
    if not normalized.endswith("."):
        normalized += "."
    return make_claim(
        claim_id=claim_id,
        raw_text=full[start:end].strip(),
        normalized_claim=normalized,
        claim_type=claim_type,
        subject=subject,
        predicate=predicate,
        claim_object=claim_object,
        scope=str(attributes.get("scope_path") or attributes.get("scope_kind") or ""),
        epistemic_status=EpistemicStatus.ASSERTED_FACT.value,
        source_start=start,
        source_end=end,
        attributes=attributes,
    )


def _make_nonfactual_claim(piece, full, start, end, claim_id, status):
    claim_type = _classify_type(piece)
    subject, predicate, claim_object = _svo(piece, claim_type)
    attributes = _attributes(piece, claim_type)
    normalized = re.sub(r"\s+", " ", piece).strip()
    if not normalized.endswith("."):
        normalized += "."
    return make_claim(
        claim_id=claim_id,
        raw_text=full[start:end].strip(),
        normalized_claim=normalized,
        claim_type=claim_type,
        subject=subject,
        predicate=predicate,
        claim_object=claim_object,
        scope=str(attributes.get("scope_path") or attributes.get("scope_kind") or ""),
        time_scope="future" if status == EpistemicStatus.FUTURE_INTENT.value else "unspecified",
        epistemic_status=status,
        source_start=start,
        source_end=end,
        qualifiers=tuple(_qualifiers(piece)),
        attributes=attributes,
    )


def _epistemic(text: str) -> str:
    if _FUTURE.search(text) and not re.search(r"\bi think\b", text, re.I):
        return EpistemicStatus.FUTURE_INTENT.value
    if re.search(r"\b(?:i|we)\s+would\s+\w+", text, re.I):
        return EpistemicStatus.OPINION.value
    if _HYPOTHESIS.search(text):
        return EpistemicStatus.HYPOTHESIS.value
    if _OPINION.search(text):
        return EpistemicStatus.OPINION.value
    if _IMPERATIVE.match(text) and not re.match(r"^i\b", text, re.I):
        return EpistemicStatus.INSTRUCTION.value
    return EpistemicStatus.ASSERTED_FACT.value


def _nonfactual_discourse(text: str) -> bool:
    """Exclude common response speech acts before the generic coverage fallback."""
    normalized = _normalize_presentation(text)
    # Drop parenthetical framing for classification only; retain it in the source span.
    clause = re.sub(r"^\([^)]{1,80}\)\s*", "", normalized).strip()
    clause = _FRAMING_PREFIX.sub("", clause)
    if re.fullmatch(r".+?\s+(?:is|would be|could be)\s+to\s+[A-Za-z][\w-]*(?:\s+.+)?[.!]?", clause, re.I):
        # A to-infinitive complement describes a proposed action, not an
        # observed completed event. This covers advice paraphrases by syntax.
        return True
    if re.match(
        r"^(?:run|execute|open|check|verify|inspect|restart|save|add|create|update|fix|edit|"
        r"install|leave|keep|stop|try|retry|wait|use|change|remove|delete|write|read|continue|review)\b",
        clause,
        re.I,
    ):
        return True
    return bool(
        _ACKNOWLEDGEMENT.match(clause)
        or _RESPONSE_NAVIGATION.match(clause)
        or _ASSISTANT_MODAL.match(clause)
        or _HORTATIVE.match(clause)
        or _OPTION_OR_RECOMMENDATION.match(clause)
        or _PERSONAL_RECOMMENDATION.match(clause)
        or _ADVICE_SPEECH_ACT.match(clause)
        or _MODAL_EVALUATION.match(clause)
        or _BELOW_COMMAND.match(clause)
        or _PROCEDURAL_PREFIX.match(clause)
        or _POINT_MODAL.match(clause)
        or _YOU_SUGGESTION.match(clause)
        or _ELLIPTICAL_FRAGMENT.match(clause)
        or _REPORTED_SPEECH.match(clause)
        or _epistemic(clause) in {EpistemicStatus.FUTURE_INTENT.value, EpistemicStatus.OPINION.value}
    )


def _normalize_presentation(text: str) -> str:
    value = text.strip()
    value = re.sub(r"^(?:(?:[-*+•])\s+|\d+[.)]\s+)", "", value)
    value = re.sub(r"^\*\*(.*?)\*\*$", r"\1", value).strip()
    return value


def _looks_assertive_factual(text: str, *, complete_clause: bool = False) -> bool:
    """Retain declarative state/event propositions, excluding non-assertive speech acts."""
    text = re.sub(r"`([^`]+)`", lambda match: match[1] if _PATH.fullmatch(match[1]) else match[0], text).strip()
    if (
        text.rstrip().endswith("?")
        or _SUBJECTIVE_COMPLEMENT.search(text)
        or _NONASSERTIVE_START.search(text)
        or _epistemic(text) != EpistemicStatus.ASSERTED_FACT.value
    ):
        return False
    words = re.findall(r"[A-Za-z][A-Za-z'-]*|\d+", text)
    if len(words) < 3:
        return False
    if not complete_clause and not text.rstrip().endswith((".", "!")):
        return False
    # This final coverage guard asks only whether the source looks like a complete
    # natural-language declarative with an explicit subject followed by a predicate
    # region. It deliberately does not infer predicate status from English suffixes.
    if re.match(
        r"^(?:because|and|but|while|although|therefore|thus|hence|consequently|so|yet|whereas|despite|"
        r"even though|which means|meaning|thereby|suggesting|after|before|when|whenever|now that|given that)\b",
        text,
        re.I,
    ):
        return False
    if re.match(r"^(?:if|when|while|although|unless|before|after)\b", text, re.I):
        return False
    # Require a clause-shaped subject and predicate region, while classifying speech
    # acts structurally rather than accumulating phrase-specific exclusions.
    tokens = re.findall(r"[A-Za-z][A-Za-z'-]*|\d+", text)
    if len(tokens) < 3:
        return False
    return bool(re.match(r"^[A-Za-z0-9_./-]+\b", text)) and _has_predicate_region(tokens)


def _has_predicate_region(tokens: list[str]) -> bool:
    """A lightweight predicate check: require a finite-state/action cue or passive form."""
    lowered = [token.lower() for token in tokens[1:]]
    if any(
        token
        in {
            "is",
            "are",
            "was",
            "were",
            "be",
            "been",
            "being",
            "has",
            "have",
            "had",
            "does",
            "do",
            "did",
            "will",
            "would",
            "can",
            "could",
            "should",
            "must",
            "remains",
            "became",
            "becomes",
            "seems",
            "looks",
            "returned",
            "crashed",
            "failed",
            "stopped",
            "passed",
            "diverged",
            "uses",
            "shows",
            "restarted",
            "saved",
            "completed",
            "exists",
            "contains",
            "includes",
            "supports",
            "runs",
            "ran",
        }
        for token in lowered
    ):
        return True
    # Cover regular inflections and passive participles without requiring sentence-initial capitalization.
    irregular = {
        "broke",
        "grew",
        "fell",
        "sent",
        "read",
        "went",
        "wrote",
        "became",
        "ran",
        "rose",
        "stood",
        "left",
        "lost",
        "won",
        "held",
        "kept",
        "found",
        "built",
        "caught",
        "brought",
        "thought",
        "took",
        "began",
        "gave",
        "hung",
        "hit",
        "came",
        "split",
        "shut",
        "froze",
        "got",
        "shrank",
        "arose",
        "slept",
        "knew",
        "overran",
        "withstood",
        "underwent",
        "overwrote",
        "withdrew",
        "swung",
        "spun",
        "sank",
        "rang",
        "laid",
        "led",
        "lent",
        "lit",
        "meant",
        "paid",
        "sought",
        "spent",
        "stuck",
        "struck",
        "taught",
        "wore",
        "wept",
    }
    # The first word after a simple subject is often the finite predicate; the
    # previous guard skipped that position and missed many irregular past forms.
    return any(token in irregular or re.search(r"(?:ed|ing|s)$", token) for token in lowered)


def _classify_type(text: str) -> str:
    low = text.lower()
    if re.search(r"\b(root cause|caused by|causes)\b", low):
        return ClaimType.CAUSAL.value
    if _ABSENCE.search(text):
        return ClaimType.ABSENCE.value
    test_outcome = re.search(r"\b(pass|passes|passed|failing|failed|failure|failures|ok|green)\b", low)
    if re.search(r"\btests?\b", low) and test_outcome:
        return ClaimType.TEST_RESULT.value
    if re.search(r"\b(?:i|we)\s+(?:ran|run|executed)\s+(?:pytest|unittest|cargo test|go test|npm test)\b", low):
        return ClaimType.TEST_EXECUTION.value
    if re.search(r"\b(i|we)\s+(ran|run|executed)\s+(pytest|unittest|cargo test|go test|npm test)\b", low) or (
        re.search(r"\btests?\b", low) and re.search(r"\b(ran|run|running|executed|execute)\b", low)
    ):
        return ClaimType.TEST_EXECUTION.value
    if re.search(r"\b(created|added|updated|implemented|wrote|edited|modified|changed|deleted|removed)\b", low):
        return ClaimType.ACTION_CHANGE.value
    if re.search(r"\b(exists|exist|is present|are present|does not exist|do not exist)\b", low):
        return ClaimType.EXISTENCE.value
    if re.search(r"\b(preserves|returns|raises|throws|behaves|behavior|behaviour)\b", low):
        return ClaimType.BEHAVIOR.value
    if re.search(r"\b(works?|working|unused|used|broken|correct|incorrect|enabled|disabled)\b", low):
        return ClaimType.BEHAVIOR.value
    if re.search(r"\b(fixed|completed|complete|finished)\b", low) and re.search(r"\b(bug|task|issue|work)\b", low):
        return ClaimType.COMPLETION.value
    if re.search(r"\b(deployed|deployment|released)\b", low):
        return ClaimType.DEPLOYMENT.value
    return ClaimType.OTHER.value


def _svo(text: str, claim_type: str) -> tuple[str, str, str]:
    match = _CAUSED.match(text.strip())
    if match:
        return match.group("subject"), "is caused by", match.group("object")
    match = _ROOT.match(text.strip()) or _ROOT_MIGHT.match(text.strip())
    if match:
        return "the root cause", "is", match.group("object")
    verb = re.search(
        r"\b(created|added|updated|implemented|ran|run|passed|failed|preserves|exists|fixed)\b",
        text,
        re.I,
    )
    predicate = verb.group(1) if verb else claim_type
    paths = _PATH.findall(text)
    claim_object = paths[0] if paths else ""
    subject = "I" if re.match(r"^i\b", text, re.I) else ""
    if claim_type == ClaimType.TEST_RESULT.value:
        subject = "tests"
        claim_object = predicate
    return subject, predicate, claim_object


def _attributes(text: str, claim_type: str) -> dict[str, object]:
    attributes: dict[str, object] = {}
    quantity = _quantity(text)
    if quantity is not None:
        attributes["quantity"] = quantity
    if re.search(r"\b(all|every|always|never)\b", text, re.I):
        attributes["universal"] = True
    paths = _PATH.findall(text)
    if paths:
        attributes["target_path"] = paths[0]
    if claim_type == ClaimType.TEST_RESULT.value:
        negated_failure = re.search(
            r"\b(?:no|zero)\s+tests?\s+failed\b|\bnone\s+of\s+the\s+tests?\s+failed\b",
            text,
            re.I,
        )
        if negated_failure:
            attributes["outcome"] = "pass"
            attributes["failed"] = 0
        else:
            attributes["outcome"] = "fail" if re.search(r"\bfail", text, re.I) else "pass"
    if claim_type in {ClaimType.TEST_EXECUTION.value, ClaimType.TEST_RESULT.value}:
        if re.search(r"\bpytest\b", text, re.I):
            attributes["command"] = "pytest"
    absence = _ABSENCE.search(text)
    if absence:
        attributes["query"] = absence.group(1)
        attributes["polarity"] = "absent"
    scope_kind, scope_path = _scope(text)
    attributes["scope_kind"] = scope_kind
    if scope_path:
        attributes["scope_path"] = scope_path
    symbol = _symbol(text, paths)
    if symbol and claim_type in {ClaimType.ACTION_CHANGE.value, ClaimType.BEHAVIOR.value, ClaimType.CAUSAL.value}:
        attributes["symbol"] = symbol
    if re.search(r"\b(does not exist|do not exist|is missing)\b", text, re.I):
        attributes["polarity"] = "absent"
    elif claim_type == ClaimType.EXISTENCE.value:
        attributes["polarity"] = "present"
    if claim_type == ClaimType.CAUSAL.value:
        _, _, claim_object = _svo(text, claim_type)
        if claim_object:
            attributes["factor"] = claim_object
    return attributes


def _quantity(text: str) -> int | None:
    digit = re.search(r"\b(\d+)\b", text)
    if digit:
        return int(digit.group(1))
    for word, value in _NUMBER_WORDS.items():
        if re.search(rf"\b{word}\b", text, re.I):
            return value
    return None


def _scope(text: str) -> tuple[str, str]:
    if re.search(r"\b(this workspace|the workspace|the repo|the repository|anywhere)\b", text, re.I):
        return "workspace", ""
    match = re.search(r"\b(?:in|under|within)\s+([A-Za-z0-9_./\\-]+)", text)
    if not match:
        return "unspecified", ""
    token = match.group(1).strip("./")
    if token.lower() in {"this", "the", "my"}:
        return "workspace", ""
    return "path", token


def _symbol(text: str, paths: list[str]) -> str:
    skip = {
        "i",
        "the",
        "a",
        "an",
        "in",
        "on",
        "of",
        "to",
        "for",
        "with",
        "from",
        "by",
        "and",
        "or",
        "all",
        "other",
        "tests",
        "test",
        "created",
        "added",
        "updated",
        "implemented",
        "preserves",
        "characters",
        "root",
        "cause",
        "bug",
        "file",
    }
    for token in re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", text):
        if token.lower() in skip:
            continue
        if any(token in path for path in paths):
            continue
        return token
    return ""


def _qualifiers(text: str) -> list[str]:
    found = []
    for cue in ("might", "may be", "i think", "hypothesis", "all", "only"):
        if re.search(rf"\b{cue}\b", text, re.I):
            found.append(cue)
    return found
