"""Command line for check, doctor, and demo."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from truth_firewall import __version__
from truth_firewall.errors import CollectorError, EvidenceError
from truth_firewall.evidence.io import load_evidence_paths
from truth_firewall.evidence.ledger import EvidenceLedger
from truth_firewall.pipeline import check_response
from truth_firewall.providers.config import load_provider_from_env, provider_status
from truth_firewall.schemas import utc_now


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="truth-firewall",
        description="Verify bounded local Python coding tasks against independent acceptance checks.",
    )
    parser.add_argument("--version", action="version", version=f"truth-firewall {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check", help="Assess a final response against evidence files.")
    check.add_argument("--response", required=True, help="Response file, or - for stdin.")
    check.add_argument("--evidence", action="append", default=[], help="Evidence file or directory. Repeatable.")
    check.add_argument("--format", choices=("text", "json"), default="text")
    check.add_argument("--runs-dir", default=".truth-firewall/runs")
    check.add_argument("--offline", action="store_true", help="Use the deterministic extractor.")
    check.add_argument("--observe-local", action="store_true",
                       help="Collect bounded filesystem, config, search, and Git observations locally.")
    check.add_argument("--run-pytest", action="store_true",
                       help="Explicitly run the current workspace's pytest suite in controlled mode.")
    check.add_argument(
        "--explain",
        action="store_true",
        help="Ask the provider for a note. The verdict stays deterministic.",
    )

    doctor = sub.add_parser("doctor", help="Report configuration and environment.")
    doctor.add_argument("--evidence", action="append", default=[])
    doctor.add_argument("--format", choices=("text", "json"), default="text")

    demo = sub.add_parser("demo", help="Run labeled local fixtures. Not a live accuracy score.")
    demo.add_argument("--format", choices=("text", "json"), default="text")
    demo.add_argument("--runs-dir", default=".truth-firewall/runs")

    run = sub.add_parser("run", help="Run a bounded local Python task with Codex and independent checks.")
    run.add_argument("--spec", default=".truth-firewall/task.json",
                     help="JSON task and acceptance-check file (default: .truth-firewall/task.json)")
    run.add_argument("--attempts", type=int, choices=(1, 2, 3), default=3,
                     help="Maximum worker attempts (default: 3)")

    args = parser.parse_args(argv)
    if args.command == "check":
        return _check_command(args)
    if args.command == "doctor":
        return _doctor_command(args)
    if args.command == "run":
        from truth_firewall.pilot import run_pilot

        return run_pilot(args.spec, attempts=args.attempts)
    return _demo_command(args)


def _check_command(args: argparse.Namespace) -> int:
    try:
        response = _read_response(args.response)
    except OSError as exc:
        print(f"error: cannot read response: {exc}", file=sys.stderr)
        return 1
    records, errors = load_evidence_paths([Path(item) for item in args.evidence])
    ledger = EvidenceLedger()
    for record in records:
        try:
            ledger.append(record)
        except EvidenceError as exc:
            errors.append(str(exc))
    def fresh_pytest():
        from truth_firewall.evidence.collectors.test_runner import TestEvidence

        try:
            return [TestEvidence().run([sys.executable, "-m", "pytest", "-q"],
                                       Path.cwd(), explicit=True, evidence_id="local-pytest")]
        except (OSError, ValueError, EvidenceError, CollectorError, subprocess.SubprocessError) as exc:
            errors.append(f"local pytest run unavailable: {type(exc).__name__}: {exc}")
            return []
    provider = None
    if not args.offline:
        try:
            provider = load_provider_from_env()
        except Exception as exc:
            errors.append(f"provider unavailable, using offline extractor: {exc}")
    result = check_response(
        response,
        list(ledger.records()),
        provider=provider,
        offline=args.offline or provider is None,
        explain=args.explain,
        runs_dir=Path(args.runs_dir),
        now=utc_now(),
        collect_local=args.observe_local,
        fresh_test_collector=fresh_pytest if args.run_pytest else None,
    )
    result.errors = errors + result.errors
    if args.format == "json":
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
    else:
        _print_text(result)
    if not result.verification_available:
        return 1
    if any(item.action == "block_or_rewrite" for item in result.decisions):
        return 2
    return 0


def _doctor_command(args: argparse.Namespace) -> int:
    report = {
        "python": sys.version.split()[0],
        "package": __version__,
        "platform": sys.platform,
        "provider": provider_status(),
        "evidence": [],
        "evidence_errors": [],
    }
    if sys.version_info < (3, 11):
        report["python_ok"] = False
    else:
        report["python_ok"] = True
    if args.evidence:
        records, errors = load_evidence_paths([Path(item) for item in args.evidence])
        report["evidence"] = [record.evidence_id for record in records]
        report["evidence_errors"] = errors
    ok = bool(report["python_ok"])
    if args.format == "json":
        print(json.dumps(report, indent=2))
    else:
        print(f"truth-firewall {report['package']}")
        print(f"python: {report['python']} ({'ok' if ok else 'need 3.11+'})")
        print(f"platform: {report['platform']}")
        provider = report["provider"]
        print(
            f"provider: {provider['provider']} key={provider['api_key']} model={provider['model']}"
        )
        if report["evidence_errors"]:
            print("evidence errors:")
            for error in report["evidence_errors"]:
                print(f"- {error}")
        elif args.evidence:
            print(f"evidence records readable: {len(report['evidence'])}")
    return 0 if ok else 1


def _demo_command(args: argparse.Namespace) -> int:
    from truth_firewall.demo import run_demo

    return run_demo(format=args.format, runs_dir=Path(args.runs_dir))


def _read_response(value: str) -> str:
    if value == "-":
        return sys.stdin.read()
    return Path(value).read_text(encoding="utf-8")


def _print_text(result) -> None:
    print(f"run: {result.run_id}")
    print(f"extractor: {result.extractor_mode}")
    print(f"delivery: {result.delivery}")
    print(f"claims: {len(result.claims)}")
    for claim, assessment, decision in zip(result.claims, result.assessments, result.decisions, strict=False):
        print(f"\n[{claim.claim_id}] {assessment.verdict} action={decision.action}")
        print(f"  claim: {claim.normalized_claim}")
        print(f"  type: {claim.claim_type} epistemic={claim.epistemic_status}")
        if assessment.evidence_ids:
            print(f"  evidence: {', '.join(assessment.evidence_ids)}")
        print(f"  {assessment.explanation}")
    if result.errors:
        print("\nerrors:")
        for error in result.errors:
            print(f"- {error}")
    print("\nGrounded response:")
    print(result.grounded_response)
    if result.audit_dir:
        print(f"\nAudit: {result.audit_dir}")
