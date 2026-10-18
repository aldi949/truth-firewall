"""Truth Firewall: unsupported claims are not presented as verified."""

__version__ = "0.2.0rc1"

from truth_firewall.pipeline import CheckResult, check_response

__all__ = ["CheckResult", "check_response", "__version__"]
