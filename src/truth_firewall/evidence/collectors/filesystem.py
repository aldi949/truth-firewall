"""Direct filesystem observations. These are trust level A."""

from __future__ import annotations

import hashlib
from pathlib import Path

from truth_firewall.constants import MAX_SNAPSHOT_CHARS
from truth_firewall.evidence.provenance import attest_record, file_state, is_attested, workspace_snapshot
from truth_firewall.safety import is_secret_path, resolve_under_root, truncate_text
from truth_firewall.schemas import EvidenceRecord, EvidenceType, TrustLevel, json_dumps, utc_now
from truth_firewall.workspace import same_workspace


class FilesystemCollector:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def observe(
        self,
        user_path: str,
        *,
        before_hash: str | None = None,
        existed_before: bool | None = None,
        snapshot: bool = False,
        evidence_id: str = "fs-observe",
    ) -> EvidenceRecord:
        path = resolve_under_root(self.root, user_path)
        exists = path.exists()
        secret = is_secret_path(path)
        file_hash = None
        excerpt = None
        size = None
        if exists and path.is_file() and not secret:
            data = path.read_bytes()
            size = len(data)
            file_hash = hashlib.sha256(data).hexdigest()
            if snapshot and b"\x00" not in data:
                text, truncated = truncate_text(data.decode("utf-8", errors="replace"), MAX_SNAPSHOT_CHARS)
                excerpt = text
                if truncated:
                    excerpt_truncated = True
                else:
                    excerpt_truncated = False
            else:
                excerpt_truncated = False
        else:
            excerpt_truncated = False
            if exists and path.is_file():
                size = path.stat().st_size
        try:
            relative = str(path.relative_to(self.root))
        except ValueError:
            relative = path.name
        payload = {
            "kind": "snapshot",
            "exists": exists,
            "path": relative.replace("\\", "/"),
            "is_file": path.is_file() if exists else False,
            "size": size,
            "content_omitted": secret,
            "excerpt_truncated": excerpt_truncated,
            "existed_before": existed_before,
            "observed_state": file_state(self.root, relative.replace("\\", "/")),
        }
        if excerpt is not None and not secret:
            payload["excerpt"] = excerpt
        return attest_record(EvidenceRecord(
            evidence_id=evidence_id,
            evidence_type=EvidenceType.FILESYSTEM.value,
            trust_level=TrustLevel.A.value,
            source="filesystem",
            timestamp=utc_now().isoformat(),
            command=None,
            cwd=str(self.root),
            exit_code=None,
            stdout="",
            stderr="",
            file_path=relative.replace("\\", "/"),
            file_hash=file_hash,
            before_hash=before_hash,
            after_hash=file_hash,
            git_metadata_json=json_dumps({}),
            payload_json=json_dumps(payload),
            provenance=f"filesystem:{relative}",
        ))

    def observe_change(self, before: EvidenceRecord, *, evidence_id: str = "fs-change") -> EvidenceRecord:
        """Prove a transition from two independently captured snapshots."""
        from dataclasses import replace

        if not is_attested(before) or before.evidence_type != EvidenceType.FILESYSTEM.value:
            raise ValueError("before state must be a first-party filesystem observation")
        if not same_workspace(before.workspace_root, str(self.root)):
            raise ValueError("before state has a different workspace")
        if not before.file_path:
            raise ValueError("before state has no path")
        old = before.structured_payload().get("observed_state")
        after = self.observe(before.file_path, evidence_id=evidence_id)
        new = after.structured_payload().get("observed_state")
        if not isinstance(old, dict) or not isinstance(new, dict) or old == new:
            raise ValueError("no observed file transition")
        if not old.get("exists") and new.get("exists"):
            change = "created"
        elif old.get("exists") and not new.get("exists"):
            change = "deleted"
        else:
            change = "edited"
        payload = after.structured_payload()
        payload.update(kind="file_edit", change_type=change, transition_observed=True,
                       before_state=old, after_state=new)
        return attest_record(replace(after, payload_json=json_dumps(payload), before_hash=old.get("hash")))

    def search_literal(self, query: str, scope: str = ".", *, evidence_id: str = "fs-search") -> EvidenceRecord:
        """Search decodable text files; any opaque entry makes absence incomplete."""
        import os

        if not isinstance(query, str) or not query or len(query) > 256:
            raise ValueError("search query must be a bounded literal")
        target = resolve_under_root(self.root, scope)
        if not target.is_dir():
            raise ValueError("search scope must be a directory")
        before = workspace_snapshot(self.root, include_generated=True)
        if before is None:
            raise ValueError("workspace cannot be snapshotted completely")
        matches = 0
        files = 0
        skipped: list[str] = []
        encodings: set[str] = set()
        def walk_error(error: OSError) -> None:
            skipped.append(str(error.filename or "unreadable-directory"))

        for current, dirs, names in os.walk(target, followlinks=False, onerror=walk_error):
            for name in dirs:
                if (Path(current) / name).is_symlink():
                    skipped.append((Path(current) / name).relative_to(self.root).as_posix())
            dirs[:] = [name for name in dirs if not (Path(current) / name).is_symlink()]
            for name in names:
                path = Path(current) / name
                if path.is_symlink() or not path.is_file():
                    skipped.append(path.relative_to(self.root).as_posix())
                    continue
                files += 1
                try:
                    if files > 20_000 or path.stat().st_size > 128 * 1024 * 1024:
                        skipped.append(path.relative_to(self.root).as_posix())
                        continue
                    data = path.read_bytes()
                    if data.startswith(b"\xff\xfe"):
                        encoding = "utf-16-le"
                        content = data[2:].decode(encoding)
                    elif data.startswith(b"\xfe\xff"):
                        encoding = "utf-16-be"
                        content = data[2:].decode(encoding)
                    elif b"\x00" not in data:
                        encoding = "utf-8"
                        content = data.decode("utf-8-sig")
                    else:
                        skipped.append(path.relative_to(self.root).as_posix())
                        continue
                except (OSError, UnicodeError):
                    skipped.append(path.relative_to(self.root).as_posix())
                    continue
                encodings.add(encoding)
                if query in content:
                    matches += 1
        after = workspace_snapshot(self.root, include_generated=True)
        if after is None or before != after:
            raise ValueError("workspace changed during search")
        relative = target.relative_to(self.root).as_posix()
        payload = {"kind": "search", "query": query, "root": relative, "match_count": matches,
                   "complete": not skipped, "covers_workspace": target == self.root,
                   "files_seen": files, "files_skipped": skipped, "supported_encodings": sorted(encodings),
                   "workspace_snapshot": after, "include_generated": True}
        return attest_record(EvidenceRecord(
            evidence_id=evidence_id, evidence_type=EvidenceType.FILESYSTEM.value, trust_level=TrustLevel.A.value,
            source="filesystem", timestamp=utc_now().isoformat(), command=None, cwd=str(self.root),
            exit_code=None, stdout="", stderr="", file_path=None, file_hash=None, before_hash=None,
            after_hash=None, git_metadata_json=json_dumps({}), payload_json=json_dumps(payload),
            provenance="filesystem:literal-search", workspace_root=str(self.root),
        ))

    def observe_config(self, user_path: str, key: str, *, evidence_id: str = "fs-config") -> EvidenceRecord:
        """Parse one JSON/TOML scalar from a directly observed file."""
        import json
        import tomllib
        from dataclasses import replace

        if not key or any(not part.isidentifier() for part in key.split(".")):
            raise ValueError("config key must be a dotted identifier")
        path = resolve_under_root(self.root, user_path)
        if is_secret_path(path) or path.suffix.lower() not in {".json", ".toml"}:
            raise ValueError("config format cannot be observed")
        raw = path.read_bytes()
        if len(raw) > 1_000_000:
            raise ValueError("config file exceeds observation limit")
        if path.suffix.lower() == ".toml":
            data = tomllib.loads(raw.decode("utf-8"))
        else:
            def unique(pairs):
                result = {}
                for name, value in pairs:
                    if name in result:
                        raise ValueError("duplicate config key")
                    result[name] = value
                return result
            data = json.loads(raw.decode("utf-8"), object_pairs_hook=unique)
        value = data
        for part in key.split("."):
            if not isinstance(value, dict) or part not in value:
                raise ValueError("config key does not exist")
            value = value[part]
        if type(value) not in {str, int, float, bool, type(None)}:
            raise ValueError("config value is not a scalar")
        observed = self.observe(user_path, evidence_id=evidence_id)
        payload = observed.structured_payload()
        payload.update(kind="config_value", key=key, value=value)
        return attest_record(replace(observed, payload_json=json_dumps(payload)))
