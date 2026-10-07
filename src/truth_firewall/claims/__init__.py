"""Claim extraction."""

from truth_firewall.claims.deterministic import DeterministicFallbackExtractor
from truth_firewall.claims.semantic import SemanticClaimExtractor

__all__ = ["DeterministicFallbackExtractor", "SemanticClaimExtractor"]
