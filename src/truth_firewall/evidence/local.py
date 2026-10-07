"""Bounded first-party observations requested by a public check surface."""

from __future__ import annotations

from pathlib import Path

from truth_firewall.claims.proposition import AtomicProposition, parse_atomic_proposition
from truth_firewall.evidence.collectors.filesystem import FilesystemCollector
from truth_firewall.evidence.collectors.git import GitCollector
from truth_firewall.evidence.collectors.structure import StructureCollector
from truth_firewall.schemas import Claim, EvidenceRecord


def collect_for_claims(
    claims: list[Claim], root: Path, propositions: dict[str, AtomicProposition] | None = None,
) -> tuple[list[EvidenceRecord], list[str]]:
    records: list[EvidenceRecord] = []
    errors: list[str] = []
    files = FilesystemCollector(root)
    git = GitCollector(root)
    structure = StructureCollector(root)
    for claim in claims:
        proposition = (propositions or {}).get(claim.claim_id) or parse_atomic_proposition(claim)
        if proposition is None or proposition.kind != claim.claim_type and not (
            claim.claim_type == "file_repository_state" and proposition.kind == "existence"
        ):
            continue
        try:
            if proposition.kind == "existence":
                record = files.observe(str(proposition.terms["target_path"]), evidence_id=f"local-{claim.claim_id}")
            elif proposition.kind == "absence":
                scope = "." if proposition.terms["scope_kind"] == "workspace" else str(proposition.terms["scope_path"])
                record = files.search_literal(str(proposition.terms["query"]), scope,
                                              evidence_id=f"local-{claim.claim_id}")
            elif proposition.kind == "config":
                record = files.observe_config(str(proposition.terms["target_path"]),
                                              str(proposition.terms["key"]), evidence_id=f"local-{claim.claim_id}")
            elif proposition.kind == "git":
                record = git.status(evidence_id=f"local-{claim.claim_id}")
            elif proposition.kind in {"source", "dependency"}:
                record = structure.observe(proposition.terms, evidence_id=f"local-{claim.claim_id}")
            else:
                continue
            records.append(record)
        except (OSError, ValueError, RuntimeError) as exc:
            errors.append(f"{claim.claim_id}: local observation unavailable ({type(exc).__name__})")
    return records, errors
