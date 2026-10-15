"""Exercise an installed executable from disposable directories outside the source tree.

No real Codex worker runs unless --real-codex is explicitly supplied.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path


def smoke(executable: Path, python: Path, work: Path, real_codex: bool) -> dict:
    work.mkdir(parents=True, exist_ok=False)
    executable, python, work = executable.resolve(), python.resolve(), work.resolve()
    observations = []
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    environment["PYTHONIOENCODING"] = "utf-8"
    environment.pop("TRUTH_FIREWALL_CODEX_JS", None)
    missing_runtime = {**environment, "TRUTH_FIREWALL_CODEX_JS": str(work / "absent-codex.js")}

    def invoke(label, args, cwd=work, *, stdin=None, env=environment, expected=0, timeout=30):
        result = subprocess.run([str(executable), *args], cwd=cwd, input=stdin, env=env,
                                capture_output=True, text=True, encoding="utf-8", errors="replace",
                                timeout=timeout, check=False)
        (work / f"{label}.stdout.txt").write_text(result.stdout, encoding="utf-8")
        (work / f"{label}.stderr.txt").write_text(result.stderr, encoding="utf-8")
        observations.append({"label": label, "args": args, "exit": result.returncode})
        assert result.returncode == expected, f"{label}: exit {result.returncode}: {result.stdout} {result.stderr}"
        return result.stdout

    help_text = invoke("help", ["--help"])
    assert all(command in help_text for command in ("init", "activate", "run", "demo"))
    version = invoke("version", ["--version"]).strip()
    demo = json.loads(invoke("demo", ["demo", "--format", "json"]))
    assert demo["banner"] == "DEMO FIXTURES - NOT LIVE PRODUCT ACCURACY"
    assert {item["name"] for item in demo["fixtures"]} == {
        "contradicted", "legitimate_hypothesis", "unsupported_causal", "supported_factual",
        "scoped_absence", "overclaimed_completion", "mixed", "local_observation",
    }, "missing bundled demo fixtures"
    assert all(not item["errors"] for item in demo["fixtures"]), "demo reported an execution error"

    placeholder = work / "placeholder project"
    placeholder.mkdir()
    invoke("init-placeholder", ["init"], placeholder, stdin="Return 42 from active.py.\n\n")
    (placeholder / "EDIT_ME_required_output.py").write_text("# not the task\n", encoding="utf-8")
    output = invoke("placeholder-blocked", ["run", "--attempts", "1"], placeholder,
                    env=missing_runtime, expected=2)
    assert "HUMAN_REQUIRED" in output and "Codex was not started" in output
    invoke("placeholder-activation-refused", ["activate"], placeholder, expected=1)

    project = work / "reviewed project"
    project.mkdir()
    active = project / "active.py"
    active.write_text("def answer(): return 42\n", encoding="utf-8")
    invoke("init-concrete", ["init"], project,
           stdin="In active.py, answer() must return the integer 42.\nactive.py\n")
    spec = project / ".truth-firewall" / "task.json"
    output = invoke("unreviewed-blocked", ["run", "--spec", str(spec), "--attempts", "1"], project,
                    env=missing_runtime, expected=2)
    assert "HUMAN_REQUIRED" in output and "Codex was not started" in output
    data = json.loads(spec.read_text(encoding="utf-8"))
    data["conditions"] = [{"id": "answer", "kind": "black_box", "path": "active.py", "symbol": "answer",
                           "cases": [{"args": [], "expected": 42}]}]
    spec.write_text(json.dumps(data), encoding="utf-8")
    invoke("activate", ["activate", "--spec", str(spec)], project, stdin="ACTIVATE\n")

    # The interpreter is the installed app's isolated environment, not this script's interpreter.
    verifier = (
        "import json,sys; from pathlib import Path; "
        "from truth_firewall.pilot import _load_spec; "
        "from truth_firewall.completion import TaskCompletionVerifier; "
        "root=Path(sys.argv[1]); _,c=_load_spec(root/'.truth-firewall'/'task.json'); "
        "print(TaskCompletionVerifier(root,c).attempt_done('HOST_VERIFIER_ONLY').decision.value)"
    )

    def verify(label, wanted):
        result = subprocess.run([str(python), "-I", "-c", verifier, str(project)], cwd=work,
                                env=environment, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0 and result.stdout.strip() == wanted, (result.stdout, result.stderr)
        observations.append({"label": label, "decision": wanted, "real_worker": False})

    verify("reviewed-positive", "VERIFIED_DONE")
    active.write_text("def answer(): return 0\n", encoding="utf-8")
    verify("reviewed-negative", "REJECT_DONE")
    active.write_text("def answer(): return 42\n", encoding="utf-8")
    data = json.loads(spec.read_text(encoding="utf-8"))
    data["task"] = "In active.py, answer() must return the integer 99."
    spec.write_text(json.dumps(data), encoding="utf-8")
    verify("edited-review", "HUMAN_REQUIRED")
    output = invoke("edited-blocked", ["run", "--spec", str(spec), "--attempts", "1"], project,
                    env=missing_runtime, expected=2)
    assert "review is invalid" in output and "Codex was not started" in output
    summaries = list((project / ".truth-firewall" / "runs").glob("*/summary.json"))
    assert len(summaries) == 2
    assert all(json.loads(p.read_text(encoding="utf-8"))["attempts"] == 0 for p in summaries)
    data["task"] = "In active.py, answer() must return the integer 42."
    spec.write_text(json.dumps(data), encoding="utf-8")
    invoke("reactivate", ["activate", "--spec", str(spec)], project, stdin="ACTIVATE\n")
    output = invoke("missing-codex", ["run", "--attempts", "1"], project, env=missing_runtime, expected=1)
    assert "ERROR" in output and ("Codex CLI" in output or "Node.js" in output)
    assert "execution/setup error" in output
    if real_codex:
        output = invoke("real-codex", ["run", "--attempts", "1"], project, timeout=350)
        assert "VERIFIED_DONE" in output and "Attempts used: 1" in output
    report = {"version": version, "installed_executable": str(executable), "work": str(work),
              "real_codex": "PASS" if real_codex else "NOT_RUN", "observations": observations}
    (work / "results.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--exe", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True, help="New disposable directory outside the source checkout")
    parser.add_argument("--real-codex", action="store_true")
    args = parser.parse_args()
    print(json.dumps(smoke(args.exe, args.python, args.work, args.real_codex), indent=2))
