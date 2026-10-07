"""Bounded, contract-driven completion for local Python repository tasks.

The contract and verifier are trusted host inputs. Repository code is untrusted and
is executed only for explicitly requested black-box cases (without a sandbox).
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
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
class TaskCompletionContract:
    task_id: str
    original_task: str
    conditions: tuple[AcceptanceCondition, ...]

    def __post_init__(self) -> None:
        if not self.task_id or not self.original_task or not self.conditions:
            raise ValueError("task identity, original task, and conditions are required")
        ids = [condition.condition_id for condition in self.conditions]
        if any(not value for value in ids) or len(ids) != len(set(ids)):
            raise ValueError("condition IDs must be nonempty and unique")
        if not any(condition.mandatory for condition in self.conditions):
            raise ValueError("at least one mandatory condition is required")


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
spec.loader.exec_module(module)
target = getattr(module, request['symbol'])
result = target(*request['args'])
sys.stdout.write(json.dumps(result, allow_nan=False))
"""


class TaskCompletionVerifier:
    """Reobserve every condition on every DONE attempt; never import claim verdicts."""

    def __init__(self, root: Path, contract: TaskCompletionContract) -> None:
        self.root = root.resolve(strict=True)
        self.contract = contract

    def attempt_done(self, worker_response: str) -> CompletionAttempt:
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
                    if not isinstance(condition.expected, list) or not all(isinstance(x, str) for x in condition.expected):
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
                if type(actual) is not type(expected) or actual != expected:
                    return ConditionVerdict.FAILED, f"protected case {index} output differs from expected"
            except (subprocess.TimeoutExpired, OSError, ValueError, UnicodeError):
                return ConditionVerdict.UNKNOWN, f"protected case {index} could not be evaluated"
        return ConditionVerdict.VERIFIED, f"{len(condition.cases)} protected cases matched"
