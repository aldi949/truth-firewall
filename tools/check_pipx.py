"""Check pipx lifecycle using only local Truth Firewall artifacts and private pipx paths."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import zipfile
from email.parser import BytesParser
from pathlib import Path


def wheel_version(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        metadata = next(name for name in archive.namelist() if name.endswith(".dist-info/METADATA"))
        info = BytesParser().parsebytes(archive.read(metadata))
        assert info["Name"] == "truth-firewall"
        return info["Version"]


def check(wheel: Path, work: Path, upgrade_wheel: Path | None) -> dict:
    wheel = wheel.resolve(strict=True)
    work.mkdir(parents=True, exist_ok=False)
    work = work.resolve()
    env = {**os.environ, "PIPX_HOME": str(work / "pipx home"), "PIPX_BIN_DIR": str(work / "bin"),
           "PIPX_MAN_DIR": str(work / "man"), "PIPX_DEFAULT_PYTHON": sys.executable,
           "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    env.pop("PYTHONPATH", None)
    observations = []

    def pipx(label, *args):
        result = subprocess.run([sys.executable, "-m", "pipx", *args], env=env, cwd=work,
                                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=240)
        (work / f"{label}.log").write_text(result.stdout + result.stderr, encoding="utf-8")
        assert result.returncode == 0, f"{label}: {result.stdout} {result.stderr}"
        observations.append(label)
        return result.stdout

    exe = work / "bin" / ("truth-firewall.exe" if os.name == "nt" else "truth-firewall")
    app_python = work / "pipx home" / "venvs" / "truth-firewall" / (
        "Scripts/python.exe" if os.name == "nt" else "bin/python")

    def version(wanted):
        result = subprocess.run([str(exe), "--version"], cwd=work, env=env,
                                capture_output=True, text=True, timeout=30)
        assert result.returncode == 0 and result.stdout.strip() == f"truth-firewall {wanted}"

    pipx("install", "install", str(wheel), "--python", sys.executable, "--pip-args=--no-index")
    wanted = wheel_version(wheel)
    version(wanted)
    pipx("installed-metadata", "list", "--json")
    smoke = subprocess.run([sys.executable, str(Path(__file__).with_name("smoke_installed.py")),
                            "--exe", str(exe), "--python", str(app_python), "--work", str(work / "smoke")],
                           cwd=work, env=env, capture_output=True, text=True, encoding="utf-8", timeout=180)
    (work / "smoke.log").write_text(smoke.stdout + smoke.stderr, encoding="utf-8")
    assert smoke.returncode == 0, smoke.stdout + smoke.stderr
    pipx("reinstall", "reinstall", "truth-firewall", "--python", sys.executable)
    version(wanted)
    pipx("upgrade-same-artifact", "upgrade", "truth-firewall", "--pip-args=--no-index")
    version(wanted)
    pipx("uninstall", "uninstall", "truth-firewall")
    assert not exe.exists() and not app_python.exists(), "pipx uninstall left app installed"
    upgraded = "NOT_RUN"
    if upgrade_wheel:
        upgrade_wheel = upgrade_wheel.resolve(strict=True)
        # Name-based local resolution exercises an actual version-changing upgrade, without PyPI.
        pipx("install-local-name", "install", "truth-firewall", "--python", sys.executable,
             "--pip-args", f'--no-index --find-links "{wheel.parent}"')
        version(wanted)
        pipx("upgrade-local-version", "upgrade", "truth-firewall", "--pip-args",
             f'--no-index --find-links "{upgrade_wheel.parent}"')
        version(wheel_version(upgrade_wheel))
        upgraded = wheel_version(upgrade_wheel)
        pipx("uninstall-upgrade", "uninstall", "truth-firewall")
        assert not exe.exists() and not app_python.exists()
    report = {"wheel": str(wheel), "version": wanted, "pipx_paths": str(work),
              "checks": observations, "version_changing_upgrade": upgraded, "uninstalled": True}
    (work / "results.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--upgrade-wheel", type=Path, help="Test-only later version built separately; never published")
    args = parser.parse_args()
    print(json.dumps(check(args.wheel, args.work, args.upgrade_wheel), indent=2))
