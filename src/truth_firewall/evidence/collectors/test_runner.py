"""Test observations. Trusted pytest results use controlled structured accounting."""

from __future__ import annotations

import re
import shlex
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from truth_firewall.evidence.collectors.process import ProcessEvidence
from truth_firewall.evidence.executable_identity import attest_runner, isolate_known_module_argv
from truth_firewall.evidence.execution_environment import execution_environment_fingerprint
from truth_firewall.evidence.provenance import (
    attest_record,
    environment_workspace_snapshot,
    is_attested,
    workspace_snapshot,
)
from truth_firewall.evidence.pytest_result import controlled_arguments, read_result
from truth_firewall.schemas import EvidenceRecord, EvidenceType, TrustLevel, json_dumps

_PASSED = re.compile(r"(\d+)\s+passed\b")
_FAILED = re.compile(r"(\d+)\s+failed\b")
_SKIPPED = re.compile(r"(\d+)\s+skipped\b")
_DESELECTED = re.compile(r"(\d+)\s+deselected\b")
_XFAILED = re.compile(r"(\d+)\s+xfailed\b")
_XPASSED = re.compile(r"(\d+)\s+xpassed\b")
_ERRORS = re.compile(r"(\d+)\s+errors?\b")
_COLLECTED = re.compile(r"(?m)^collected\s+(\d+)\s+items?\b")
_PYTEST_SUMMARY = re.compile(r"(?m)^=+\s*([^\r\n]+?)\s*=+\s*$|^\s*([^\r\n]+?)\s+in\s+\d+(?:\.\d+)?s\s*$")
_UNITTEST_RAN = re.compile(r"Ran\s+(\d+)\s+tests?\b")
_UNITTEST_OK = re.compile(r"(?m)^OK(?:\s|$)")
_UNITTEST_FAIL = re.compile(r"FAILED\s*\(([^)]*)\)")
_PROGRESS_LINE = re.compile(r"(?m)^([.FEsxXpP]+)\s*(?:\[\s*\d+%\])?\s*$")
_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_LINT_OK = re.compile(r"All checks passed!?", re.IGNORECASE)
_LINT_FOUND = re.compile(r"Found\s+(\d+)\s+error", re.IGNORECASE)
_LINT_TOOLS = ("ruff", "flake8", "pylint", "mypy", "eslint")
_PYTHON = re.compile(r"python(?:\d+(?:\.\d+)*)?(?:\.exe)?$", re.I)
_PY_LAUNCHER = re.compile(r"py(?:\.exe)?$", re.I)
_SHELLS = {"cmd", "cmd.exe", "powershell", "powershell.exe", "pwsh", "pwsh.exe", "sh", "bash", "zsh"}
_CODE_SUFFIXES = {
    ".py",
    ".pyi",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".mjs",
    ".cjs",
    ".rs",
    ".go",
    ".java",
    ".cs",
    ".c",
    ".h",
    ".cpp",
    ".hpp",
    ".rb",
    ".php",
    ".swift",
    ".kt",
    ".scala",
}
_TEST_CONFIG_SUFFIXES = {".toml", ".yaml", ".yml", ".json", ".ini", ".cfg", ".conf", ".lock"}
_TEST_CONFIG_NAMES = {
    "pytest.ini",
    "tox.ini",
    "setup.cfg",
    "setup.py",
    "package.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "cargo.toml",
    "cargo.lock",
    "uv.lock",
    "poetry.lock",
    "go.mod",
    "go.sum",
    "go.work",
    "go.work.sum",
}
_PYTEST_SIMPLE_OPTIONS = {"-q", "-v", "-x", "-s", "--capture=no", "--disable-warnings", "--tb=short", "--tb=long"}


@dataclass(frozen=True)
class TestInvocation:
    framework: str
    scope: str
    targets: tuple[str, ...] = ()


