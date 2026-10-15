"""Inspect the built wheel and sdist against the intended source file set."""

from __future__ import annotations

import argparse
import configparser
import hashlib
import json
import re
import tarfile
import tomllib
import zipfile
from email.parser import BytesParser
from pathlib import Path, PurePosixPath


def check(dist: Path, source: Path) -> dict:
    project = tomllib.loads((source / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    version = project["version"]
    package = source / "src" / "truth_firewall"
    intended = {"truth_firewall/" + p.relative_to(package).as_posix(): p.read_bytes()
                for p in package.rglob("*") if p.is_file()
                and (p.suffix in {".py", ".json"} or p.name == "py.typed")}
    module_version = re.search(r'__version__ = "([^"]+)"', intended["truth_firewall/__init__.py"].decode())
    assert module_version and module_version[1] == version, "CLI and distribution versions differ"
    wheels = list(dist.glob("*.whl"))
    sdists = list(dist.glob("*.tar.gz"))
    assert len(wheels) == len(sdists) == 1, "expected exactly one wheel and one sdist"
    with zipfile.ZipFile(wheels[0]) as archive:
        names = archive.namelist()
        assert len(names) == len(set(names)), "duplicate wheel members"
        wheel = {name: archive.read(name) for name in names if not name.endswith("/")}
    dist_info = f"truth_firewall-{version}.dist-info/"
    allowed_metadata = {dist_info + name for name in (
        "METADATA", "WHEEL", "entry_points.txt", "top_level.txt", "RECORD", "LICENSE", "licenses/LICENSE")}
    assert set(wheel) <= set(intended) | allowed_metadata, "unexpected wheel files"
    assert {n: b for n, b in wheel.items() if n.startswith("truth_firewall/")} == intended, "wheel source/data mismatch"
    metadata = BytesParser().parsebytes(wheel[dist_info + "METADATA"])
    assert metadata["Name"] == "truth-firewall" and metadata["Version"] == version
    assert metadata["Requires-Python"] == ">=3.11"
    assert all("extra ==" in dep for dep in metadata.get_all("Requires-Dist", [])), "unexpected runtime dependency"
    assert "https://github.com/aldi949/truth-firewall" in str(metadata.get_all("Project-URL"))
    entries = configparser.ConfigParser()
    entries.read_string(wheel[dist_info + "entry_points.txt"].decode())
    assert dict(entries["console_scripts"]) == {"truth-firewall": "truth_firewall.cli:main"}
    assert b"Root-Is-Purelib: true" in wheel[dist_info + "WHEEL"]
    assert any(name.endswith("/LICENSE") for name in wheel), "license missing from wheel"

    with tarfile.open(sdists[0], "r:gz") as archive:
        members = archive.getmembers()
        assert all(member.isfile() or member.isdir() for member in members), "sdist links or special files"
        files = {}
        for member in members:
            path = PurePosixPath(member.name)
            assert not path.is_absolute() and ".." not in path.parts, "unsafe archive path"
            assert path.parts[0] == f"truth_firewall-{version}", "unexpected sdist root"
            if member.isfile():
                name = PurePosixPath(*path.parts[1:]).as_posix()
                assert name not in files, "duplicate sdist member"
                files[name] = archive.extractfile(member).read()
    required = {"src/" + name: content for name, content in intended.items()}
    assert all(files.get(name) == content for name, content in required.items()), "sdist source/data mismatch"
    source_files = {p.relative_to(source).as_posix(): p.read_bytes() for folder in ("tests", "tools", "docs")
                    for p in (source / folder).rglob("*") if p.is_file() and p.suffix in {".py", ".md"}}
    assert all(files.get(name) == content for name, content in source_files.items()), "sdist checks/docs mismatch"
    allowed = set(required) | set(source_files) | {
        "pyproject.toml", "MANIFEST.in", "LICENSE", "README.md", "QUICKSTART.md", "CONTRIBUTING.md",
        "PILOT_FEEDBACK.md", "PKG-INFO", "setup.cfg",
    } | {"src/truth_firewall.egg-info/" + name for name in (
        "PKG-INFO", "SOURCES.txt", "dependency_links.txt", "entry_points.txt", "requires.txt", "top_level.txt")}
    assert set(files) <= allowed, f"unexpected sdist files: {sorted(set(files) - allowed)}"
    assert {"pyproject.toml", "LICENSE", "README.md", "tools/check_dist.py", "tools/smoke_installed.py"} <= set(files)
    private_key = re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----\s+[A-Za-z0-9+/=\r\n]{64,}-----END")
    for name, content in {**files, **wheel}.items():
        assert not private_key.search(content), f"private key material in {name}"
    return {"version": version, "runtime_dependencies": [], "package_files": len(intended),
            "demo_fixtures": len([n for n in intended if n.startswith("truth_firewall/fixtures/demo/")]),
            "artifacts": [{"path": str(p.resolve()), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
                          for p in [wheels[0], sdists[0]]]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("dist", type=Path)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    print(json.dumps(check(args.dist, args.source), indent=2))
