"""Environment configuration. Never prints secret values."""

from __future__ import annotations

import os

from truth_firewall.errors import ProviderError, ProviderNotConfigured
from truth_firewall.providers.base import LLMProvider
from truth_firewall.providers.openai_compatible import OpenAICompatibleProvider


def provider_status() -> dict[str, str]:
    name = os.environ.get("TRUTH_FIREWALL_PROVIDER", "").strip().lower() or "unset"
    key_configured = bool(os.environ.get("TRUTH_FIREWALL_API_KEY") or os.environ.get("OPENAI_API_KEY"))
    return {
        "provider": name,
        "api_key": "configured" if key_configured else "missing",
        "model": os.environ.get("TRUTH_FIREWALL_MODEL") or "gpt-4o-mini",
        "base_url": os.environ.get("TRUTH_FIREWALL_BASE_URL")
        or os.environ.get("OPENAI_BASE_URL")
        or "https://api.openai.com/v1",
    }


def load_provider_from_env() -> LLMProvider | None:
    name = os.environ.get("TRUTH_FIREWALL_PROVIDER", "").strip().lower()
    if name in {"", "none", "offline"}:
        return None
    if name in {"openai", "openai_compatible"}:
        return OpenAICompatibleProvider.from_env()
    raise ProviderError(f"unknown TRUTH_FIREWALL_PROVIDER value: {name}")


def provider_is_configured() -> bool:
    try:
        return load_provider_from_env() is not None
    except ProviderNotConfigured:
        return False
    except ProviderError:
        return False
