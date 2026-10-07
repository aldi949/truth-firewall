"""Controller-owned two-authority coverage tests using disposable subprocesses."""

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from truth_firewall.contract_coverage import ObligationView
from truth_firewall.coverage_authority import (
    CoverageAuthorityController,
    CoverageProgram,
    EvidenceAuthorization,
    EvidenceStatus,
    FinalCoverageStatus,
    InventoryAuthorization,
    InventoryStatus,
)


TASK = "Require alpha. Require beta. Require gamma."


def obligation(name: str) -> dict:
    quote = f"Require {name}"
    start = TASK.index(quote)
    return {
        "obligation_id": name,
        "source_span": [start, start + len(quote)],
        "source_text": quote,
        "required_behavior": f"Emit required behavior for {name}",
        "classification": "MANDATORY",
        "claim": {"predicate": f"emits_{name}", "scope": "output", "semantics": "OUTPUT",
                   "cases": ["standard"], "side_effects": []},
        "uncertainty": "NONE",
        "uncertainty_reason": "",
    }


def program(tmp_path: Path, role: str, config: dict) -> CoverageProgram:
    identity = f"trusted-{role}"
    config_json = json.dumps(config)
    script = f'''import json, sys
request = json.loads(sys.stdin.buffer.read())
config = json.loads({config_json!r})
response = {{"role": {role!r}, "identity": {identity!r},
            "run_id": request["run_id"], "task_hash": request["task_hash"]}}
if {role!r} in ("planner", "reviewer"):
    response["obligations"] = config["obligations"]
elif {role!r} == "reconciler":
    response.update(status=config["status"], unresolved_items=config["unresolved_items"],
                    obligations=config["obligations"])
else:
    response["status"] = config["status"]
    response["evidence_records"] = config["evidence_records"]
sys.stdout.write(json.dumps(response))
'''
    path = tmp_path / f"{role}.py"
    path.write_text(script, encoding="utf-8")
    return CoverageProgram(identity, role, path.resolve(), hashlib.sha256(path.read_bytes()).hexdigest())


def configured(tmp_path: Path, planner_names=("alpha", "beta"), reviewer_names=("alpha", "beta", "gamma"),
               final_names=("alpha", "beta", "gamma"), inventory_status="INVENTORY_COVERAGE_AUTHORIZED",
               unresolved=(), evidence_names=None):
    p_items = [obligation(name) for name in planner_names]
    r_items = [obligation(name) for name in reviewer_names]
    final_items = [obligation(name) for name in final_names]
    evidence_names = final_names if evidence_names is None else evidence_names
    evidence_records = []
    for name in evidence_names:
        item = obligation(name)
        evidence_records.append({"obligation_id": name, "claim": item["claim"],
                                 "description": f"independently inspect {name}",
                                 "review_status": "ENTAILS", "rationale": "condition entails claim"})
    programs = (
        program(tmp_path, "planner", {"obligations": p_items}),
        program(tmp_path, "reviewer", {"obligations": r_items}),
        program(tmp_path, "reconciler", {"status": inventory_status,
                                         "unresolved_items": list(unresolved),
                                         "obligations": final_items}),
        program(tmp_path, "evidence_reviewer", {"status": "EVIDENCE_AUTHORIZED",
                                                  "evidence_records": evidence_records}),
    )
    return CoverageAuthorityController(planner=programs[0], reviewer=programs[1],
                                       reconciler=programs[2], evidence_reviewer=programs[3])


def test_reviewer_found_missing_obligation_and_final_dropped_it(tmp_path):
    controller = configured(tmp_path, final_names=("alpha", "beta"))
    inventory = controller.authorize_inventory(TASK)
    assert inventory.status is InventoryStatus.UNRESOLVED
    assert any(item.source_text == "Require gamma" for item in inventory.inventory) is False


def test_reconciliation_preserving_reviewer_obligation_may_authorize(tmp_path):
    controller = configured(tmp_path)
    inventory = controller.authorize_inventory(TASK)
    assert inventory.status is InventoryStatus.AUTHORIZED
    evidence = controller.authorize_evidence(TASK, inventory)
    final = controller.authorize_final(TASK, inventory, evidence)
    assert final.status is FinalCoverageStatus.AUTHORIZED
    assert controller.is_final_authorized(final)


