"""Generic completion authority for a single controller-owned toy-scale session.

The controller keeps the contract, coverage programs, checks, frozen candidate,
and issuance key. This is architectural separation, not an OS sandbox: another
same-user process may still access controller files or interfere with the host.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from uuid import uuid4


class ContractInvalid(ValueError):
    pass


class Verdict(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"
    ERROR = "ERROR"


class Decision(str, Enum):
    VERIFIED_DONE = "VERIFIED_DONE"
    REJECT_DONE = "REJECT_DONE"
    HUMAN_REQUIRED = "HUMAN_REQUIRED"


class CoverageDisposition(str, Enum):
    COVERAGE_AUTHORIZED = "COVERAGE_AUTHORIZED"
    COVERAGE_UNRESOLVED = "COVERAGE_UNRESOLVED"
    COVERAGE_REJECTED = "COVERAGE_REJECTED"


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _file_hash(path: Path) -> str:
    return _digest(path.read_bytes())


@dataclass(frozen=True)
class Obligation:
    obligation_id: str
    description: str
    source_mapping: str
    mandatory: bool
    verification_mode: str  # CHECK or UNSUPPORTED
    required_check_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProtectedCheck:
    check_id: str
    obligation_id: str
    executable: Path
    sha256: str


@dataclass(frozen=True)
class TaskContract:
    contract_id: str
    original_task: str
    obligations: tuple[Obligation, ...]
    checks: tuple[ProtectedCheck, ...]

    def __post_init__(self) -> None:
        if not self.contract_id or not self.original_task or not self.obligations:
            raise ContractInvalid("task identity, original task, and obligations are required")
        ids = [item.obligation_id for item in self.obligations]
        if any(not value for value in ids) or len(set(ids)) != len(ids):
            raise ContractInvalid("obligation IDs must be nonempty and unique")
        if not any(item.mandatory for item in self.obligations):
            raise ContractInvalid("contract has no mandatory obligation")
        check_ids = [item.check_id for item in self.checks]
        if any(not value for value in check_ids) or len(set(check_ids)) != len(check_ids):
            raise ContractInvalid("check IDs must be nonempty and unique")
        check_map = {item.check_id: item for item in self.checks}
        referenced: set[str] = set()
        for item in self.obligations:
            if not item.description or not item.source_mapping or item.source_mapping not in self.original_task:
                raise ContractInvalid(f"unmapped obligation: {item.obligation_id}")
            if item.verification_mode == "UNSUPPORTED":
                if item.required_check_ids:
                    raise ContractInvalid("unsupported obligation has checks")
            elif item.verification_mode == "CHECK":
                if not item.required_check_ids or len(set(item.required_check_ids)) != len(item.required_check_ids):
                    raise ContractInvalid(f"missing/duplicate checks: {item.obligation_id}")
                for check_id in item.required_check_ids:
                    check = check_map.get(check_id)
                    if check is None or check.obligation_id != item.obligation_id:
                        raise ContractInvalid(f"check mapping mismatch: {check_id}")
                    referenced.add(check_id)
            else:
                raise ContractInvalid(f"undeclared verification mode: {item.obligation_id}")
        if referenced != set(check_map):
            raise ContractInvalid("orphan or unregistered check")
        for check in self.checks:
            if not check.executable.is_absolute() or len(check.sha256) != 64:
                raise ContractInvalid(f"invalid check identity: {check.check_id}")
            try:
                int(check.sha256, 16)
            except ValueError as exc:
                raise ContractInvalid(f"invalid check hash: {check.check_id}") from exc

    @property
    def task_hash(self) -> str:
        return _digest(self.original_task.encode("utf-8"))

    @property
    def obligation_map_hash(self) -> str:
        return _digest(_canonical([asdict(item) for item in self.obligations]))

    @property
    def sha256(self) -> str:
        return _digest(_canonical({
            "contract_id": self.contract_id, "original_task": self.original_task,
            "obligations": [asdict(item) for item in self.obligations],
            "checks": [{"check_id": item.check_id, "obligation_id": item.obligation_id,
                        "executable": str(item.executable), "sha256": item.sha256}
                       for item in self.checks],
        }))


@dataclass(frozen=True)
class CoverageProgram:
    identity: str
    role: str  # planner or reviewer
    executable: Path
    sha256: str


@dataclass(frozen=True)
class CoverageAuthorization:
    disposition: CoverageDisposition
    task_hash: str
    obligation_map_hash: str
    contract_hash: str
    planner_identity: str | None
    planner_run_id: str | None
    reviewer_identity: str | None
    reviewer_run_id: str | None
    unresolved_items: tuple[str, ...]
    provenance_hash: str | None


@dataclass(frozen=True)
class FrozenCandidate:
    candidate_hash: str
    directories: tuple[str, ...]
    files: tuple[tuple[str, bytes], ...]


@dataclass(frozen=True)
class CheckResult:
    session_id: str
    execution_id: str
    task_hash: str
    contract_hash: str
    check_id: str
    obligation_id: str
    check_sha256: str
    candidate_hash: str
    verdict: Verdict
    executed_successfully: bool
    observation: str


@dataclass(frozen=True)
class CompletionReceipt:
    """Historical output only. Constructing or copying it grants no authority."""
    session_id: str
    issuance_id: str
    contract_hash: str
    candidate_hash: str | None
    results: tuple[CheckResult, ...]
    decision: Decision
    worker_response: str


@dataclass(frozen=True)
class _IssuedEvidence:
    result: CheckResult
    seal: str


_MAX_FILES = 20_000
_MAX_BYTES = 128 * 1024 * 1024


def _read_tree(root: Path) -> FrozenCandidate:
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("candidate is not a directory")
    directories: list[str] = []
    files: list[tuple[str, bytes]] = []
    total = 0
    for parent, children, names in os.walk(root, followlinks=False):
        children.sort()
        names.sort()
        for name in children:
            path = Path(parent) / name
            if path.is_symlink() or not path.is_dir():
                raise ValueError("candidate link or special directory")
            directories.append(path.relative_to(root).as_posix())
        for name in names:
            path = Path(parent) / name
            if path.is_symlink() or not stat.S_ISREG(path.lstat().st_mode):
                raise ValueError("candidate link or special file")
            before = path.stat()
            data = path.read_bytes()
            after = path.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise ValueError("candidate changed during capture")
            total += len(data)
            if len(files) + len(directories) > _MAX_FILES or total > _MAX_BYTES:
                raise ValueError("candidate exceeds bounded capture")
            files.append((path.relative_to(root).as_posix(), data))
    directories.sort()
    files.sort(key=lambda item: item[0])
    manifest = {"directories": directories,
                "files": [(path, len(data), _digest(data)) for path, data in files]}
    return FrozenCandidate(_digest(_canonical(manifest)), tuple(directories), tuple(files))


def _capture(root: Path) -> FrozenCandidate:
    first = _read_tree(root)
    if _read_tree(root).candidate_hash != first.candidate_hash:
        raise ValueError("candidate changed while freezing")
    return first


def _materialize(snapshot: FrozenCandidate, root: Path) -> None:
    root.mkdir()
    for directory in snapshot.directories:
        (root / directory).mkdir(parents=True, exist_ok=True)
    for name, data in snapshot.files:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def _run_program(program: CoverageProgram, request: dict, *, timeout_seconds: float = 10.0) -> dict:
    if not program.identity or program.role not in {"planner", "reviewer"}:
        raise ValueError("invalid coverage program identity")
    if not program.executable.is_absolute() or _file_hash(program.executable) != program.sha256:
        raise ValueError("coverage executable identity mismatch")
    completed = subprocess.run([sys.executable, "-I", "-B", str(program.executable)],
                               input=_canonical(request), capture_output=True,
                               timeout=timeout_seconds, check=False)
    if completed.returncode != 0 or len(completed.stdout) > 65536:
        raise ValueError("coverage program failed")
    output = json.loads(completed.stdout.decode("utf-8"))
    if not isinstance(output, dict) or output.get("role") != program.role or _file_hash(program.executable) != program.sha256:
        raise ValueError("coverage program output/identity mismatch")
    return output


class ControllerSession:
    """Only this controller can execute checks and record authorized candidate S."""

    def __init__(self, contract: TaskContract, *, planner: CoverageProgram | None,
                 reviewer: CoverageProgram | None) -> None:
        self.contract = contract
        self.session_id = uuid4().hex
        self.__key = secrets.token_bytes(32)
        self.__authorized: dict[str, FrozenCandidate] = {}
        self.__coverage = self._establish_coverage(planner, reviewer)
        self.__coverage_seal = hmac.new(self.__key, _canonical(asdict(self.__coverage)),
                                        hashlib.sha256).hexdigest()

    @property
    def coverage(self) -> CoverageAuthorization:
        return self.__coverage

    def _coverage_valid(self) -> bool:
        item = self.__coverage
        return (hmac.compare_digest(self.__coverage_seal,
                                    hmac.new(self.__key, _canonical(asdict(item)), hashlib.sha256).hexdigest())
                and item.task_hash == self.contract.task_hash
                and item.obligation_map_hash == self.contract.obligation_map_hash
                and item.contract_hash == self.contract.sha256
                and item.disposition is CoverageDisposition.COVERAGE_AUTHORIZED
                and item.planner_identity is not None and item.planner_run_id is not None
                and item.reviewer_identity is not None and item.reviewer_run_id is not None
                and item.planner_identity != item.reviewer_identity and not item.unresolved_items)

    def _establish_coverage(self, planner: CoverageProgram | None,
                            reviewer: CoverageProgram | None) -> CoverageAuthorization:
        base = {"task_hash": self.contract.task_hash,
                "obligation_map_hash": self.contract.obligation_map_hash,
                "contract_hash": self.contract.sha256}
        unresolved = CoverageAuthorization(CoverageDisposition.COVERAGE_UNRESOLVED,
                                           *base.values(), None, None, None, None,
                                           ("coverage not established",), None)
        if planner is None or reviewer is None or planner.role != "planner" or reviewer.role != "reviewer":
            return unresolved
        if planner.identity == reviewer.identity or planner.executable.resolve() == reviewer.executable.resolve():
            return unresolved
        planner_run = uuid4().hex
        reviewer_run = uuid4().hex
        request = {**base, "original_task": self.contract.original_task,
                   "obligations": [asdict(item) for item in self.contract.obligations],
                   "planner_run_id": planner_run}
        try:
            proposal = _run_program(planner, request)
            if (proposal.get("identity") != planner.identity or proposal.get("run_id") != planner_run
                    or any(proposal.get(key) != value for key, value in base.items())):
                return unresolved
            review = _run_program(reviewer, {**request, "planner_identity": planner.identity,
                                            "planner_proposal": proposal, "reviewer_run_id": reviewer_run})
            if (review.get("identity") != reviewer.identity or review.get("run_id") != reviewer_run
                    or review.get("planner_run_id") != planner_run
                    or review.get("planner_identity") != planner.identity
                    or any(review.get(key) != value for key, value in base.items())):
                return unresolved
            disposition = CoverageDisposition(review.get("disposition"))
            items = review.get("unresolved_items")
            if not isinstance(items, list) or not all(type(item) is str for item in items):
                return unresolved
            if disposition is CoverageDisposition.COVERAGE_AUTHORIZED and items:
                return unresolved
            record = CoverageAuthorization(disposition, *base.values(), planner.identity, planner_run,
                                           reviewer.identity, reviewer_run, tuple(items),
                                           _digest(_canonical({"planner": proposal, "reviewer": review,
                                                               "planner_sha256": planner.sha256,
                                                               "reviewer_sha256": reviewer.sha256})))
            if disposition is CoverageDisposition.COVERAGE_REJECTED:
                raise ContractInvalid("independent coverage reviewer rejected contract")
            return record
        except ContractInvalid:
            raise
        except (OSError, ValueError, UnicodeError, subprocess.TimeoutExpired, json.JSONDecodeError):
            return unresolved

    def _seal(self, result: CheckResult) -> str:
        return hmac.new(self.__key, _canonical(asdict(result)), hashlib.sha256).hexdigest()

    def _execute_check(self, check: ProtectedCheck, snapshot: FrozenCandidate) -> _IssuedEvidence:
        result_kwargs = {"session_id": self.session_id, "execution_id": uuid4().hex,
                         "task_hash": self.contract.task_hash, "contract_hash": self.contract.sha256,
                         "check_id": check.check_id, "obligation_id": check.obligation_id,
                         "check_sha256": check.sha256, "candidate_hash": snapshot.candidate_hash}
        verdict = Verdict.ERROR
        succeeded = False
        observation = "check could not be executed"
        try:
            if _file_hash(check.executable) != check.sha256:
                raise ValueError("registered check identity changed")
            with tempfile.TemporaryDirectory(prefix="tf-check-copy-") as temporary:
                frozen_path = Path(temporary) / "candidate"
                _materialize(snapshot, frozen_path)
                if _read_tree(frozen_path).candidate_hash != snapshot.candidate_hash:
                    raise ValueError("materialized candidate differs from S")
                completed = subprocess.run([sys.executable, "-I", "-B", str(check.executable), str(frozen_path)],
                                           cwd=frozen_path, capture_output=True, timeout=10, check=False)
                if completed.returncode != 0 or len(completed.stdout) > 65536:
                    raise ValueError("registered check failed or exceeded output limit")
                output = json.loads(completed.stdout.decode("utf-8"))
                if (not isinstance(output, dict)
                        or set(output) != {"check_id", "obligation_id", "verdict", "observation"}
                        or output["check_id"] != check.check_id
                        or output["obligation_id"] != check.obligation_id
                        or type(output["observation"]) is not str):
                    raise ValueError("check output identity/shape mismatch")
                if _read_tree(frozen_path).candidate_hash != snapshot.candidate_hash:
                    raise ValueError("check mutated its frozen candidate copy")
                if _file_hash(check.executable) != check.sha256:
                    raise ValueError("registered check changed during execution")
                verdict = Verdict(output["verdict"])
                observation = output["observation"]
                succeeded = True
        except (OSError, ValueError, UnicodeError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
            observation = f"check execution unavailable: {type(exc).__name__}"
        result = CheckResult(**result_kwargs, verdict=verdict, executed_successfully=succeeded,
                             observation=observation)
        return _IssuedEvidence(result, self._seal(result))

    def _reduce(self, snapshot: FrozenCandidate, issued: tuple[_IssuedEvidence, ...]) -> Decision:
        if not self._coverage_valid():
            return Decision.HUMAN_REQUIRED
        if len(issued) != len(self.contract.checks):
            return Decision.HUMAN_REQUIRED
        by_id: dict[str, CheckResult] = {}
        for item in issued:
            if not isinstance(item, _IssuedEvidence) or not isinstance(item.result, CheckResult):
                return Decision.HUMAN_REQUIRED
            result = item.result
            if not hmac.compare_digest(item.seal, self._seal(result)) or result.check_id in by_id:
                return Decision.HUMAN_REQUIRED
            if (result.session_id != self.session_id or result.task_hash != self.contract.task_hash
                    or result.contract_hash != self.contract.sha256
                    or result.candidate_hash != snapshot.candidate_hash):
                return Decision.HUMAN_REQUIRED
            by_id[result.check_id] = result
        if set(by_id) != {check.check_id for check in self.contract.checks}:
            return Decision.HUMAN_REQUIRED
        for check in self.contract.checks:
            result = by_id[check.check_id]
            if (result.obligation_id != check.obligation_id or result.check_sha256 != check.sha256
                    or not result.executed_successfully):
                return Decision.HUMAN_REQUIRED
        mandatory = [by_id[check_id] for obligation in self.contract.obligations if obligation.mandatory
                     for check_id in obligation.required_check_ids]
        if any(item.mandatory and item.verification_mode == "UNSUPPORTED" for item in self.contract.obligations):
            return Decision.HUMAN_REQUIRED
        if any(item.verdict in {Verdict.UNKNOWN, Verdict.ERROR} for item in mandatory):
            return Decision.HUMAN_REQUIRED
        if any(item.verdict == Verdict.FAIL for item in mandatory):
            return Decision.REJECT_DONE
        return Decision.VERIFIED_DONE

    def attempt(self, workspace: Path, *, worker_response: str = "") -> CompletionReceipt:
        if self.coverage.disposition is CoverageDisposition.COVERAGE_REJECTED:
            raise ContractInvalid("coverage rejected")
        snapshot: FrozenCandidate | None = None
        issued: tuple[_IssuedEvidence, ...] = ()
        decision = Decision.HUMAN_REQUIRED
        try:
            if self._coverage_valid():
                root = workspace.resolve(strict=True)
                for check in self.contract.checks:
                    if check.executable.is_relative_to(root) or check.executable.resolve(strict=True).is_relative_to(root):
                        raise ContractInvalid("protected check lies inside worker workspace")
                snapshot = _capture(root)
                issued = tuple(self._execute_check(check, snapshot) for check in self.contract.checks)
                decision = self._reduce(snapshot, issued)
                if decision is Decision.VERIFIED_DONE:
                    self.__authorized[snapshot.candidate_hash] = snapshot
        except ContractInvalid:
            raise
        except (OSError, ValueError, RuntimeError):
            decision = Decision.HUMAN_REQUIRED
        return CompletionReceipt(self.session_id, uuid4().hex, self.contract.sha256,
                                 snapshot.candidate_hash if snapshot else None,
                                 tuple(item.result for item in issued), decision, worker_response)

    def authorized_state_decision(self, candidate_hash: str) -> Decision:
        """Consult controller state, never a caller-created receipt."""
        return (Decision.VERIFIED_DONE if candidate_hash in self.__authorized
                else Decision.HUMAN_REQUIRED)

    def current_workspace_decision(self, workspace: Path) -> Decision:
        try:
            state = _capture(workspace).candidate_hash
        except (OSError, ValueError, RuntimeError):
            return Decision.HUMAN_REQUIRED
        return self.authorized_state_decision(state)
