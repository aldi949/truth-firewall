import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from truth_firewall.evidence.provenance import attest_record
from truth_firewall.schemas import evidence_from_dict

NOW = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)
_TEST_ROOT_BINDING_CACHE = {}


@pytest.fixture
def now():
    return NOW


def evidence(**overrides):
    raw = {
        "evidence_type": "test",
        "trust_level": "A",
        "source": "unit",
        "timestamp": (NOW - timedelta(minutes=1)).isoformat(),
        "command": "pytest",
        "cwd": ".",
        "workspace_root": str(Path.cwd().resolve()),
        "exit_code": 0,
        "stdout": "6 passed in 0.01s",
        "stderr": "",
        "provenance": "test:explicit-run",
        "structured_payload": {"kind": "test_run", "passed": 6, "failed": 0, "framework": "pytest"},
    }
    raw.update(overrides)
    if isinstance(raw.get("structured_payload"), dict) and raw["structured_payload"].get("kind") == "search":
        raw["structured_payload"] = {**raw["structured_payload"], "complete": True}
    # This helper models records already captured by the first-party collector.
    # It intentionally does not attest path-qualified/local lookalike executables.
    command = raw.get("command")
    if isinstance(command, str) and command.strip() and raw.get("provenance") == "test:explicit-run":
        first = command.strip().split()[0].strip("\"'").replace("\\", "/").lower()
        parts = command.split()
        module = (
            parts[2]
            if first in {"python", "python.exe", "python3"} and len(parts) > 2 and parts[1] == "-m"
            else ""
        )
        framework = module if module in {"pytest", "unittest"} else (
            "ruff" if first == "ruff" else "pytest" if first in {"pytest", "pytest.exe"} else None
        )
        if framework:
            payload = raw.get("structured_payload")
            if isinstance(payload, dict):
                payload = dict(payload)
                payload.setdefault("framework", framework)
                payload["runner_identity"] = {
                    "trusted": True,
                    "framework": framework,
                    "invocation": "first-party-test-fixture",
                }
                raw["structured_payload"] = payload
    evidence_id = raw.pop("evidence_id", "e-001")
    record = evidence_from_dict(raw, evidence_id=evidence_id)
    # Fixtures represent observations made inside the trusted test host. An
    # evidence_from_dict record used by production remains imported and untrusted.
    from dataclasses import replace

    from truth_firewall.schemas import json_dumps

    payload = record.structured_payload()
    if payload.get("kind") == "test_run" and isinstance(payload.get("runner_identity"), dict):
        from truth_firewall.evidence.collectors.test_runner import parse_test_stdout
        from truth_firewall.evidence.executable_identity import attest_runner
        from truth_firewall.evidence.execution_environment import (
            controlled_test_environment,
            execution_environment_fingerprint,
        )
        from truth_firewall.evidence.provenance import environment_workspace_snapshot, workspace_snapshot

        parsed = parse_test_stdout(record.stdout, command=record.command)
        coordinates = ("passed", "failed", "skipped", "xfailed", "xpassed", "errors", "deselected", "collected")
        consistent = not any(key in parsed and key in payload and parsed[key] != payload[key]
                             for key in coordinates)
        payload["trusted_result"] = consistent
        payload["result_counts"] = {key: payload.get(key, parsed.get(key)) for key in coordinates
                                    if type(payload.get(key, parsed.get(key))) is int}
        runner_argv = [sys.executable, "-I", "-m", "pytest"]
        runner_identity = attest_runner(runner_argv, Path(record.workspace_root or Path.cwd()))
        if runner_identity is not None:
            environment = controlled_test_environment()
            payload["controlled_pytest"] = True
            payload["logical_command"] = record.command
            payload["runner_argv"] = runner_argv
            payload["runner_identity"] = runner_identity
            payload["execution_environment_fingerprint"] = execution_environment_fingerprint(environment,
                                                                                                runner_identity)
            binding_root = Path(record.workspace_root or Path.cwd())
            if binding_root.resolve() == Path.cwd().resolve():
                if "workspace" not in _TEST_ROOT_BINDING_CACHE:
                    _TEST_ROOT_BINDING_CACHE["workspace"] = (
                        environment_workspace_snapshot(binding_root),
                        workspace_snapshot(binding_root),
                    )
                env_workspace, source_workspace = _TEST_ROOT_BINDING_CACHE["workspace"]
            else:
                env_workspace = environment_workspace_snapshot(binding_root)
                source_workspace = workspace_snapshot(binding_root)
            payload["environment_workspace_snapshot"] = env_workspace
            payload["workspace_snapshot"] = source_workspace
    if payload and "value" not in payload:
        payload["state_binding"] = "event"
    record = replace(record, trust_level=str(raw.get("trust_level", "A")), payload_json=json_dumps(payload))
    if raw.get("provenance") == "test:explicit-run" and isinstance(raw.get("structured_payload"), dict):
        identity = raw["structured_payload"].get("runner_identity")
        if isinstance(identity, dict) and identity.get("trusted") is True:
            from dataclasses import replace

            record = replace(record, tool_attestation=True)
    return attest_record(record)