def recognize_test_invocation(command: str | None) -> TestInvocation | None:
    """Recognize direct test-runner argv conservatively; shell wrappers are not proof."""
    if not command or re.search(r"[;&|<>`\r\n]", command):
        return None
    try:
        argv = [part.strip("\"'") for part in shlex.split(command, posix=False)]
    except ValueError:
        return None
    if not argv:
        return None
    executable = re.split(r"[\\/]", argv[0])[-1].lower()
    if executable in _SHELLS or executable == "env":
        return None

    framework = ""
    args: list[str] = []
    if executable in {"pytest", "pytest.exe"}:
        framework, args = "pytest", argv[1:]
    elif _PYTHON.fullmatch(executable) or _PY_LAUNCHER.fullmatch(executable):
        module_index = 2 if len(argv) > 2 and argv[1] == "-I" else 1
        if len(argv) <= module_index + 1 or argv[module_index].lower() != "-m":
            return None
        module = argv[module_index + 1].lower()
        if module not in {"pytest", "unittest"}:
            return None
        framework, args = module, argv[module_index + 2 :]
    elif executable == "cargo" and len(argv) > 1 and argv[1].lower() == "test":
        framework, args = "cargo", argv[2:]
    elif executable == "go" and len(argv) > 1 and argv[1].lower() == "test":
        framework, args = "go", argv[2:]
    elif executable in {"npm", "pnpm", "yarn"} and len(argv) > 1 and argv[1].lower() == "test":
        # The script behind a package-manager alias is unknown, so it cannot establish full-suite scope.
        return TestInvocation(framework=executable, scope="unknown", targets=tuple(argv[2:]))
    else:
        return None

    if any(token in {"--help", "-h", "--version", "-V", "--collect-only"} for token in args):
        return TestInvocation(framework=framework, scope="unknown")
    if framework == "pytest":
        selectors = []
        index = 0
        value_options = {"-c", "--confcutdir", "--rootdir", "--override-ini", "-o", "--ignore", "--ignore-glob"}
        while index < len(args):
            token = args[index]
            if token in {"-k", "-m"} or "::" in token:
                selectors.append(token)
                if token in {"-k", "-m"} and index + 1 < len(args):
                    selectors.append(args[index + 1])
                    index += 1
            elif token in value_options:
                selectors.append(token)
                if index + 1 < len(args):
                    selectors.append(args[index + 1])
                    index += 1
            elif token.startswith("-"):
                if token.startswith(("--ignore=", "--rootdir=", "--confcutdir=", "--override-ini=")):
                    selectors.append(token)
                elif token not in _PYTEST_SIMPLE_OPTIONS:
                    selectors.append(token)
            else:
                selectors.append(token)
            index += 1
        targets = tuple(item for item in selectors if not item.startswith("-") and "::" not in item)
        return TestInvocation(framework, "scoped" if selectors else "full", targets)

    if framework == "unittest":
        scoped = any(not token.startswith("-") or token == "-k" for token in args)
        targets = tuple(token for token in args if not token.startswith("-"))
        return TestInvocation(framework, "scoped" if scoped else "full", targets)

    if framework == "cargo":
        scoped = bool(args)
        return TestInvocation(framework, "scoped" if scoped else "full", tuple(args))
    if framework == "go":
        targets = tuple(token for token in args if not token.startswith("-"))
        full = not targets or targets == ("./...",)
        return TestInvocation(framework, "full" if full else "scoped", targets)
    return TestInvocation(framework, "unknown")


