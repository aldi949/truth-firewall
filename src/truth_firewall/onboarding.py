"""Local, deterministic preparation of an editable pilot task contract."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path, PureWindowsPath

from truth_firewall.completion import acceptance_content_digest
from truth_firewall.pilot import _load_spec


def init_task(*, overwrite: bool = False) -> int:
    root = Path.cwd().resolve()
    directory = root / ".truth-firewall"
    target = directory / "task.json"
    temporary: Path | None = None
    try:
        # Do not follow a configuration link outside the repository.
        if directory.is_symlink() or directory.resolve() != directory or target.is_symlink():
            raise ValueError(".truth-firewall and task.json must not be symbolic links or redirected paths")
        if target.exists() and not overwrite:
            print("task.json already exists; nothing was changed. Use init --overwrite to replace it.",
                  file=sys.stderr)
            return 1
        print("Truth Firewall cannot infer omitted requirements or guarantee complete task coverage.")
        task = input("Task text: ").strip()
        if not task:
            raise ValueError("task text must not be empty")
        required_path = input("Required output file (repository-relative; Enter for an editable template): ").strip()
        if required_path:
            path = PureWindowsPath(required_path)
            if path.drive or path.root or ".." in path.parts:
                raise ValueError("output file must be a repository-relative path without '..'")
            candidate = root / Path(*path.parts)
            if candidate.resolve() == root or not candidate.resolve().is_relative_to(root):
                raise ValueError("output file must stay inside the current repository")
            if candidate.resolve().is_relative_to(directory.resolve()):
                raise ValueError("choose a task output file outside .truth-firewall")
        template = not required_path
        data = {
            "task_id": "local-task",
            "task_type": "python",
            "task": task,
            "acceptance_review": "unreviewed",
            "onboarding_note": (
                "EDITABLE TEMPLATE: replace the placeholder condition before running. "
                if template else "STARTER CHECK ONLY: file existence does not establish behavior. "
            ) + (
                "Review every mandatory requirement. "
                "Omitted requirements cannot be inferred; coverage is not guaranteed."
            ),
            "conditions": [{
                "id": "EDIT_ME_required_file" if template else "required-file",
                "kind": "file_exists",
                "path": "EDIT_ME_required_output.py" if template else path.as_posix(),
                "exists": True,
            }],
        }
        directory.mkdir(exist_ok=True)
        # Validate the serialized candidate through the same loader as run, before
        # touching an existing contract. Explicit overwrite uses an atomic replace.
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory,
                                         prefix=".init-", suffix=".json", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
        _load_spec(temporary)
        if overwrite:
            os.replace(temporary, target)
        else:
            # Exclusive creation also protects a task created since the first check.
            with target.open("x", encoding="utf-8") as stream:
                stream.write(temporary.read_text(encoding="utf-8"))
        _load_spec(target)
        if template:
            print("Editable template only: replace EDIT_ME_required_output.py before running.")
        else:
            print("Starter check only: file existence does not verify behavior or all requirements.")
        print("Created .truth-firewall/task.json")
        print("Review every mandatory requirement before running.")
        print("Next: edit the checks, then truth-firewall activate")
        return 0
    except (OSError, ValueError, EOFError, KeyboardInterrupt) as exc:
        print(f"error: init could not prepare task.json: {exc}", file=sys.stderr)
        return 1
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def activate_task(spec_value: str = ".truth-firewall/task.json") -> int:
    """Require a deliberate human review of a generated starter contract."""
    temporary: Path | None = None
    try:
        target = Path(spec_value).absolute()
        if target.is_symlink():
            raise ValueError("task contract must not be a symbolic link")
        original = target.read_bytes()
        data, contract = _load_spec(target)
        gaps = contract.readiness_gaps()
        if any("EDIT_ME" in gap for gap in gaps):
            raise ValueError("replace every EDIT_ME condition before activation")
        if contract.acceptance_review not in {"unreviewed", "reviewed"}:
            raise ValueError("this contract is not an init starter requiring activation")
        print(f"Original task: {contract.original_task}")
        print("Mandatory acceptance checks:")
        for condition in contract.conditions:
            print(f"- {condition.condition_id}: {condition.kind} {condition.path}")
        print("Review and edit these checks for every mandatory requirement.")
        print("Explicit review does not prove coverage; omitted requirements cannot be inferred.")
        if input("Type ACTIVATE to confirm your review: ").strip() != "ACTIVATE":
            print("Contract remains unreviewed.")
            return 2
        if target.read_bytes() != original:
            raise ValueError("task contract changed during review; review it again")
        data["acceptance_review"] = "reviewed"
        data["acceptance_digest"] = acceptance_content_digest(data)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=target.parent,
                                         prefix=".activate-", suffix=".json", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
        _load_spec(temporary)
        os.replace(temporary, target)
        print(f"Activated {target}. Next: truth-firewall run --spec {spec_value}")
        return 0
    except (OSError, ValueError, EOFError, KeyboardInterrupt) as exc:
        print(f"error: activation failed: {exc}", file=sys.stderr)
        return 1
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
