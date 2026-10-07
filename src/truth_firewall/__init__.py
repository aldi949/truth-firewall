"""Truth Firewall: unsupported claims are not presented as verified."""

__version__ = "0.1.0"

from truth_firewall.pipeline import CheckResult, check_response

__all__ = ["CheckResult", "check_response", "__version__"]
