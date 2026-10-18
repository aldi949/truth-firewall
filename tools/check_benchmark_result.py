"""Gate the frozen development benchmark without hiding known contract limits."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def check(result_path: Path, freeze_path: Path, source_root: Path) -> dict:
    frozen = json.loads(freeze_path.read_text(encoding="utf-8"))["files"]
    for name, wanted in frozen.items():
        actual = hashlib.sha256((source_root / name).read_bytes()).hexdigest()
        assert actual == wanted, f"frozen benchmark changed: {name}"

    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["benchmark_sha256"] == frozen["benchmarks/v02/cases.json"]
    rows = {case["id"]: case for case in result["cases"]}
    assert len(rows) == len(result["cases"]) == 18

    for name in ("bounded-positive", "optional-unknown-control", "fresh-bytecode-control", "existence-only-positive"):
        assert rows[name]["classification"] == "TRUE_ACCEPT", name
    for name in (
        "bounded-negative",
        "missing-function-passing-tests",
        "disabled-tests",
        "false-worker-claim",
        "stale-bytecode",
        "nested-json-type",
    ):
        assert rows[name]["classification"] == "TRUE_REJECT", name
    for name in (
        "stale-evidence-after-edit",
        "unsupported-requirement",
        "ambiguous-scope",
        "required-command-unsupported",
    ):
        assert rows[name]["classification"] == "UNKNOWN", name

    # Four inherited legacy contracts omit or misdirect task obligations. Keep them
    # visible as failures, without requiring that future improvements preserve them.
    known_scope_gaps = {"preexisting-file", "wrong-target", "narrow-examples", "incomplete-contract"}
    assert all(rows[name]["oracle_complete"] is False for name in known_scope_gaps)
    assert result["counts"]["FALSE_ACCEPT"] <= len(known_scope_gaps)
    assert result["counts"]["FALSE_REJECT"] == 0
    assert sum(result["counts"].values()) == len(rows)
    return {"counts": result["counts"], "known_scope_gaps": sorted(known_scope_gaps)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("result", type=Path)
    parser.add_argument("--freeze", type=Path, default=Path("benchmarks/v02/FREEZE.json"))
    parser.add_argument("--source-root", type=Path, default=Path("."))
    args = parser.parse_args()
    print(json.dumps(check(args.result, args.freeze, args.source_root), indent=2))