def recognized_test_execution(record: EvidenceRecord) -> TestInvocation | None:
    """Return runner identity only for direct executions recorded by a trusted collector."""
    if record.tool_attestation is not True:
        return None
    is_explicit = record.provenance in {"test:explicit-run", "process:explicit-run"}
    is_hook = record.provenance.startswith(("codex_hook:", "cursor_hook:"))
    is_hook_process = is_hook and record.evidence_type in {"process", "test"}
    if not is_explicit and not is_hook_process:
        return None
    payload = record.structured_payload()
    logical_command = payload.get("logical_command") if payload.get("controlled_pytest") is True else record.command
    invocation = recognize_test_invocation(logical_command)
    if invocation is None or invocation.scope == "unknown":
        return None
    if payload.get("trusted_result") is True and payload.get("result_semantics") == "isolated_pytest_junit":
        requested = payload.get("requested_scope")
        if requested not in {"full", "scoped"}:
            return None
        targets = payload.get("requested_targets")
        if not isinstance(targets, list) or not all(isinstance(item, str) for item in targets):
            return None
        invocation = TestInvocation("pytest", requested, tuple(targets))
    identity = payload.get("runner_identity")
    if not isinstance(identity, dict) or identity.get("trusted") is not True:
        return None
    if identity.get("framework") != invocation.framework:
        return None
    if record.evidence_type == "test":
        hook_test = is_hook and payload.get("kind") == "test_run"
        if not (record.provenance.startswith("test:") and payload.get("kind") == "test_run") and not hook_test:
            return None
    elif record.evidence_type == "process":
        expected_kind = payload.get("kind") == "process" or (
            is_hook_process
            and payload.get("kind") == "codex_tool_result"
            and payload.get("tool_result_shape") == "opaque"
        )
        if not (record.provenance.startswith("process:") or is_hook_process) or not expected_kind:
            return None
    else:
        return None
    return invocation


def _is_test_result_provenance(record: EvidenceRecord) -> bool:
    payload = record.structured_payload()
    if (
        record.provenance.startswith(("codex_hook:", "cursor_hook:"))
        and payload.get("kind") == "test_run"
    ):
        return True
    identity = payload.get("runner_identity")
    return (
        record.tool_attestation is True
        and record.provenance.startswith("test:")
        and isinstance(identity, dict)
        and identity.get("trusted") is True
        and identity.get("framework") == payload.get("framework")
    )


def is_atomic_test_result(text: str) -> bool:
    """Accept only a single test outcome proposition, never a sentence containing one."""
    value = re.sub(r"\s+", " ", text).strip().rstrip(".!?").strip()
    path = r"[A-Za-z0-9_./\\:-]+"
    counted = re.fullmatch(
        rf"(?:(?:all|the)\s+)?(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)?\s*"
        rf"tests?\s+(?:(?:in|under|within)\s+{path}\s+)?(?:passed|pass|passes|failed|fail|fails|failing)",
        value,
        re.I,
    )
    suite = re.fullmatch(
        r"(?:(?:all|the)\s+)?test\s+suite\s+(?:passed|pass|passes|failed|fail|fails|failing)",
        value,
        re.I,
    )
    selected = re.fullmatch(
        r"the\s+selected\s+tests?\s+(?:finished|completed|ran)\s+with\s+\d+\s+passed",
        value,
        re.I,
    )
    return counted is not None or suite is not None or selected is not None


def parse_test_stdout(stdout: str, command: str | None = None) -> dict[str, object]:
    """Parse terminal output. Missing numbers stay missing; exit code is not a count."""
    text = _ANSI.sub("", stdout or "")
    summary: dict[str, object] = {"kind": "test_run"}
    invocation = recognize_test_invocation(command)
    if invocation and invocation.framework == "pytest":
        final_line = text.strip().splitlines()[-1] if text.strip() else ""
        summary_match = _PYTEST_SUMMARY.fullmatch(final_line)
        summary_line = " ".join(part for part in summary_match.groups() if part) if summary_match else ""
        passed = _last_int(_PASSED, summary_line)
        failed = _last_int(_FAILED, summary_line)
        skipped = _last_int(_SKIPPED, summary_line)
        deselected = _last_int(_DESELECTED, summary_line)
        xfailed = _last_int(_XFAILED, summary_line)
        xpassed = _last_int(_XPASSED, summary_line)
        errors = _last_int(_ERRORS, summary_line)
        collected = _last_int(_COLLECTED, text)
        if any(value is not None for value in (passed, failed, skipped, deselected, xfailed, xpassed, errors)):
            summary["framework"] = "pytest"
            summary["parser_name"] = "pytest-summary"
            summary["parse_confidence"] = "high"
            _put_counts(summary, passed=passed, failed=failed, skipped=skipped, deselected=deselected,
                        xfailed=xfailed, xpassed=xpassed, errors=errors, collected=collected)
        else:
            progress = _parse_pytest_progress(text, command)
            if progress:
                summary.update(progress)
    elif invocation and invocation.framework == "unittest":
        unit = _parse_unittest(text)
        if unit:
            summary.update(unit)
    lint = _parse_lint(text, command)
    if lint:
        if "parser_name" in summary and "parser_name" in lint:
            lint = dict(lint)
            lint["lint_parser_name"] = lint.pop("parser_name")
        summary.update(lint)
    if "passed" in summary or "failed" in summary:
        failed_count = summary.get("failed")
        error_count = summary.get("errors")
        summary["success"] = bool(
            failed_count in (0, None)
            and error_count in (0, None)
            and (failed_count == 0 or isinstance(summary.get("passed"), int))
        )
        if summary.get("unittest_ok") is True:
            summary["success"] = True
        if isinstance(failed_count, int) and failed_count > 0:
            summary["success"] = False
        if isinstance(error_count, int) and error_count > 0:
            summary["success"] = False
    return summary


