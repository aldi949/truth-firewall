"""Deterministic provider for tests and labeled fixtures."""

from __future__ import annotations

from typing import Any, Callable

from truth_firewall.errors import ProviderError


class FakeProvider:
    def __init__(
        self,
        claims: list[dict[str, Any]] | Callable[[str], list[dict[str, Any]]] | None = None,
        *,
        explanation: dict[str, Any] | None = None,
        rewritten: str | None = None,
        fail: bool = False,
    ) -> None:
        self._claims = claims if claims is not None else []
        self._explanation = explanation or {
            "verdict": "VERIFIED",
            "evidence_ids": ["missing-id"],
            "explanation": "model assertion without authority",
            "limitations": [],
        }
        self._rewritten = rewritten
        self.fail = fail
        self.extract_payloads: list[str] = []
        self.verify_calls = 0

    def extract_claims(self, response_text: str) -> list[dict[str, Any]]:
        self.extract_payloads.append(response_text)
        if self.fail:
            raise ProviderError("fake provider failed")
        if callable(self._claims):
            return list(self._claims(response_text))
        return list(self._claims)

    def verify_claim_evidence(
        self, claim: dict[str, Any], evidence: list[dict[str, Any]]
    ) -> dict[str, Any]:
        self.verify_calls += 1
        if self.fail:
            raise ProviderError("fake provider failed")
        return dict(self._explanation)

    def rewrite_response(self, response_text: str, assessments: list[dict[str, Any]]) -> str:
        if self.fail:
            raise ProviderError("fake provider failed")
        if self._rewritten is not None:
            return self._rewritten
        return response_text
