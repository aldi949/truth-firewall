"""Validate provider claim JSON. The provider does not assign verdicts."""

from __future__ import annotations

import re

from truth_firewall.claims.extractor import ExtractionResult
from truth_firewall.constants import MAX_CLAIMS
from truth_firewall.errors import ExtractorError
from truth_firewall.providers.base import LLMProvider
from truth_firewall.schemas import claim_from_dict

_FRAGMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\s*=\s*[^,\n]+)?$")


class SemanticClaimExtractor:
    name = "semantic"

    def __init__(self, provider: LLMProvider) -> None:
        self.provider = provider

    def extract(self, response_text: str) -> ExtractionResult:
        try:
            raw_claims = self.provider.extract_claims(response_text)
        except Exception as exc:
            raise ExtractorError(str(exc)) from exc
        warnings: list[str] = []
        claims = []
        search_from = 0
        if not isinstance(raw_claims, list):
            raise ExtractorError("provider returned claims in an unexpected shape")
        for raw in raw_claims:
            if len(claims) >= MAX_CLAIMS:
                warnings.append(f"claim cap {MAX_CLAIMS} reached")
                break
            if not isinstance(raw, dict):
                warnings.append("dropped a non-object claim")
                continue
            # Verdicts from the model are interpretations, never stored as proof.
            raw = {key: value for key, value in raw.items() if key not in {"verdict", "evidence_ids"}}
            raw_text = raw.get("raw_text") or raw.get("text") or ""
            if isinstance(raw_text, str):
                source_span = raw.get("source_span")
                valid_span = (
                    isinstance(source_span, (list, tuple))
                    and len(source_span) == 2
                    and all(isinstance(item, int) for item in source_span)
                    and 0 <= source_span[0] <= source_span[1] <= len(response_text)
                    and response_text[source_span[0] : source_span[1]] == raw_text
                )
                if not valid_span:
                    found = response_text.find(raw_text, search_from)
                    if found >= 0:
                        raw["source_span"] = [found, found + len(raw_text)]
                        search_from = found + len(raw_text)
                else:
                    search_from = max(search_from, source_span[1])
            claim = claim_from_dict(raw, claim_id=f"c-{len(claims) + 1:03d}", response_text=response_text)
            if claim is None:
                warnings.append("dropped a claim with an unknown type or empty text")
                continue
            if is_signature_fragment(claim.raw_text, response_text):
                warnings.append("dropped a parameter fragment split out of a signature")
                continue
            claims.append(claim)
        return ExtractionResult(claims=claims, warnings=warnings, mode=self.name)


def is_signature_fragment(raw_text: str, full_text: str) -> bool:
    stripped = raw_text.strip().strip(",").strip()
    if not stripped or "\n" in stripped or len(stripped) > 80:
        return False
    if not _FRAGMENT.fullmatch(stripped):
        return False
    name = stripped.split("=", 1)[0].strip()
    return re.search(r"\([^)]*\b" + re.escape(name) + r"\b[^)]*\)", full_text) is not None
