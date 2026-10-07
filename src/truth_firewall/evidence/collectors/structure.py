"""Bounded, first-party structural observations of local source and manifests."""

from __future__ import annotations

import ast
import hashlib
import re
import tomllib
from dataclasses import replace
from pathlib import Path

from truth_firewall.evidence.collectors.filesystem import FilesystemCollector
from truth_firewall.evidence.provenance import attest_record
from truth_firewall.safety import is_secret_path, resolve_under_root
from truth_firewall.schemas import EvidenceRecord, json_dumps


class StructureCollector:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.files = FilesystemCollector(self.root)

    def observe(self, terms: dict[str, object], *, evidence_id: str) -> EvidenceRecord:
        user_path = str(terms["target_path"])
        path = resolve_under_root(self.root, user_path)
        if is_secret_path(path) or not path.is_file() or path.stat().st_size > 1_000_000:
            raise ValueError("source is unavailable for bounded inspection")
        raw = path.read_bytes()
        if len(raw) > 1_000_000 or b"\x00" in raw:
            raise ValueError("source exceeds structural inspection limits")
        if path.suffix.lower() == ".py" and "query_kind" in terms:
            observed, complete = self._python(raw.decode("utf-8"), terms)
            kind = "source_structure"
        elif "package" in terms and path.suffix.lower() in {".txt", ".toml"}:
            observed, complete = self._dependency(path, raw.decode("utf-8"), terms)
            kind = "dependency_declaration"
        else:
            raise ValueError("unsupported structural source format")
        record = self.files.observe(user_path, evidence_id=evidence_id)
        if record.file_hash != hashlib.sha256(raw).hexdigest():
            raise ValueError("source changed during observation")
        payload = record.structured_payload()
        payload.update(kind=kind, query={key: value for key, value in terms.items() if key != "target_path"},
                       observed=observed, complete=complete)
        return attest_record(replace(record, payload_json=json_dumps(payload)))

    @staticmethod
    def _python(text: str, terms: dict[str, object]) -> tuple[bool, bool]:
        tree = ast.parse(text)
        symbol = str(terms["symbol"])
        query = str(terms["query_kind"])
        if not symbol.isidentifier() and query != "import":
            raise ValueError("symbol is not an identifier")
        nodes = tree.body
        classes = [node for node in nodes if isinstance(node, ast.ClassDef) and node.name == symbol]
        functions = [node for node in nodes if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                     and node.name == symbol]
        dynamic = any(isinstance(node, (ast.ImportFrom, ast.Call)) and (
            isinstance(node, ast.ImportFrom) and any(alias.name == "*" for alias in node.names)
            or isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id in {"exec", "eval", "setattr"}) for node in ast.walk(tree))
        if query == "class":
            return bool(classes), not dynamic
        if query == "function":
            return bool(functions), not dynamic
        if query == "definition":
            return bool(classes or functions), not dynamic
        if query == "method" or query == "signature" and "class_name" in terms:
            owner = terms.get("class_name")
            if not isinstance(owner, str) or not owner.isidentifier():
                raise ValueError("class name is not an identifier")
            owners = [node for node in nodes if isinstance(node, ast.ClassDef) and node.name == owner]
            if len(owners) != 1:
                return False, not dynamic and not owners
            methods = [node for node in owners[0].body
                       if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == symbol]
            if query == "method":
                return bool(methods), not dynamic
            functions = methods
        if query == "signature":
            if len(functions) != 1:
                return False, not dynamic and not functions
            args = functions[0].args
            if args.vararg or args.kwarg:
                return False, False
            count = len(args.posonlyargs) + len(args.args) + len(args.kwonlyargs)
            return count == terms["count"], not dynamic
        if query == "import":
            imports: set[str] = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imports.update(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imports.add(node.module)
            return symbol in imports, not dynamic
        if query == "constant":
            values: list[object] = []
            for node in nodes:
                assigned = []
                if isinstance(node, ast.Assign):
                    assigned = [target.id for target in node.targets if isinstance(target, ast.Name)]
                elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                    assigned = [node.target.id]
                if symbol in assigned:
                    try:
                        values.append(ast.literal_eval(node.value))
                    except (ValueError, TypeError, SyntaxError, MemoryError):
                        return False, False
            if len(values) != 1 or type(values[0]) not in {str, int, float, bool, type(None)}:
                return False, not dynamic and not values
            wanted = terms["value"]
            return type(values[0]) is type(wanted) and values[0] == wanted, not dynamic
        raise ValueError("unsupported source query")

    @staticmethod
    def _dependency(path: Path, text: str, terms: dict[str, object]) -> tuple[bool, bool]:
        if path.suffix.lower() == ".toml":
            data = tomllib.loads(text)
            items = data.get("project", {}).get("dependencies", [])
            if not isinstance(items, list) or not all(isinstance(item, str) for item in items):
                return False, False
        else:
            items = [line.strip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]
        parsed = []
        for item in items:
            match = re.fullmatch(r"([A-Za-z][A-Za-z0-9_.-]*)(==|>=|<=|~=|!=)([A-Za-z0-9][A-Za-z0-9_.+!-]*)", item)
            if not match:
                return False, False
            parsed.append((match[1].lower().replace("_", "-"), match[2], match[3]))
        wanted = (terms["package"], terms["operator"], terms["version"])
        return wanted in parsed, True