def _parse_pytest_progress(text: str, command: str | None) -> dict[str, object] | None:
    if not _invokes_tool(command, "pytest"):
        return None
    lines = _PROGRESS_LINE.findall(text)
    if not lines:
        return None
    glyphs = "".join(lines)
    if not glyphs:
        return None
    passed = sum(1 for char in glyphs if char in ".pP")
    failed = sum(1 for char in glyphs if char == "F")
    errors = sum(1 for char in glyphs if char == "E")
    skipped = sum(1 for char in glyphs if char == "s")
    xfailed = sum(1 for char in glyphs if char == "x")
    parsed: dict[str, object] = {
        "framework": "pytest",
        "parser_name": "pytest-progress",
        "parse_confidence": "high",
        "passed": passed,
        "failed": failed,
    }
    if errors:
        parsed["errors"] = errors
    if skipped:
        parsed["skipped"] = skipped
    if xfailed:
        parsed["xfailed"] = xfailed
    return parsed


def _parse_unittest(text: str) -> dict[str, object] | None:
    ran = _UNITTEST_RAN.search(text)
    ok = _UNITTEST_OK.search(text) is not None
    failed_block = _UNITTEST_FAIL.search(text)
    if not ran and not ok and not failed_block:
        return None
    parsed: dict[str, object] = {
        "framework": "unittest",
        "parser_name": "unittest-summary",
        "parse_confidence": "high",
    }
    if ran:
        parsed["collected"] = int(ran.group(1))
    if ok:
        parsed["unittest_ok"] = True
        parsed["failed"] = 0
        parsed["success"] = True
        if "collected" in parsed:
            skipped = _named_count(text, "skipped") or 0
            xfailed = _named_count(text, "expected failures") or 0
            parsed["skipped"] = skipped
            parsed["xfailed"] = xfailed
            parsed["passed"] = max(0, parsed["collected"] - skipped - xfailed)
    if failed_block:
        details = failed_block.group(1)
        failures = _named_count(details, "failures")
        errors = _named_count(details, "errors")
        parsed["unittest_ok"] = False
        parsed["success"] = False
        if failures is not None:
            parsed["failed"] = failures
        if errors is not None:
            parsed["errors"] = errors
    return parsed


def _parse_lint(text: str, command: str | None) -> dict[str, object] | None:
    if not command:
        return None
    tool = next((name for name in _LINT_TOOLS if _invokes_tool(command, name)), None)
    if tool is None:
        return None
    found = _LINT_FOUND.search(text)
    clean = _LINT_OK.search(text) is not None
    parsed: dict[str, object] = {"lint_tool": tool, "parser_name": f"{tool}-output"}
    if found:
        parsed["lint_violations"] = int(found.group(1))
        parsed["lint_success"] = int(found.group(1)) == 0
        parsed["parse_confidence"] = "high"
    elif clean:
        parsed["lint_violations"] = 0
        parsed["lint_success"] = True
        parsed["parse_confidence"] = "high"
    else:
        parsed["parse_confidence"] = "low"
    return parsed


