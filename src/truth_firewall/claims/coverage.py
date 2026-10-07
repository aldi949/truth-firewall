"""Find assertive residual source text that no extracted claim covers."""

from __future__ import annotations

import re

from truth_firewall.claims.deterministic import may_contain_unextracted_fact
from truth_firewall.schemas import Claim

_CONNECTOR_ONLY = re.compile(r"[\W_]*(?:(?:and|but|or|while|although|because|therefore|then|also)[\W_]*)*", re.I)


def uncovered_assertive_spans(response_text: str, claims: list[Claim]) -> list[tuple[int, int]]:
    """A partial extraction must not disable the zero-claim safety principle."""
    covered: list[tuple[int, int]] = []
    for claim in claims:
        start, end = claim.source_start, claim.source_end
        if (start is None or end is None or start < 0 or end > len(response_text)
                or response_text[start:end] != claim.raw_text):
            continue
        covered.append((start, end))
    if not covered:
        return []  # The pipeline's existing zero-claim guard handles this case.
    covered.sort()
    merged = [covered[0]]
    for start, end in covered[1:]:
        left, right = merged[-1]
        if start <= right:
            merged[-1] = (left, max(right, end))
        else:
            merged.append((start, end))
    gaps: list[tuple[int, int]] = []
    cursor = 0
    for start, end in [*merged, (len(response_text), len(response_text))]:
        if cursor < start:
            gap = response_text[cursor:start]
            if _CONNECTOR_ONLY.fullmatch(gap) is None and may_contain_unextracted_fact(gap):
                gaps.append((cursor, start))
        cursor = end
    return gaps
