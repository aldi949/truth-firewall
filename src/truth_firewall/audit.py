"""Local audit artifacts for one check, with contained exclusive run storage."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from truth_firewall.storage import ensure_directory, ensure_root, write_text


def write_audit(directory: Path, artifacts: dict[str, Any], *, permitted_root: Path | None = None) -> None:
    configured = permitted_root if permitted_root is not None else directory.parent
    relative = directory.relative_to(configured)
    root = ensure_root(configured)
    directory = ensure_directory(root, root / relative, exclusive=True)
    mapping = {
        "meta.json": artifacts.get("meta", {}),
        "claims.json": artifacts.get("claims", []),
        "evidence.json": artifacts.get("evidence", []),
        "candidates.json": artifacts.get("candidates", {}),
        "assessments.json": artifacts.get("assessments", []),
        "decisions.json": artifacts.get("decisions", []),
        "coverage.json": artifacts.get("coverage", []),
        "errors.json": artifacts.get("errors", []),
    }
    for name, payload in mapping.items():
        write_text(root, directory / name, json.dumps(payload, indent=2, ensure_ascii=False, default=str))
    write_text(root, directory / "response.txt", str(artifacts.get("response", "")))
    write_text(root, directory / "grounded.txt", str(artifacts.get("grounded", "")))
