"""Project errors. User-facing messages stay free of secrets and stack noise."""


class TruthFirewallError(Exception):
    """Base error for expected failures."""


class ProviderError(TruthFirewallError):
    """The language-model provider failed or returned unusable output."""


class ProviderNotConfigured(ProviderError):
    """No provider credentials are configured."""


class ExtractorError(TruthFirewallError):
    """Claim extraction failed."""


class EvidenceError(TruthFirewallError):
    """Evidence could not be loaded or recorded."""


class UnsafePathError(EvidenceError):
    """A path escaped the allowed root or was otherwise unsafe to read."""


class CollectorError(TruthFirewallError):
    """A collector refused or failed an observation."""