def _invokes_tool(command: str | None, tool: str) -> bool:
    if not command:
        return False
    first = command.strip().split(" ", 1)[0].lower()
    if first in {"echo", "type", "printf", "write-output"}:
        return False
    return re.search(rf"(?:^|[\\/\s]){re.escape(tool)}(?:\.exe)?\b", command, re.IGNORECASE) is not None


def is_code_edit(record: EvidenceRecord) -> bool:
    payload = record.structured_payload()
    edit_types = {"created", "edited", "deleted", "modified"}
    if payload.get("kind") != "file_edit" and payload.get("change_type") not in edit_types:
        return False
    path = record.file_path or payload.get("file_path") or payload.get("path")
    if not isinstance(path, str):
        return False
    normalized = path.replace("\\", "/")
    name = normalized.rsplit("/", 1)[-1].lower()
    return (
        Path(name).suffix.lower() in _CODE_SUFFIXES | _TEST_CONFIG_SUFFIXES
        or name in _TEST_CONFIG_NAMES
        or (name.startswith("requirements") and name.endswith(".txt"))
        or name == ".env"
        or name.startswith(".env.")
    )


def _named_count(text: str, name: str) -> int | None:
    match = re.search(rf"{name}\s*=\s*(\d+)", text)
    if not match:
        return None
    return int(match.group(1))


def _put_counts(summary: dict[str, object], **counts: int | None) -> None:
    for key, value in counts.items():
        if value is not None:
            summary[key] = value


def _last_int(pattern: re.Pattern[str], text: str) -> int | None:
    matches = pattern.findall(text)
    if not matches:
        return None
    return int(matches[-1])


