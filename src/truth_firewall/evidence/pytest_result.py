"""Controlled pytest invocation and result accounting.

This prevents project conftest and auto-loaded plugins from supplying the
result. It does not sandbox arbitrary code inside test functions.
"""

from __future__ import annotations

import ctypes
import os
import xml.etree.ElementTree as ET
from pathlib import Path

from truth_firewall.evidence.execution_environment import controlled_test_environment

_BOOTSTRAP = (
    "import sys, pytest; from _pytest.config import PytestPluginManager; "
    "PytestPluginManager.consider_module = lambda self, mod: None; "
    "sys.exit(pytest.main(sys.argv[1:]))"
)


def controlled_arguments(args: list[str], directory: Path, cwd: Path) -> tuple[list[str], dict[str, str]] | None:
    if len(args) >= 3 and args[1:3] == ["-m", "pytest"]:
        extra = args[3:]
    elif len(args) >= 4 and args[1:4] == ["-I", "-m", "pytest"]:
        extra = args[4:]
    else:
        return None
    # A closed invocation grammar prevents short-form plugin/config flags and
    # other project-controlled pytest options from weakening this result mode.
    harmless = {"-q", "-v", "-x", "-s", "--disable-warnings", "--capture=no",
                "--tb=short", "--tb=long"}
    targets = []
    index = 0
    while index < len(extra):
        item = extra[index]
        if item in harmless:
            index += 1
            continue
        if item in {"-k", "-m"} and index + 1 < len(extra) and not extra[index + 1].startswith("-"):
            index += 2
            continue
        candidate = Path(item.split("::", 1)[0])
        if item.startswith("-") or not candidate.exists():
            return None
        try:
            candidate.resolve(strict=True).relative_to(cwd.resolve(strict=True))
        except (OSError, RuntimeError, ValueError):
            return None
        targets.append(item)
        index += 1
    if not targets:
        extra = [*extra, str(cwd)]
    config = directory / "pytest.ini"
    config.write_text("[pytest]\naddopts =\nxfail_strict = true\n", encoding="utf-8")
    report = directory / "result.xml"
    invocation = [args[0], "-I", "-c", _BOOTSTRAP, *[_windows_short_path(item) for item in extra],
                  "--noconftest", "-p", "no:cacheprovider",
                  "-c", str(config), "--junitxml", str(report)]
    return invocation, controlled_test_environment()


def _windows_short_path(value: str) -> str:
    if os.name != "nt" or value.startswith("-") or not Path(value).exists():
        return value
    buffer = ctypes.create_unicode_buffer(32768)
    length = ctypes.windll.kernel32.GetShortPathNameW(value, buffer, len(buffer))
    return buffer.value if 0 < length < len(buffer) else value


def read_result(path: Path) -> dict[str, int] | None:
    try:
        root = ET.parse(path).getroot()
        suites = [root] if root.tag == "testsuite" else root.findall("testsuite")
        if not suites:
            return None
        totals = {key: 0 for key in ("collected", "passed", "failed", "errors", "skipped", "xfailed", "xpassed")}
        for suite in suites:
            if suite.tag != "testsuite":
                return None
            tests = int(suite.attrib["tests"])
            failed = int(suite.attrib.get("failures", "0"))
            errors = int(suite.attrib.get("errors", "0"))
            skipped = int(suite.attrib.get("skipped", "0"))
            if min(tests, failed, errors, skipped) < 0 or failed + errors + skipped > tests:
                return None
            cases = list(suite.iter("testcase"))
            if len(cases) != tests:
                return None
            observed_failed = sum(case.find("failure") is not None for case in cases)
            observed_errors = sum(case.find("error") is not None for case in cases)
            observed_skipped = sum(case.find("skipped") is not None for case in cases)
            if (observed_failed, observed_errors, observed_skipped) != (failed, errors, skipped):
                return None
            if any(sum(case.find(tag) is not None for tag in ("failure", "error", "skipped")) > 1
                   for case in cases):
                return None
            totals["collected"] += tests
            totals["passed"] += tests - failed - errors - skipped
            totals["failed"] += failed
            totals["errors"] += errors
            totals["skipped"] += skipped
            for case in cases:
                marker = case.find("skipped")
                if marker is not None and marker.attrib.get("type") == "pytest.xfail":
                    totals["xfailed"] += 1
        if totals["collected"] <= 0:
            return None
        return totals
    except (OSError, ET.ParseError, KeyError, ValueError, TypeError):
        return None
