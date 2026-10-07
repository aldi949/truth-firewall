"""Bounded, process-local freshness binding for controlled test executions."""

from __future__ import annotations

import hashlib
import hmac
import importlib.metadata
import json
import os
import platform
import sys
from pathlib import Path

from truth_firewall.evidence.provenance import _KEY

_MAX_ENV_BYTES = 1_000_000
_MAX_DISTRIBUTIONS = 10_000


def controlled_test_environment(environment: dict[str, str] | None = None) -> dict[str, str]:
    values = dict(os.environ if environment is None else environment)
    values["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    values.pop("PYTEST_ADDOPTS", None)
    values.pop("PYTEST_PLUGINS", None)
    return values


def execution_environment_fingerprint(
    environment: dict[str, str],
    runner_identity: dict | None,
) -> str | None:
    """Fingerprint environment inputs without serializing environment values."""
    if not isinstance(environment, dict) or runner_identity is None:
        return None
    try:
        env_items = sorted((str(key), str(value)) for key, value in environment.items())
        encoded_env = json.dumps(env_items, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(encoded_env) > _MAX_ENV_BYTES:
            return None
        executable = Path(sys.executable).resolve(strict=True)
        if str(executable) != runner_identity.get("executable"):
            return None
        executable_digest = runner_identity.get("executable_sha256")
        if not isinstance(executable_digest, str):
            return None
        distributions = _installed_distribution_identity()
        if distributions is None:
            return None
        material = json.dumps(
            {
                "environment": env_items,
                "runner_identity": runner_identity,
                "interpreter": {
                    "path": str(executable),
                    "sha256": executable_digest,
                    "version": sys.version,
                    "implementation": sys.implementation.name,
                    "prefix": str(Path(sys.prefix).resolve()),
                    "base_prefix": str(Path(sys.base_prefix).resolve()),
                    "platform": platform.platform(),
                },
                "installed_distributions": distributions,
            },
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        return hmac.new(_KEY, material, hashlib.sha256).hexdigest()
    except (OSError, RuntimeError, TypeError, ValueError, UnicodeError):
        return None


def _installed_distribution_identity() -> tuple[tuple[str, str], ...] | None:
    """Observe package state for each collection/check, never across operations."""
    try:
        distributions = tuple(sorted(
            (item.metadata.get("Name", "").lower(), item.version)
            for item in importlib.metadata.distributions()
        ))
        return distributions if len(distributions) <= _MAX_DISTRIBUTIONS else None
    except (OSError, RuntimeError, TypeError, ValueError):
        return None