class TestEvidence:
    def record(
        self,
        *,
        command: str,
        cwd: str | None,
        exit_code: int | None,
        stdout: str,
        stderr: str = "",
        framework: str | None = None,
        trust_level: str = TrustLevel.A.value,
        evidence_id: str = "test-1",
        provenance: str = "test-record",
        source: str = "test",
        timestamp: str | None = None,
    ) -> EvidenceRecord:
        process = ProcessEvidence().record(
            command=command,
            cwd=cwd,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            source=source,
            trust_level=trust_level,
            evidence_id=evidence_id,
            provenance=provenance,
        )
        summary = parse_test_stdout(process.stdout, command=process.command)
        if framework and "framework" not in summary:
            summary["framework"] = framework
        payload = process.structured_payload()
        payload.update(summary)
        record = EvidenceRecord(
            evidence_id=process.evidence_id,
            evidence_type=EvidenceType.TEST.value,
            trust_level=process.trust_level,
            source=process.source,
            timestamp=timestamp or process.timestamp,
            command=process.command,
            cwd=process.cwd,
            exit_code=process.exit_code,
            stdout=process.stdout,
            stderr=process.stderr,
            file_path=None,
            file_hash=None,
            before_hash=None,
            after_hash=None,
            git_metadata_json=process.git_metadata_json,
            payload_json=json_dumps(payload),
            provenance=process.provenance,
            tool_attestation=process.tool_attestation,
        )
        return record

    def run(
        self,
        args: list[str],
        cwd: Path,
        *,
        explicit: bool,
        evidence_id: str = "test-run",
        workspace_root: Path | None = None,
    ) -> EvidenceRecord:
        # Preserve the first-party execution attestation produced by ProcessEvidence.run().
        # Re-recording the process would discard the in-memory attestation and runner identity.
        requested_command = subprocess.list2cmdline(args)
        binding_root = (workspace_root or cwd).resolve(strict=True)
        try:
            cwd.resolve(strict=True).relative_to(binding_root)
        except (OSError, RuntimeError, ValueError) as exc:
            raise ValueError("test working directory is outside its bound workspace") from exc
        requested = recognize_test_invocation(requested_command)
        trusted_result = None
        controlled_execution = False
        controlled_identity = None
        environment_binding = None
        environment_workspace_state = None
        logical_argv = None
        if requested and requested.framework == "pytest" and requested.scope != "unknown":
            with tempfile.TemporaryDirectory(prefix="tf-pytest-") as temporary:
                controlled = controlled_arguments(args, Path(temporary), cwd)
                if controlled is not None:
                    command, environment = controlled
                    logical_argv = isolate_known_module_argv(args)
                    identity_before = attest_runner(logical_argv, cwd)
                    state_before = workspace_snapshot(binding_root)
                    environment_state_before = environment_workspace_snapshot(binding_root)
                    environment_binding_before = execution_environment_fingerprint(environment, identity_before)
                    process = ProcessEvidence().run(command, cwd, explicit=explicit,
                                                    evidence_id=evidence_id, env=environment,
                                                    workspace_root=binding_root)
                    identity_after = attest_runner(logical_argv, cwd)
                    state_after = workspace_snapshot(binding_root)
                    environment_state_after = environment_workspace_snapshot(binding_root)
                    environment_binding_after = execution_environment_fingerprint(environment, identity_after)
                    controlled_execution = (identity_before is not None and identity_before == identity_after
                                            and state_before is not None and state_before == state_after
                                            and environment_state_before is not None
                                            and environment_state_before == environment_state_after
                                            and environment_binding_before is not None
                                            and environment_binding_before == environment_binding_after)
                    controlled_identity = identity_before if controlled_execution else None
                    environment_binding = environment_binding_after if controlled_execution else None
                    environment_workspace_state = environment_state_after if controlled_execution else None
                    trusted_result = read_result(Path(temporary) / "result.xml")
                else:
                    process = ProcessEvidence().run(args, cwd, explicit=explicit, evidence_id=evidence_id,
                                                    workspace_root=binding_root)
        else:
            process = ProcessEvidence().run(args, cwd, explicit=explicit, evidence_id=evidence_id,
                                            workspace_root=binding_root)
        summary = parse_test_stdout(process.stdout,
                                    command=requested_command if controlled_execution else process.command)
        payload = process.structured_payload()
        payload.update(summary)
        if controlled_execution:
            payload["runner_identity"] = controlled_identity
            payload["workspace_snapshot"] = state_after
            payload["logical_command"] = requested_command
            payload["controlled_pytest"] = True
            payload["runner_argv"] = logical_argv
            payload["execution_environment_fingerprint"] = environment_binding
            payload["environment_workspace_snapshot"] = environment_workspace_state
        if trusted_result is not None and controlled_execution:
            # A zero exit code or printable summary alone never establishes result
            # semantics. The isolated runner's machine-readable accounting must
            # also agree with the process outcome.
            clean = trusted_result["failed"] == trusted_result["errors"] == 0
            conflict = any(type(summary.get(key)) is int and summary[key] != trusted_result[key]
                           for key in trusted_result if key in summary)
            payload["trusted_result"] = (process.exit_code == 0) == clean and not conflict
            payload["result_semantics"] = "isolated_pytest_junit"
            trusted_result["selected"] = trusted_result["collected"]
            if requested.scope == "full":
                trusted_result["deselected"] = 0
            payload["result_counts"] = trusted_result
            payload["requested_scope"] = requested.scope
            payload["requested_targets"] = list(requested.targets)
        result = EvidenceRecord(
            evidence_id=process.evidence_id,
            evidence_type=EvidenceType.TEST.value,
            trust_level=process.trust_level,
            source="test",
            timestamp=process.timestamp,
            command=process.command,
            cwd=process.cwd,
            exit_code=process.exit_code,
            stdout=process.stdout,
            stderr=process.stderr,
            file_path=None,
            file_hash=None,
            before_hash=None,
            after_hash=None,
            git_metadata_json=process.git_metadata_json,
            payload_json=json_dumps(payload),
            provenance="test:explicit-run",
            workspace_root=process.workspace_root,
            sequence=process.sequence,
            tool_attestation=process.tool_attestation or controlled_execution,
        )
        return attest_record(result) if is_attested(process) or controlled_execution else result
