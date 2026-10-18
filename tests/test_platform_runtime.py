from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from truth_firewall.platform_runtime import discover_codex, process_group_options, terminate_process_tree
from truth_firewall.supervised_worker import WorkerProcessSpec, WorkerProcessStatus, run_worker_process


def locator(paths):
    return lambda name: str(paths[name]) if name in paths else None


@pytest.mark.parametrize("layout", ["Windows npm", "macOS Intel npm", "macOS ARM npm", "Ubuntu npm"])
def test_explicit_javascript_supports_spaces_and_unicode(tmp_path, layout):
    root = tmp_path / layout / "安装"
    root.mkdir(parents=True)
    js = root / "codex.js"
    js.write_text("// deterministic fixture", encoding="utf-8")
    node = root / "node"
    node.touch()
    runtime = discover_codex(environment={"TRUTH_FIREWALL_CODEX_JS": str(js)}, which=locator({"node": node}))
    assert runtime.command == (str(node.resolve()), str(js.resolve()))


def test_invalid_override_does_not_fall_back(tmp_path):
    native = tmp_path / "codex"
    native.touch()
    with pytest.raises(RuntimeError, match="JavaScript entry point"):
        discover_codex(environment={"TRUTH_FIREWALL_CODEX_JS": str(tmp_path / "missing.js")},
                       which=locator({"codex": native}))


def test_native_install_does_not_require_node(tmp_path):
    native = tmp_path / ("codex.exe" if os.name == "nt" else "codex")
    native.touch()
    assert discover_codex(environment={}, which=locator({native.name: native})).command == (str(native.resolve()),)


@pytest.mark.skipif(os.name != "nt", reason="Windows CreateProcess and npm wrapper behavior")
def test_windows_prefers_native_exe_over_extensionless_npm_launcher(tmp_path):
    npm = tmp_path / "npm"
    npm.mkdir()
    launcher = npm / "codex"
    launcher.touch()
    native = tmp_path / "codex.exe"
    native.touch()
    assert discover_codex(environment={}, which=locator({"codex": launcher, "codex.exe": native})).command == (
        str(native.resolve()),)


@pytest.mark.skipif(os.name != "nt", reason="Windows CreateProcess and npm wrapper behavior")
def test_windows_extensionless_npm_launcher_uses_node(tmp_path):
    launcher = tmp_path / "codex"
    launcher.touch()
    js = tmp_path / "node_modules" / "@openai" / "codex" / "bin" / "codex.js"
    js.parent.mkdir(parents=True)
    js.touch()
    node = tmp_path / "node.exe"
    node.touch()
    assert discover_codex(environment={}, which=locator({"codex": launcher, "node.exe": node})).command == (
        str(node.resolve()), str(js.resolve()))


def test_windows_npm_wrapper_launches_javascript_without_shell(tmp_path):
    wrapper = tmp_path / "codex.cmd"
    wrapper.touch()
    js = tmp_path / "node_modules" / "@openai" / "codex" / "bin" / "codex.js"
    js.parent.mkdir(parents=True)
    js.touch()
    node = tmp_path / "node.exe"
    node.touch()
    assert discover_codex(environment={}, which=locator({"codex": wrapper, "node.exe": node})).command == (
        str(node.resolve()), str(js.resolve()))


def test_windows_appdata_fallback_preserves_existing_install(tmp_path):
    js = tmp_path / "npm" / "node_modules" / "@openai" / "codex" / "bin" / "codex.js"
    js.parent.mkdir(parents=True)
    js.touch()
    node = tmp_path / "node.exe"
    node.touch()
    runtime = discover_codex(environment={"APPDATA": str(tmp_path)}, which=locator({"node.exe": node}))
    assert runtime.command == (str(node.resolve()), str(js.resolve()))


@pytest.mark.skipif(os.name == "nt", reason="POSIX npm symlink layout; Windows symlink privileges vary")
def test_posix_npm_symlink_resolves_to_javascript(tmp_path):
    js = tmp_path / "lib" / "node_modules" / "@openai" / "codex" / "bin" / "codex.js"
    js.parent.mkdir(parents=True)
    js.touch()
    link = tmp_path / "codex"
    link.symlink_to(js)
    node = tmp_path / "node"
    node.touch()
    assert discover_codex(environment={}, which=locator({"codex": link, "node": node})).command == (
        str(node.resolve()), str(js.resolve()))


def test_absent_runtime_and_missing_node_are_setup_errors(tmp_path):
    with pytest.raises(RuntimeError, match="Codex CLI was not found"):
        discover_codex(environment={}, which=locator({}))
    js = tmp_path / "codex.js"
    js.touch()
    with pytest.raises(RuntimeError, match="Node.js was not found"):
        discover_codex(environment={"TRUTH_FIREWALL_CODEX_JS": str(js)}, which=locator({}))


@pytest.mark.parametrize("name", ["python", "python.exe", "python3", "python3.11", "python3.14"])
def test_versioned_python_invocations_supported(name):
    assert WorkerProcessSpec((name, "-I", "worker.py")).command[0] == name


@pytest.mark.parametrize("name", ["python-malware", "python3x", "python2", "sh"])
def test_other_interpreters_remain_rejected(name):
    with pytest.raises(ValueError, match="isolated Python script"):
        WorkerProcessSpec((name, "-I", "worker.py"))


def test_process_groups_match_current_operating_system():
    options = process_group_options()
    assert options["start_new_session"] is (os.name != "nt")
    assert (options["creationflags"] != 0) is (os.name == "nt")