def test_unresolved_branch_disagreement_stays_unresolved(tmp_path):
    controller = configured(tmp_path, inventory_status="INVENTORY_COVERAGE_UNRESOLVED",
                             unresolved=("reviewer and planner interpretations differ",))
    inventory = controller.authorize_inventory(TASK)
    assert inventory.status is InventoryStatus.UNRESOLVED


def test_fabricated_inventory_authority_cannot_authorize(tmp_path):
    controller = configured(tmp_path)
    inventory = controller.authorize_inventory(TASK)
    evidence = controller.authorize_evidence(TASK, inventory)
    fake = replace(inventory, authority_id="caller-made", seal="0" * 64)
    final = controller.authorize_final(TASK, fake, evidence)
    assert final.status is FinalCoverageStatus.UNRESOLVED
    assert not controller.is_final_authorized(final)


def test_inventory_authority_is_bound_to_original_task_hash(tmp_path):
    controller = configured(tmp_path)
    inventory = controller.authorize_inventory(TASK)
    with pytest.raises(ValueError):
        controller.authorize_evidence(TASK + " Changed.", inventory)


def test_inventory_authority_is_bound_to_exact_inventory_hash(tmp_path):
    controller = configured(tmp_path)
    inventory = controller.authorize_inventory(TASK)
    evidence = controller.authorize_evidence(TASK, inventory)
    forged = replace(inventory, inventory_hash="0" * 64)
    final = controller.authorize_final(TASK, forged, evidence)
    assert final.status is FinalCoverageStatus.UNRESOLVED


def test_inventory_mutation_invalidates_prior_authority(tmp_path):
    controller = configured(tmp_path)
    inventory = controller.authorize_inventory(TASK)
    changed = replace(inventory.inventory[0], required_behavior="mutated")
    mutated = replace(inventory, inventory=(changed, *inventory.inventory[1:]))
    with pytest.raises(ValueError):
        controller.authorize_evidence(TASK, mutated)


def test_insufficient_evidence_cannot_authorize_final_coverage(tmp_path):
    controller = configured(tmp_path, evidence_names=("alpha", "beta"))
    inventory = controller.authorize_inventory(TASK)
    evidence = controller.authorize_evidence(TASK, inventory)
    assert evidence.status is EvidenceStatus.UNRESOLVED
    final = controller.authorize_final(TASK, inventory, evidence)
    assert final.status is FinalCoverageStatus.UNRESOLVED


def test_sufficient_evidence_without_valid_inventory_authority_cannot_authorize(tmp_path):
    controller = configured(tmp_path)
    inventory = controller.authorize_inventory(TASK)
    evidence = controller.authorize_evidence(TASK, inventory)
    fake_inventory = replace(inventory, seal="bad")
    fake_evidence = replace(evidence, seal="bad")
    final = controller.authorize_final(TASK, fake_inventory, fake_evidence)
    assert final.status is FinalCoverageStatus.UNRESOLVED


def test_both_authorities_are_required_for_final_authorization(tmp_path):
    controller = configured(tmp_path)
    inventory = controller.authorize_inventory(TASK)
    evidence = controller.authorize_evidence(TASK, inventory)
    authorized = controller.authorize_final(TASK, inventory, evidence)
    assert authorized.status is FinalCoverageStatus.AUTHORIZED
    assert controller.is_final_authorized(authorized)


def test_both_decompositions_missing_same_clause_is_not_proof_of_completeness(tmp_path):
    controller = configured(tmp_path, planner_names=("alpha", "beta"),
                             reviewer_names=("alpha", "beta"), final_names=("alpha", "beta"))
    inventory = controller.authorize_inventory(TASK)
    # Limitation: no branch disclosed gamma, so the reconciler cannot know it
    # was omitted. Authorization records completion of the procedure, not a
    # mathematical proof that natural-language extraction was exhaustive.
    assert inventory.status is InventoryStatus.AUTHORIZED
    assert all(item.source_text != "Require gamma" for item in inventory.inventory)
