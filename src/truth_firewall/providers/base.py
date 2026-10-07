"""Provider interface. Implementations must not be treated as evidence."""

from __future__ import annotations

from typing import Any, Protocol


class LLMProvider(Protocol):
    def extract_claims(self, response_text: str) -> list[dict[str, Any]]:
        """Return claim dicts. They are interpretations, not proof."""

    def verify_claim_evidence(
        self, claim: dict[str, Any], evidence: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Explain support. The caller keeps the deterministic verdict."""

    def rewrite_response(self, response_text: str, assessments: list[dict[str, Any]]) -> str:
        """Optional rewrite. The default pipeline does not use this for delivery."""
