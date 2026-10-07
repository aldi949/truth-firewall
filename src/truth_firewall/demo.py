"""Labeled fixture walkthrough. Output from this command is not live accuracy."""

from __future__ import annotations

import json
import tempfile
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

from truth_firewall.pipeline import check_response
from truth_firewall.providers.fake import FakeProvider
from truth_firewall.schemas import evidence_from_dict, utc_now

BANNER = "DEMO FIXTURES - NOT LIVE PRODUCT ACCURACY"


def fixture_dir() -> Path:
    return Path(__file__).resolve().parent / "fixtures" / "demo"


def run_demo(*, format: str = "text", runs_dir: Path | None = None) -> int:
    now = utc_now()
    run_suffix = uuid4().hex[:8]
    reports = []
    for path in sorted(fixture_dir().glob("*.json")):
        reports.append(_run_one(path, now=now, runs_dir=runs_dir, run_suffix=run_suffix))
    reports.append(_run_local(now=now, runs_dir=runs_dir, run_suffix=run_suffix))
    if format == "json":
        print(json.dumps({"banner": BANNER, "fixtures": reports}, indent=2, ensure_ascii=False))
    else:
        print(BANNER)
        print("Extraction source: labeled fixture claims, validated by SemanticClaimExtractor.")
        print("Verification: deterministic evidence rules.")
        print("Fixture timestamps are assigned when the demo starts.")
        for report in reports:
            print(f"\n== {report['name']} ==")
            print(f"delivery: {report['delivery']}")
            for row in report["verdicts"]:
                print(f"- {row['verdict']} {row['action']}: {row['claim']}")
            print(report["grounded_response"])
    return 0


def _run_local(*, now, runs_dir: Path | None, run_suffix: str) -> dict:
    """A real, short-lived collector observation alongside imported fixtures."""
    with tempfile.TemporaryDirectory(prefix="tf-demo-") as temporary:
        root = Path(temporary)
        (root / "README.md").write_text("A real local demo file.\n", encoding="utf-8")
        result = check_response("README.md exists.", offline=True, collect_local=True,
                                workspace_root=root, runs_dir=runs_dir, now=now,
                                run_id=f"demo-local-observation-{run_suffix}")
    return {
        "name": "local_observation", "label": "FIRST_PARTY_LOCAL", "delivery": result.delivery,
        "extractor_mode": result.extractor_mode,
        "verdicts": [{"verdict": assessment.verdict, "action": decision.action,
                      "claim": claim.normalized_claim}
                     for claim, assessment, decision in zip(
                         result.claims, result.assessments, result.decisions, strict=False)],
        "grounded_response": result.grounded_response, "errors": result.errors,
    }


def _run_one(path: Path, *, now, runs_dir: Path | None, run_suffix: str) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    stamped = []
    for index, item in enumerate(data.get("evidence", []), start=1):
        record = dict(item)
        record["timestamp"] = (now - timedelta(seconds=5)).isoformat()
        record["cwd"] = str(Path.cwd().resolve())
        record["workspace_root"] = str(Path.cwd().resolve())
        record["provenance"] = record.get("provenance") or f"demo-fixture:{data['name']}"
        payload = dict(record.get("structured_payload") or {})
        payload["demo_fixture"] = True
        record["structured_payload"] = payload
        record.setdefault("evidence_id", f"e-{index:03d}")
        parsed = evidence_from_dict(record, evidence_id=record["evidence_id"])
        stamped.append(parsed)
    provider = FakeProvider(claims=data.get("claims", []))
    result = check_response(
        data["response"],
        stamped,
        provider=provider,
        offline=False,
        runs_dir=runs_dir,
        now=now,
        run_id=f"demo-{data['name']}-{run_suffix}",
        workspace_root=Path.cwd(),
    )
    return {
        "name": data["name"],
        "label": data.get("label", "FIXTURE"),
        "delivery": result.delivery,
        "extractor_mode": result.extractor_mode,
        "verdicts": [
            {
                "verdict": assessment.verdict,
                "action": decision.action,
                "claim": claim.normalized_claim,
            }
            for claim, assessment, decision in zip(
                result.claims, result.assessments, result.decisions, strict=False
            )
        ],
        "grounded_response": result.grounded_response,
        "errors": result.errors,
    }
