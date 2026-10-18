"""Local, deterministic preparation of an editable pilot task contract."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path, PureWindowsPath

from truth_firewall.completion import acceptance_content_digest
from truth_firewall.pilot import _load_spec


def _relative_output(root: Path, directory: Path, value: str) -> str:
    path = PureWindowsPath(value)
    if not value or path.drive or path.root or ".." in path.parts:
        raise ValueError("output file must be a repository-relative path without '..'")
    candidate = root / Path(*path.parts)
    if candidate.resolve() == root or not candidate.resolve().is_relative_to(root):
        raise ValueError("output file must stay inside the current repository")
    if candidate.resolve().is_relative_to(directory.resolve()):
        raise ValueError("choose a task output file outside .truth-firewall")
    return path.as_posix()


def _value(prompt: str):
    """Prompt for a typed value without evaluating Python or executing user input."""
    kind = input(f"{prompt} type (text/number/bool/null/list): ").strip().lower()
    if kind == "null":
        return None
    if kind not in {"text", "number", "bool", "list"}:
        raise ValueError("choose text, number, bool, null, or list")
    value = input(f"{prompt}: ")
    if kind == "text":
        return value
    if kind == "bool":
        if value.lower() not in {"true", "false"}:
            raise ValueError("boolean values must be true or false")
        return value.lower() == "true"
    if kind in {"number", "list"}:
        parsed = json.loads(value)
        if kind == "number" and type(parsed) not in {int, float}:
            raise ValueError("number values must be a JSON number")
        if kind == "list" and not isinstance(parsed, list):
            raise ValueError("list values must be a JSON array, for example [1, 2]")
        # Reject overflowing floats as well as explicit non-finite constants.
        json.dumps(parsed, allow_nan=False)
        return parsed
    raise ValueError("choose text, number, bool, null, or list")


def _guided_conditions(root: Path, directory: Path) -> list[dict]:
    print("Add explicit, bounded mandatory checks. Descriptions do not prove requirement coverage.")
    print("Supported: behavior cases, Python API signatures, and file existence.")
    print("For unsupported or ambiguous requirements, stop and obtain human review; do not invent evidence.")
    conditions = []
    while True:
        description = input("Mandatory criterion (state exactly what this check will prove): ").strip()
        if not description:
            raise ValueError("a mandatory criterion must not be empty")
        kind = input("Check kind (behavior/api/file; unsupported to stop): ").strip().lower()
        if kind not in {"behavior", "api", "file"}:
            raise ValueError("unsupported check: HUMAN_REQUIRED; no contract was saved")
        path = _relative_output(root, directory, input("Checked file (repository-relative): ").strip())
        condition = {"id": f"criterion-{len(conditions) + 1}", "description": description,
                     "kind": {"behavior": "black_box", "api": "python_function", "file": "file_exists"}[kind],
                     "path": path}
        if kind == "file":
            print("File existence does not prove modification or functional behavior.")
            exists = input("Must the file exist? (yes/no): ").strip().lower()
            if exists not in {"yes", "no"}:
                raise ValueError("answer yes or no for file existence")
            condition["exists"] = exists == "yes"
        else:
            symbol = input("Python function name: ").strip()
            if not symbol.isidentifier():
                raise ValueError("provide a Python function name")
            condition["symbol"] = symbol
            if kind == "api":
                print("An API signature check does not prove functional behavior.")
                parameters = input("Parameter names, separated by commas (Enter for none): ").strip()
                names = [name.strip() for name in parameters.split(",")] if parameters else []
                if not all(name.isidentifier() for name in names) or len(set(names)) != len(names):
                    raise ValueError("parameter names must be distinct Python identifiers")
                condition["parameters"] = names
            else:
                print("Finite cases prove only those cases, not broader or exhaustive behavior.")
                cases = []
                while True:
                    count = int(input("Number of positional arguments (0 for none): ").strip())
                    if not 0 <= count <= 32:
                        raise ValueError("use between 0 and 32 positional arguments")
                    arguments = [_value(f"Argument {index + 1}") for index in range(count)]
                    cases.append({"args": arguments, "expected": _value("Expected result")})
                    another = input("Add another case? (yes/no): ").strip().lower()
                    if another not in {"yes", "no"}:
                        raise ValueError("answer yes or no when adding cases")
                    if another == "no":
                        break
                condition["cases"] = cases
        conditions.append(condition)
        more = input("Add another mandatory check? (yes/no): ").strip().lower()
        if more not in {"yes", "no"}:
            raise ValueError("answer yes or no when adding checks")
        if more == "no":
            return conditions


def init_task(*, overwrite: bool = False, guided: bool = False) -> int:
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
        conditions = _guided_conditions(root, directory) if guided else None
        required_path = ("" if guided else input(
            "Required output file (repository-relative; Enter for an editable template): ").strip())
        normalized_path = _relative_output(root, directory, required_path) if required_path else ""
        template = not guided and not required_path
        data = {
            "task_id": "local-task",
            "task_type": "python",
            "task": task,
            "acceptance_review": "unreviewed",
            "onboarding_note": (
                "GUIDED CHECKS: user-stated criteria are bounded checks, not proof of complete coverage. "
                if guided else "EDITABLE TEMPLATE: replace the placeholder condition before running. "
                if template else "STARTER CHECK ONLY: file existence does not establish behavior. "
            ) + (
                "Review every mandatory requirement. "
                "Omitted requirements cannot be inferred; coverage is not guaranteed."
            ),
            "conditions": conditions if guided else [{
                "id": "EDIT_ME_required_file" if template else "required-file",
                "kind": "file_exists",
                "path": "EDIT_ME_required_output.py" if template else normalized_path,
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
        if guided:
            print("Guided checks saved UNREVIEWED. No checks were executed and nothing was activated.")
        elif template:
            print("Editable template only: replace EDIT_ME_required_output.py before running.")
        else:
            print("Starter check only: file existence does not verify behavior or all requirements.")
        print("Created .truth-firewall/task.json")
        print("Review every mandatory requirement before running.")
        print("Next: truth-firewall activate" if guided else "Next: edit the checks, then truth-firewall activate")
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
        # Show the exact reviewable content, including all evaluation fields and
        # optional explicit obligation metadata; the digest binds this same data.
        print(json.dumps({key: value for key, value in data.items()
                          if key not in {"acceptance_review", "acceptance_digest"}},
                         indent=2, ensure_ascii=False))
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
