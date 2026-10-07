"""Canonical verdict and response policy."""

from truth_firewall.policy.engine import decide, is_enforced, requires_correction

__all__ = ["decide", "is_enforced", "requires_correction"]
