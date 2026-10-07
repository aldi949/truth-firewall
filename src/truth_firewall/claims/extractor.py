"""Claim extractor contract."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from truth_firewall.schemas import Claim


@dataclass
class ExtractionResult:
    claims: list[Claim]
    warnings: list[str]
    mode: str


class ClaimExtractor(Protocol):
    name: str

    def extract(self, response_text: str) -> ExtractionResult:
        """Turn assistant prose into structured claims. This is not verification."""