def adapter_run(tmp_path, source, *, timeout=10):
    workspace = tmp_path / "task 安装"
    workspace.mkdir()
    fake = tmp_path / "fake_codex.py"
    fake.write_text(source, encoding="utf-8")
    adapter = Path(__file__).resolve().parents[1] / "src" / "truth_firewall" / "codex_worker.py"
    env = {"TF_PILOT_RAW_ROOT": str(tmp_path / "raw"),
           "TF_CODEX_COMMAND": json.dumps([sys.executable, "-I", str(fake)])}
    result = run_worker_process(WorkerProcessSpec((sys.executable, "-I", str(adapter)),
                                                environment=env, timeout_seconds=timeout),
                                task_run_id="deterministic-platform", attempt_number=1,
                                workspace=workspace, task_instructions="Create answer() returning 42.",
                                artifact_root=tmp_path / "supervisor")
    return result, workspace


def test_deterministic_codex_adapter_is_not_authenticated_e2e(tmp_path):
    result, _ = adapter_run(tmp_path, """import sys
from pathlib import Path
args = sys.argv
assert args[1] == 'exec'
assert sys.stdin.read().endswith('42.\\n')
Path(args[args.index('--output-last-message')+1]).write_text('DONE deterministic substitute', encoding='utf-8')
print('{}')
""")
    assert result.status == WorkerProcessStatus.SUCCEEDED
    assert result.worker_declared_status == "DONE"
    launch = json.loads((tmp_path / "raw" / "attempt-01" / "launch.json").read_text(encoding="utf-8"))
    assert launch["timeout_seconds"] == 10
    assert launch["argv"][0] == sys.executable


def test_codex_nonzero_exit_cannot_be_success(tmp_path):
    result, _ = adapter_run(tmp_path, "import sys\nsys.exit(9)\n")
    assert result.status == WorkerProcessStatus.NONZERO_EXIT
    assert result.claimed_completion_state is None


def test_supervisor_timeout_cleans_codex_descendant(tmp_path):
    result, workspace = adapter_run(tmp_path, r'''import subprocess, sys, time
from pathlib import Path
child = "import time; from pathlib import Path; time.sleep(3); Path('orphan-marker').write_text('survived')"
subprocess.Popen([sys.executable, '-I', '-c', child])
Path('descendant-started').touch()
time.sleep(20)
''', timeout=1.5)
    assert result.status == WorkerProcessStatus.TIMEOUT
    assert result.process_terminated
    assert (workspace / "descendant-started").exists(), "substitute must launch before timeout"
    time.sleep(3.2)
    assert not (workspace / "orphan-marker").exists()



def test_posix_group_is_killed_even_when_leader_exits_on_sigterm(monkeypatch):
    signals = []
    process = SimpleNamespace(pid=123, poll=lambda: None, wait=lambda timeout: 0)
    fake_os = SimpleNamespace(name="posix", killpg=lambda pid, sig: signals.append((pid, sig)))
    monkeypatch.setattr("truth_firewall.platform_runtime.os", fake_os)
    monkeypatch.setattr("truth_firewall.platform_runtime.signal", SimpleNamespace(SIGTERM=15, SIGKILL=9))
    terminate_process_tree(process)
    assert signals == [(123, 15), (123, 9)]


@pytest.mark.skipif(os.name == "nt", reason="POSIX signal/process-group integration")
def test_posix_sigterm_ignoring_descendant_is_killed_after_leader_exits(tmp_path):
    child = """import signal, time
from pathlib import Path
signal.signal(signal.SIGTERM, signal.SIG_IGN)
Path('ready').touch()
time.sleep(1.5)
Path('survived').touch()
"""
    leader = """import subprocess, sys, time
subprocess.Popen([sys.executable, '-I', '-c', sys.argv[1]])
time.sleep(20)
"""
    process = subprocess.Popen([sys.executable, "-I", "-c", leader, child], cwd=tmp_path,
                               **process_group_options())
    try:
        deadline = time.monotonic() + 5
        while not (tmp_path / "ready").exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert (tmp_path / "ready").exists(), "descendant must install SIGTERM handler before cleanup"
        assert terminate_process_tree(process)
        assert process.returncode == -signal.SIGTERM
        time.sleep(1.7)
        assert not (tmp_path / "survived").exists()
    finally:
        if process.poll() is None:
            terminate_process_tree(process)



def test_keyboard_interrupt_cleans_worker_tree_and_propagates(tmp_path, monkeypatch):
    workspace = tmp_path / "interrupted-task"
    workspace.mkdir()
    worker = tmp_path / "worker.py"
    worker.write_text(r'''import subprocess, sys, time
from pathlib import Path
child = "import time; from pathlib import Path; time.sleep(2); Path('orphan-after-interrupt').touch()"
subprocess.Popen([sys.executable, '-I', '-c', child])
Path('child-started').touch()
time.sleep(20)
''', encoding="utf-8")
    deadline = time.monotonic() + 5

    def interrupted_sleep(seconds):
        # Inject the same Python exception Ctrl+C raises after an actual worker
        # has launched a child. Do not deliver a console signal to pytest itself.
        while not (workspace / "child-started").exists():
            assert time.monotonic() < deadline, "worker must launch its descendant"
            time.sleep(0.01)
        raise KeyboardInterrupt

    monkeypatch.setattr("truth_firewall.supervised_worker.time",
                        SimpleNamespace(monotonic=time.monotonic, sleep=interrupted_sleep))
    with pytest.raises(KeyboardInterrupt):
        run_worker_process(WorkerProcessSpec((sys.executable, "-I", str(worker))),
                           task_run_id="interrupt", attempt_number=1, workspace=workspace,
                           task_instructions="Wait.", artifact_root=tmp_path / "supervisor")
    time.sleep(2.2)
    assert not (workspace / "orphan-after-interrupt").exists()
