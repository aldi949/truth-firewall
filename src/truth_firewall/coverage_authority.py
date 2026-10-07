"""Controller-owned inventory and evidence authority for contract coverage.

The configured programs are trusted controller dependencies. This module
invokes them itself; model outputs and caller-created records are proposals,
never authority. Natural-language completeness remains an empirical property,
not a mathematical guarantee.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import subprocess
import sys
from dataclasses import asdict, dataclass, replace
from enum import Enum
from pathlib import Path
from uuid import uuid4

from .contract_coverage import Claim, EntailmentStatus, ObligationView, RequirementClass, Semantics, Uncertainty, entails


class InventoryStatus(str, Enum):
    AUTHORIZED = "INVENTORY_COVERAGE_AUTHORIZED"
    UNRESOLVED = "INVENTORY_COVERAGE_UNRESOLVED"
    REJECTED = "INVENTORY_COVERAGE_REJECTED"


class EvidenceStatus(str, Enum):
    AUTHORIZED = "EVIDENCE_AUTHORIZED"
    UNRESOLVED = "EVIDENCE_UNRESOLVED"
    REJECTED = "EVIDENCE_REJECTED"


class FinalCoverageStatus(str, Enum):
    AUTHORIZED = "COVERAGE_AUTHORIZED"
    UNRESOLVED = "COVERAGE_UNRESOLVED"


@dataclass(frozen=True)
class CoverageProgram:
    identity: str
    role: str
    executable: Path
    sha256: str


@dataclass(frozen=True)
class InventoryAuthorization:
    task_hash: str
    inventory_hash: str
    inventory: tuple[ObligationView, ...]
    planner_identity: str
    planner_run_id: str
    reviewer_identity: str
    reviewer_run_id: str
    reconciliation_identity: str
    reconciliation_run_id: str
    status: InventoryStatus
    provenance: str
    provenance_hash: str
    authority_id: str
    seal: str


@dataclass(frozen=True)
class EvidenceAuthorization:
    task_hash: str
    inventory_hash: str
    evidence_hash: str
    status: EvidenceStatus
    evidence_records: tuple[tuple[str, Claim, str], ...]
    reviewer_identity: str
    reviewer_run_id: str
    provenance: str
    provenance_hash: str
    authority_id: str
    seal: str


@dataclass(frozen=True)
class FinalCoverageAuthorization:
    task_hash: str
    inventory_hash: str
    evidence_hash: str
    status: FinalCoverageStatus
    inventory_authority_id: str
    evidence_authority_id: str
    authority_id: str
    seal: str


def _canonical(value: object) -> bytes:
    def normalize(item: object) -> object:
        if isinstance(item, Enum):
            return item.value
        if isinstance(item, dict):
            return {key: normalize(child) for key, child in item.items()}
        if isinstance(item, (tuple, list)):
            return [normalize(child) for child in item]
        if isinstance(item, (set, frozenset)):
            return sorted(normalize(child) for child in item)
        return item

    return json.dumps(normalize(value), ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _claim_json(claim: Claim) -> dict:
    return {"predicate": claim.predicate, "scope": claim.scope,
            "semantics": claim.semantics.value, "cases": sorted(claim.cases),
            "side_effects": sorted(claim.side_effects)}


def _obligation_json(item: ObligationView) -> dict:
    return {"obligation_id": item.obligation_id, "source_span": list(item.source_span),
            "source_text": item.source_text, "required_behavior": item.required_behavior,
            "classification": item.classification.value, "claim": _claim_json(item.claim),
            "uncertainty": item.uncertainty.value, "uncertainty_reason": item.uncertainty_reason}


def _inventory_hash(inventory: tuple[ObligationView, ...]) -> str:
    return _hash(_canonical([_obligation_json(item) for item in inventory]))


def _parse_claim(value: object) -> Claim:
    required = {"predicate", "scope", "semantics", "cases", "side_effects"}
    if type(value) is not dict or set(value) != required:
        raise ValueError("invalid claim schema")
    if (type(value["predicate"]) is not str or type(value["scope"]) is not str
            or type(value["cases"]) is not list or type(value["side_effects"]) is not list
            or any(type(item) is not str for item in (*value["cases"], *value["side_effects"]))):
        raise ValueError("invalid claim values")
    return Claim(value["predicate"], value["scope"], Semantics(value["semantics"]),
                 frozenset(value["cases"]), frozenset(value["side_effects"]))


def _parse_obligation(value: object, task: str) -> ObligationView:
    required = {"obligation_id", "source_span", "source_text", "required_behavior", "classification",
                "claim", "uncertainty", "uncertainty_reason"}
    if type(value) is not dict or set(value) != required:
        raise ValueError("invalid obligation schema")
    span = value["source_span"]
    if (type(span) is not list or len(span) != 2 or any(type(item) is not int for item in span)
            or type(value["obligation_id"]) is not str or type(value["source_text"]) is not str
            or type(value["required_behavior"]) is not str or type(value["uncertainty_reason"]) is not str):
        raise ValueError("invalid obligation values")
    item = ObligationView(value["obligation_id"], (span[0], span[1]), value["source_text"],
                          value["required_behavior"], RequirementClass(value["classification"]),
                          _parse_claim(value["claim"]), Uncertainty(value["uncertainty"]),
                          value["uncertainty_reason"])
    item.validate(task)
    return item


class CoverageAuthorityController:
    """Runs independent coverage programs and owns the resulting authority."""

    def __init__(self, *, planner: CoverageProgram, reviewer: CoverageProgram,
                 reconciler: CoverageProgram, evidence_reviewer: CoverageProgram) -> None:
        programs = (planner, reviewer, reconciler, evidence_reviewer)
        if any(item.role != role for item, role in zip(programs, ("planner", "reviewer", "reconciler", "evidence_reviewer"))):
            raise ValueError("coverage program roles do not match controller slots")
        if any(not item.identity or not item.executable.is_absolute() or len(item.sha256) != 64 for item in programs):
            raise ValueError("invalid coverage program identity")
        if len({item.identity for item in programs}) != len(programs):
            raise ValueError("coverage programs must have distinct identities")
        self.__programs = {item.role: item for item in programs}
        self.__key = secrets.token_bytes(32)
        self.__issued_inventory: dict[str, str] = {}
        self.__issued_evidence: dict[str, str] = {}
        self.__issued_final: dict[str, str] = {}

    def _invoke(self, role: str, request: dict) -> dict:
        program = self.__programs[role]
        if _hash(program.executable.read_bytes()) != program.sha256:
            raise ValueError(f"{role} program identity changed")
        completed = subprocess.run([sys.executable, "-I", "-B", str(program.executable)],
                                   input=_canonical(request), capture_output=True, timeout=30, check=False)
        if completed.returncode != 0 or len(completed.stdout) > 1_000_000:
            raise ValueError(f"{role} coverage program failed")
        output = json.loads(completed.stdout.decode("utf-8"))
        if (type(output) is not dict or output.get("role") != role
                or output.get("identity") != program.identity or output.get("run_id") != request["run_id"]
                or output.get("task_hash") != request["task_hash"]
                or _hash(program.executable.read_bytes()) != program.sha256):
            raise ValueError(f"{role} response identity mismatch")
        return output

    def authorize_inventory(self, original_task: str, context: str = "") -> InventoryAuthorization:
        if not original_task:
            raise ValueError("original task is required")
        task_hash = _hash(original_task.encode("utf-8"))
        planner_id, reviewer_id, reconciler_id = uuid4().hex, uuid4().hex, uuid4().hex

        # Reviewer receives exactly task/context identity; planner material is
        # not supplied, and the planner call completes independently first.
        planner = self._invoke("planner", {"task_hash": task_hash, "run_id": planner_id,
                                           "original_task": original_task, "context": context})
        reviewer = self._invoke("reviewer", {"task_hash": task_hash, "run_id": reviewer_id,
                                             "original_task": original_task, "context": context})
        p_items = tuple(_parse_obligation(item, original_task) for item in planner.get("obligations", []))
        r_items = tuple(_parse_obligation(item, original_task) for item in reviewer.get("obligations", []))
        if not p_items or not r_items:
            raise ValueError("both independent decompositions must return obligations")

        reconciliation = self._invoke("reconciler", {
            "task_hash": task_hash, "run_id": reconciler_id, "original_task": original_task,
            "planner_identity": self.__programs["planner"].identity, "planner_run_id": planner_id,
            "planner_obligations": [_obligation_json(item) for item in p_items],
            "reviewer_identity": self.__programs["reviewer"].identity, "reviewer_run_id": reviewer_id,
            "reviewer_obligations": [_obligation_json(item) for item in r_items],
        })
        status = InventoryStatus(reconciliation.get("status"))
        unresolved = reconciliation.get("unresolved_items")
        final_values = reconciliation.get("obligations")
        if (type(unresolved) is not list or not all(type(item) is str for item in unresolved)
                or type(final_values) is not list):
            raise ValueError("invalid reconciliation response")
        inventory = tuple(_parse_obligation(item, original_task) for item in final_values)
        if not inventory or len({item.obligation_id for item in inventory}) != len(inventory):
            raise ValueError("reconciled inventory is empty or has duplicate IDs")
        if status is InventoryStatus.AUTHORIZED and unresolved:
            status = InventoryStatus.UNRESOLVED

        # Deterministic monotonicity check: every mandatory condition from
        # either independent branch must be entailed by some final entry.
        required = [item for item in (*p_items, *r_items)
                    if item.classification is RequirementClass.MANDATORY]
        if (not required or not any(item.classification is RequirementClass.MANDATORY for item in inventory)
                or any(item.uncertainty is Uncertainty.REQUIRED for item in required)):
            status = InventoryStatus.UNRESOLVED
        for branch_item in required:
            if not any(final.source_span == branch_item.source_span
                       and final.classification is RequirementClass.MANDATORY
                       and entails(final.claim, branch_item.claim) for final in inventory):
                status = InventoryStatus.UNRESOLVED
                break

        provenance = {"planner_identity": self.__programs["planner"].identity,
                      "planner_run_id": planner_id, "planner_hash": self.__programs["planner"].sha256,
                      "reviewer_identity": self.__programs["reviewer"].identity,
                      "reviewer_run_id": reviewer_id, "reviewer_hash": self.__programs["reviewer"].sha256,
                      "reconciler_identity": self.__programs["reconciler"].identity,
                      "reconciler_run_id": reconciler_id,
                      "reconciler_hash": self.__programs["reconciler"].sha256,
                      "planner_response": planner, "reviewer_response": reviewer,
                      "reconciliation_response": reconciliation}
        inventory_hash = _inventory_hash(inventory)
        authority_id = uuid4().hex
        provenance_text = _canonical(provenance).decode("utf-8")
        bare = InventoryAuthorization(task_hash, inventory_hash, inventory,
                                       self.__programs["planner"].identity, planner_id,
                                       self.__programs["reviewer"].identity, reviewer_id,
                                       self.__programs["reconciler"].identity, reconciler_id,
                                       status, provenance_text, _hash(provenance_text.encode("utf-8")),
                                       authority_id, "")
        seal = self.__seal(asdict(bare))
        authority = replace(bare, seal=seal)
        self.__issued_inventory[authority_id] = seal
        return authority

    def authorize_evidence(self, original_task: str,
                           inventory_authority: InventoryAuthorization) -> EvidenceAuthorization:
        if not self._valid_inventory(inventory_authority):
            raise ValueError("inventory authority is not controller-issued or no longer valid")
        task_hash = _hash(original_task.encode("utf-8"))
        if (task_hash != inventory_authority.task_hash
                or inventory_authority.status is not InventoryStatus.AUTHORIZED):
            raise ValueError("inventory authority does not authorize this task")
        run_id = uuid4().hex
        response = self._invoke("evidence_reviewer", {
            "task_hash": task_hash, "run_id": run_id, "original_task": original_task,
            "inventory_hash": inventory_authority.inventory_hash,
            "inventory": [_obligation_json(item) for item in inventory_authority.inventory],
        })
        status = EvidenceStatus(response.get("status"))
        records_value = response.get("evidence_records")
        if type(records_value) is not list:
            raise ValueError("invalid evidence review response")
        records: list[tuple[str, Claim, str]] = []
        seen: set[str] = set()
        by_id = {item.obligation_id: item for item in inventory_authority.inventory}
        for record in records_value:
            required = {"obligation_id", "claim", "description", "review_status", "rationale"}
            if type(record) is not dict or set(record) != required:
                raise ValueError("invalid evidence record")
            obligation_id = record["obligation_id"]
            if (type(obligation_id) is not str or obligation_id in seen or obligation_id not in by_id
                    or type(record["description"]) is not str or type(record["rationale"]) is not str):
                raise ValueError("evidence record identity mismatch")
            seen.add(obligation_id)
            claim = _parse_claim(record["claim"])
            obligation = by_id[obligation_id]
            if (record["review_status"] != EntailmentStatus.ENTAILS.value
                    or not record["description"].strip() or not record["rationale"].strip()
                    or obligation.classification is not RequirementClass.MANDATORY
                    or obligation.uncertainty is Uncertainty.REQUIRED
                    or not entails(claim, obligation.claim)):
                status = EvidenceStatus.UNRESOLVED
            records.append((obligation_id, claim, record["description"]))
        required_ids = {item.obligation_id for item in inventory_authority.inventory
                        if item.classification is RequirementClass.MANDATORY}
        if not required_ids <= seen:
            status = EvidenceStatus.UNRESOLVED
        evidence_hash = _hash(_canonical([(key, _claim_json(claim), description)
                                          for key, claim, description in records]))
        provenance_text = _canonical(response).decode("utf-8")
        provenance_hash = _hash(provenance_text.encode("utf-8"))
        authority_id = uuid4().hex
        bare = EvidenceAuthorization(task_hash, inventory_authority.inventory_hash, evidence_hash,
                                     status, tuple(records), self.__programs["evidence_reviewer"].identity,
                                     run_id, provenance_text, provenance_hash, authority_id, "")
        seal = self.__seal(asdict(bare))
        authority = replace(bare, seal=seal)
        self.__issued_evidence[authority_id] = seal
        return authority

    def authorize_final(self, original_task: str, inventory: InventoryAuthorization,
                        evidence: EvidenceAuthorization) -> FinalCoverageAuthorization:
        task_hash = _hash(original_task.encode("utf-8"))
        valid = (self._valid_inventory(inventory) and self._valid_evidence(evidence)
                 and inventory.status is InventoryStatus.AUTHORIZED
                 and evidence.status is EvidenceStatus.AUTHORIZED
                 and task_hash == inventory.task_hash == evidence.task_hash
                 and inventory.inventory_hash == evidence.inventory_hash
                 and _inventory_hash(inventory.inventory) == inventory.inventory_hash)
        status = FinalCoverageStatus.AUTHORIZED if valid else FinalCoverageStatus.UNRESOLVED
        authority_id = uuid4().hex
        bare = FinalCoverageAuthorization(task_hash, inventory.inventory_hash, evidence.evidence_hash,
                                          status, inventory.authority_id, evidence.authority_id,
                                          authority_id, "")
        seal = self.__seal(asdict(bare))
        result = replace(bare, seal=seal)
        self.__issued_final[authority_id] = seal
        return result

    def is_final_authorized(self, authorization: FinalCoverageAuthorization) -> bool:
        if not isinstance(authorization, FinalCoverageAuthorization) or type(authorization.seal) is not str:
            return False
        expected = self.__issued_final.get(authorization.authority_id)
        return (expected is not None and hmac.compare_digest(expected, authorization.seal)
                and authorization.status is FinalCoverageStatus.AUTHORIZED
                and hmac.compare_digest(expected, self.__seal({**asdict(authorization), "seal": ""})))

    def __seal(self, value: object) -> str:
        return hmac.new(self.__key, _canonical(value), hashlib.sha256).hexdigest()

    def _valid_inventory(self, item: InventoryAuthorization) -> bool:
        if (not isinstance(item, InventoryAuthorization) or type(item.seal) is not str
                or type(item.inventory) is not tuple
                or any(not isinstance(entry, ObligationView) for entry in item.inventory)
                or type(item.provenance) is not str):
            return False
        expected = self.__issued_inventory.get(item.authority_id)
        return (expected is not None and item.status is InventoryStatus.AUTHORIZED
                and _inventory_hash(item.inventory) == item.inventory_hash
                and _hash(item.provenance.encode("utf-8")) == item.provenance_hash
                and hmac.compare_digest(expected, item.seal)
                and hmac.compare_digest(expected, self.__seal({**asdict(item), "seal": ""})))

    def _valid_evidence(self, item: EvidenceAuthorization) -> bool:
        if (not isinstance(item, EvidenceAuthorization) or type(item.seal) is not str
                or type(item.evidence_records) is not tuple or type(item.provenance) is not str):
            return False
        expected = self.__issued_evidence.get(item.authority_id)
        return (expected is not None and item.status is EvidenceStatus.AUTHORIZED
                and _hash(item.provenance.encode("utf-8")) == item.provenance_hash
                and hmac.compare_digest(expected, item.seal)
                and hmac.compare_digest(expected, self.__seal({**asdict(item), "seal": ""})))

    def _seal(self, value: object) -> str:
        return hmac.new(self.__key, _canonical(value), hashlib.sha256).hexdigest()
