"""Bounded, contract-driven completion for local Python repository tasks.

The contract and verifier are trusted host inputs. Repository code is untrusted and
is executed only for explicitly requested black-box cases (without a sandbox).
"""

from __future__ import annotations

import ast
import hashlib
import json
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from truth_firewall.evidence.provenance import workspace_snapshot
from truth_firewall.safety import resolve_under_root


class ConditionVerdict(str, Enum):
    VERIFIED = "VERIFIED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


class CompletionDecision(str, Enum):
    VERIFIED_DONE = "VERIFIED_DONE"
    REJECT_DONE = "REJECT_DONE"
    HUMAN_REQUIRED = "HUMAN_REQUIRED"


def acceptance_content_digest(data: dict[str, Any]) -> str:
    """Hash the exact reviewable JSON content, excluding only review metadata."""
    content = {key: value for key, value in data.items()
               if key not in {"acceptance_review", "acceptance_digest"}}
    canonical = json.dumps(content, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AcceptanceCondition:
    condition_id: str
    kind: str
    path: str = ""
    symbol: str = ""
    expected: Any = None
    cases: tuple[tuple[Any, Any], ...] = ()
    mandatory: bool = True


@dataclass(frozen=True)
class CompletionObligation:
    obligation_id: str
    text: str
    scope: str
    condition_ids: tuple[str, ...]
    coverage: str = "unresolved"
    target_path: str = ""
    mandatory: bool = True


@dataclass(frozen=True)
class TaskCompletionContract:
    task_id: str
    original_task: str
    conditions: tuple[AcceptanceCondition, ...]
    acceptance_review: str = "legacy"
    acceptance_digest: str | None = None
    current_content_digest: str | None = None
    obligations: tuple[CompletionObligation, ...] = ()

    def __post_init__(self) -> None:
        if not self.task_id or not self.original_task or not self.conditions:
            raise ValueError("task identity, original task, and conditions are required")
        ids = [condition.condition_id for condition in self.conditions]
        if any(not value for value in ids) or len(ids) != len(set(ids)):
            raise ValueError("condition IDs must be nonempty and unique")
        if not any(condition.mandatory for condition in self.conditions):
            raise ValueError("at least one mandatory condition is required")
        if self.acceptance_review not in {"legacy", "unreviewed", "reviewed"}:
            raise ValueError("acceptance_review must be legacy, unreviewed, or reviewed")
        obligation_ids = [item.obligation_id for item in self.obligations]
        if (any(not key for key in obligation_ids) or len(set(obligation_ids)) != len(obligation_ids)
                or any(len(set(item.condition_ids)) != len(item.condition_ids) for item in self.obligations)):
            raise ValueError("obligation and evidence binding IDs must be unique and nonempty")

    def readiness_gaps(self) -> tuple[str, ...]:
        gaps = []
        if self.acceptance_review == "unreviewed":
            gaps.append("starter acceptance checks have not been explicitly reviewed and activated")
        elif self.acceptance_review == "reviewed" and (
                not isinstance(self.acceptance_digest, str)
                or self.acceptance_digest != self.current_content_digest):
            gaps.append("acceptance review is invalid because the task or checks changed; review and activate again")
        if any("EDIT_ME" in value.upper() for condition in self.conditions
               for value in (condition.condition_id, condition.path, condition.symbol)):
            gaps.append("replace every EDIT_ME acceptance-check placeholder")
        check_map = {item.condition_id: item for item in self.conditions}
        supported = {"file_state": {"file_exists", "file_changed"},
                     "signature": {"python_function"}, "examples": {"black_box"},
                     "test_suite": {"unittest"}}
        if self.obligations and not any(item.mandatory for item in self.obligations):
            gaps.append("explicit obligation inventory has no mandatory obligation")
        for obligation in self.obligations:
            if not obligation.mandatory:
                continue
            bindings = [check_map.get(key) for key in obligation.condition_ids]
            if (not obligation.text.strip() or obligation.coverage != "full"
                    or obligation.scope not in supported or not bindings
                    or any(item is None or not item.mandatory
                           or item.kind not in supported[obligation.scope] for item in bindings)):
                gaps.append(f"obligation {obligation.obligation_id}: scope or evidence coverage is unresolved")
            elif obligation.target_path and any(item.path != obligation.target_path for item in bindings):
                gaps.append(f"obligation {obligation.obligation_id}: evidence targets a different declared path")
        return tuple(gaps)


@dataclass(frozen=True)
class ConditionEvidence:
    condition_id: str
    verdict: ConditionVerdict
    observation: str
    state_seal: str | None
    source: str = "completion-verifier"


@dataclass(frozen=True)
class CompletionAttempt:
    contract: TaskCompletionContract
    worker_response: str
    evidence: tuple[ConditionEvidence, ...]
    decision: CompletionDecision
    state_seal: str | None

    @property
    def unmet_conditions(self) -> tuple[str, ...]:
        return tuple(item.condition_id for item in self.evidence if item.verdict != ConditionVerdict.VERIFIED)

    def is_current(self, root: Path) -> bool:
        return self.state_seal is not None and workspace_snapshot(root) == self.state_seal

    def current_decision(self, root: Path) -> CompletionDecision:
        if not self.is_current(root):
            return CompletionDecision.HUMAN_REQUIRED
        return self.decision


_BLACK_BOX_RUNNER = r"""
import importlib.util, json, sys
from pathlib import Path
request = json.load(sys.stdin)
spec = importlib.util.spec_from_file_location('_completion_target', request['path'])
if spec is None or spec.loader is None:
    raise RuntimeError('cannot load module')
module = importlib.util.module_from_spec(spec)
# Execute the observed source, never a timestamp/size-matching stale .pyc.
exec(compile(Path(request['path']).read_bytes(), request['path'], 'exec'), module.__dict__)
target = getattr(module, request['symbol'])
result = target(*request['args'])
sys.stdout.write(json.dumps(result, allow_nan=False))
"""


_UNITTEST_RUNNER = r"""
import contextlib, io, json, sys, types, unittest
from pathlib import Path
request = json.load(sys.stdin)
sys.path.insert(0, str(Path(request['path']).parent))
sys.path.insert(0, request['root'])
module = types.ModuleType('_protected_suite')
module.__file__ = request['path']
sys.modules[module.__name__] = module
with contextlib.redirect_stdout(io.StringIO()):
    exec(compile(Path(request['path']).read_bytes(), request['path'], 'exec'), module.__dict__)
    suite = unittest.defaultTestLoader.loadTestsFromModule(module)
    def ids(item):
        if isinstance(item, unittest.TestSuite):
            return [name for child in item for name in ids(child)]
        return [item.id().removeprefix('_protected_suite.')]
    selected = ids(suite)
    result = unittest.TestResult()
    suite.run(result)
print(json.dumps({'ids': sorted(selected), 'run': result.testsRun,
                  'failures': len(result.failures), 'errors': len(result.errors),
                  'skipped': len(result.skipped), 'expected_failures': len(result.expectedFailures),
                  'unexpected_successes': len(result.unexpectedSuccesses)}))
"""


def _json_equal(actual: Any, expected: Any) -> bool:
    """JSON equality with exact types at every depth (bool is not int)."""
    if type(actual) is not type(expected):
        return False
    if isinstance(actual, dict):
        return actual.keys() == expected.keys() and all(_json_equal(actual[k], expected[k]) for k in actual)
    if isinstance(actual, list):
        return len(actual) == len(expected) and all(_json_equal(a, b) for a, b in zip(actual, expected))
    return actual == expected


class TaskCompletionVerifier:
    """Reobserve every condition on every DONE attempt; never import claim verdicts."""

    def __init__(self, root: Path, contract: TaskCompletionContract) -> None:
        self.root = root.resolve(strict=True)
        self.contract = contract

    def attempt_done(self, worker_response: str) -> CompletionAttempt:
        if self.contract.readiness_gaps():
            return CompletionAttempt(self.contract, worker_response, (),
                                     CompletionDecision.HUMAN_REQUIRED, None)
        before = workspace_snapshot(self.root)
        observations = [self._observe(condition) for condition in self.contract.conditions]
        after = workspace_snapshot(self.root)
        stable = before is not None and before == after
        evidence = tuple(
            ConditionEvidence(condition.condition_id,
                              verdict if stable else ConditionVerdict.UNKNOWN,
                              note if stable else "workspace state unavailable or changed during verification",
                              after if stable else None)
            for condition, (verdict, note) in zip(self.contract.conditions, observations)
        )
        mandatory = [item for condition, item in zip(self.contract.conditions, evidence) if condition.mandatory]
        if any(item.verdict == ConditionVerdict.UNKNOWN for item in mandatory):
            decision = CompletionDecision.HUMAN_REQUIRED
        elif any(item.verdict == ConditionVerdict.FAILED for item in mandatory):
            decision = CompletionDecision.REJECT_DONE
        else:
            decision = CompletionDecision.VERIFIED_DONE
        return CompletionAttempt(self.contract, worker_response, evidence, decision, after if stable else None)

    def _observe(self, condition: AcceptanceCondition) -> tuple[ConditionVerdict, str]:
        try:
            if condition.kind == "file_changed":
                if not _sha256(condition.expected):
                    return ConditionVerdict.UNKNOWN, "a reviewed baseline SHA-256 is required"
                path = self._path(condition.path)
                changed = path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() != condition.expected
                return (ConditionVerdict.VERIFIED if changed else ConditionVerdict.FAILED,
                        "file content differs from reviewed baseline" if changed else
                        "file is absent or unchanged from reviewed baseline")
            if condition.kind == "unittest":
                return self._unittest(condition)
            if condition.kind == "file_exists":
                if condition.expected is not None and type(condition.expected) is not bool:
                    return ConditionVerdict.UNKNOWN, "unsupported file existence expectation"
                path = self._path(condition.path)
                exists = path.is_file()
                wanted = condition.expected is not False
                return (ConditionVerdict.VERIFIED if exists == wanted else ConditionVerdict.FAILED,
                        f"file existence is {exists}; expected {wanted}")
            if condition.kind == "python_function":
                path = self._path(condition.path)
                if not path.is_file() or path.suffix != ".py" or not condition.symbol.isidentifier():
                    return ConditionVerdict.FAILED, "Python source or named function is absent"
                tree = ast.parse(path.read_text(encoding="utf-8"))
                matches = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                           and node.name == condition.symbol]
                if len(matches) != 1:
                    return ConditionVerdict.FAILED, "exactly one top-level function is required"
                if condition.expected is not None:
                    if not isinstance(condition.expected, list) or not all(
                        isinstance(x, str) for x in condition.expected
                    ):
                        return ConditionVerdict.UNKNOWN, "unsupported signature specification"
                    args = matches[0].args
                    if args.vararg or args.kwarg or args.posonlyargs or args.kwonlyargs or args.defaults:
                        return ConditionVerdict.UNKNOWN, "signature has unsupported parameter forms"
                    names = [arg.arg for arg in args.args]
                    if names != condition.expected:
                        return ConditionVerdict.FAILED, f"parameters {names!r} differ from contract"
                return ConditionVerdict.VERIFIED, "top-level function and requested parameters observed"
            if condition.kind == "black_box":
                return self._black_box(condition)
            return ConditionVerdict.UNKNOWN, f"unsupported condition kind: {condition.kind}"
        except (OSError, ValueError, SyntaxError, UnicodeError) as exc:
            return ConditionVerdict.UNKNOWN, f"observation unavailable: {type(exc).__name__}"

    def _unittest(self, condition: AcceptanceCondition) -> tuple[ConditionVerdict, str]:
        expected = condition.expected
        if (not isinstance(expected, dict) or not _sha256(expected.get("sha256"))
                or not isinstance(expected.get("test_ids"), list) or not expected["test_ids"]
                or not all(isinstance(key, str) and key for key in expected["test_ids"])
                or len(set(expected["test_ids"])) != len(expected["test_ids"])):
            return ConditionVerdict.UNKNOWN, "a reviewed suite hash and exact nonempty test IDs are required"
        path = self._path(condition.path)
        if not path.is_file() or path.suffix != ".py":
            return ConditionVerdict.FAILED, "mandatory unittest suite is absent"
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected["sha256"]:
            return ConditionVerdict.FAILED, "mandatory test source changed from its reviewed hash"
        try:
            # A fresh cache prefix prevents stale bytecode in suite dependencies too.
            with tempfile.TemporaryDirectory(prefix="tf-suite-cache-") as cache:
                result = subprocess.run([sys.executable, "-I", "-B", "-X", f"pycache_prefix={cache}",
                                         "-c", _UNITTEST_RUNNER],
                                        input=json.dumps({"path": str(path), "root": str(self.root)}),
                                        cwd=self.root, capture_output=True, text=True, timeout=15, check=False)
            if result.returncode:
                return ConditionVerdict.UNKNOWN, "mandatory suite could not execute normally"
            observed = json.loads(result.stdout)
            passed = (observed["ids"] == sorted(expected["test_ids"])
                      and observed["run"] == len(expected["test_ids"])
                      and all(observed[key] == 0 for key in (
                          "failures", "errors", "skipped", "expected_failures", "unexpected_successes")))
            return (ConditionVerdict.VERIFIED if passed else ConditionVerdict.FAILED,
                    "mandatory unittest command: " + json.dumps(observed, sort_keys=True))
        except (subprocess.TimeoutExpired, OSError, ValueError, KeyError, TypeError):
            return ConditionVerdict.UNKNOWN, "mandatory suite evidence unavailable or malformed"

    def _path(self, value: str) -> Path:
        excluded = {".git", ".truth-firewall", "build", "dist", "__pycache__", ".pytest_cache",
                    ".ruff_cache", ".venv", "venv"}
        if not value or any(part.lower() in excluded or part.lower().endswith(".egg-info")
                            for part in Path(value).parts):
            raise ValueError("path outside bounded source snapshot")
        return resolve_under_root(self.root, value)

    def _black_box(self, condition: AcceptanceCondition) -> tuple[ConditionVerdict, str]:
        path = self._path(condition.path)
        if not path.is_file() or path.suffix != ".py" or not condition.symbol.isidentifier():
            return ConditionVerdict.FAILED, "black-box target is absent"
        if not condition.cases:
            return ConditionVerdict.UNKNOWN, "protected oracle has no cases"
        for index, (args, expected) in enumerate(condition.cases):
            if not isinstance(args, (tuple, list)):
                return ConditionVerdict.UNKNOWN, f"case {index} arguments are unsupported"
            request = {"path": str(path), "symbol": condition.symbol, "args": args}
            try:
                payload = json.dumps(request, allow_nan=False)
                result = subprocess.run([sys.executable, "-I", "-c", _BLACK_BOX_RUNNER], input=payload,
                                        cwd=self.root, capture_output=True, text=True, timeout=5, check=False)
                if result.returncode != 0:
                    return ConditionVerdict.FAILED, f"protected case {index} raised or exited nonzero"
                actual = json.loads(result.stdout)
                if not _json_equal(actual, expected):
                    return ConditionVerdict.FAILED, f"protected case {index} output differs from expected"
            except (subprocess.TimeoutExpired, OSError, ValueError, UnicodeError):
                return ConditionVerdict.UNKNOWN, f"protected case {index} could not be evaluated"
        return ConditionVerdict.VERIFIED, f"{len(condition.cases)} protected cases matched"


def _sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(char in "0123456789abcdef" for char in value)
