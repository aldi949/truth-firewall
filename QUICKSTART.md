# Quickstart: local Python task with Codex

Use **Windows PowerShell** for real tasks. Package installation and Codex setup are separate.

## Package installation

**Planned command after PyPI publication — not currently a verified public PyPI install:**

```powershell
pipx install truth-firewall
```

Prerequisites: Python 3.11+ and pipx on PATH. Installing the package supplies neither Python nor pipx. If needed, install pipx with `py -3.11 -m pip install --user pipx`, run `py -3.11 -m pipx ensurepath`, and reopen PowerShell. Substitute your installed supported Python version. Check `pipx --version` before continuing.

Before publication, the available GitHub installation requires Git and uses the reviewed published commit:

```powershell
pipx install "git+https://github.com/aldi949/truth-firewall.git@95d0fc00de59134527af979de6f07eda1bd65d8a"
```

For a locally built candidate wheel instead, use `pipx install .\truth_firewall-0.1.0-py3-none-any.whl`. These are actual source/artifact installs, not proof that `pipx install truth-firewall` resolves to this project on PyPI. Publication instructions and gates are in [docs/PUBLISHING.md](docs/PUBLISHING.md).

From any working directory, check the installed executable:

```powershell
truth-firewall --help
truth-firewall --version
truth-firewall demo
```

The offline demo uses bundled fixtures and labels them **NOT LIVE PRODUCT ACCURACY**. It needs no Node.js or Codex authentication. pipx upgrade and uninstall commands after publication are `pipx upgrade truth-firewall` and `pipx uninstall truth-firewall`. For a newer local wheel, use `pipx install --force .\path\to\new.whl`; `pipx upgrade` of a wheel-pinned installation does not switch it to PyPI.

## First-time Codex setup

Install Node.js and Codex CLI separately for the current Windows user, following the [official Codex CLI instructions](https://developers.openai.com/codex/cli). Confirm `node --version` and `codex --version`, then run `codex login` and check `codex login status`. You need valid Codex authentication; installing Truth Firewall does not configure an account, provide credits, install Node.js, or install Codex.

The pilot expects the npm Codex entry point at `%APPDATA%\npm\node_modules\@openai\codex\bin\codex.js`. For another installation layout, set `TRUTH_FIREWALL_CODEX_JS` to the real `codex.js` path. If Node.js or that entry point is missing, a reviewed task reports a setup `ERROR`; an unreviewed contract still blocks before attempting a worker. Runtime support beyond Windows is not claimed by this pilot.

Open PowerShell in the Python repository you want Truth Firewall to work on, then run:

```powershell
truth-firewall init
notepad .\.truth-firewall\task.json
truth-firewall activate
truth-firewall run
```

## Prepare the task

Run `init` from the root of a disposable or nonsensitive local Python repository. It asks for your task text and optionally a repository-relative output file that your task must create. It is deterministic, local, and needs no LLM or API key. It validates the generated JSON through the existing task/spec loader before reporting success.

A supplied path creates a `file_exists` starter check only. Leaving the path blank creates a valid, explicitly marked editable template with `EDIT_ME_required_output.py` as a placeholder. **Replace that placeholder before activation.** Both starter forms are unreviewed and cannot authorize completion. Neither option infers behavior or guarantees full coverage. Open the contract to review and extend it before activation:

```powershell
notepad .\.truth-firewall\task.json
```

Keep `task` as your bounded request. Edit `conditions` to check **every mandatory requirement**. The `onboarding_note` is an editing reminder, not an acceptance check. Each condition has a unique `id` and a repository-relative `path`:

- `file_exists`: `path` and `exists` (`true` or `false`).
- `python_function`: `path`, top-level `symbol`, and exact `parameters` list.
- `black_box`: `path`, top-level `symbol`, and one or more `cases` with JSON `args` and `expected` return value.

For example, if your task is specifically to implement `add_one(value)` in `app.py`, you could use these conditions (adapt them to your actual request):

```json
[
  {"id": "file", "kind": "file_exists", "path": "app.py", "exists": true},
  {"id": "api", "kind": "python_function", "path": "app.py", "symbol": "add_one", "parameters": ["value"]},
  {"id": "behavior", "kind": "black_box", "path": "app.py", "symbol": "add_one", "cases": [{"args": [1], "expected": 2}]}
]
```

All listed conditions are mandatory. Truth Firewall cannot infer requirements omitted from the file, and explicit review does not guarantee complete task coverage. If the supported checks cannot establish your task's requirements, do not treat the result as proof of completion. Check that every condition points to the intended file: the verifier cannot soundly match paths against arbitrary natural-language task text. `init` refuses to overwrite an existing contract; use `truth-firewall init --overwrite` only when you intend to replace it.

## Run

From the task repository after reviewing and editing the contract, confirm the displayed task and checks, then run:

```powershell
truth-firewall activate
truth-firewall run
```

The default spec is `.truth-firewall/task.json`. For a generated contract at another path, use `truth-firewall activate --spec path\to\task.json`. Existing manually authored contracts remain usable with `truth-firewall run --spec path\to\task.json`; generated contracts require activation. An unreviewed starter returns `HUMAN_REQUIRED` before launching Codex, even if its file-existence check would pass. An `EDIT_ME` placeholder cannot authorize `VERIFIED_DONE`.

Activation stores a digest of the task and acceptance checks you reviewed. After changing either, review the file and run `truth-firewall activate` again before `run`; a changed reviewed contract returns `HUMAN_REQUIRED` without launching Codex. This detects ordinary edits, but the digest is not an authenticated signature against someone who can rewrite both it and the contract.

Keep the generated `acceptance_review` and `onboarding_note` fields. Removing both from a concrete starter makes it indistinguishable from an older manually authored spec, which remains accepted for compatibility. Review and activate the generated contract instead.

Truth Firewall runs Codex, verifies the checks after each attempt, and automatically continues after `REJECT_DONE`, up to three attempts. The final result is:

- `VERIFIED_DONE`: all listed checks passed on the observed final state; the terminal shows the original task, checks, evidence summary, and attempts used.
- `HUMAN_REQUIRED`: completion remains unproven; the terminal names unresolved or failed checks and asks you to review them.
- `ERROR`: execution or setup failed; the terminal gives the runtime reason. This is not a task-failure verdict.

A minimal local run summary is saved in `.truth-firewall\runs\<run-id>\summary.json`. It does not include task text or source. Detailed local invocation logs are stored in the operating system's temporary directory and may contain the task prompt and Codex output.

## Supported scope

This Windows pilot uses a local Python repository, Codex CLI, bounded tasks, and deterministic acceptance checks. Codex runs as your normal user. The pilot does not isolate a hostile worker, prove unlisted requirements, or cover subjective review, UI behavior, deployment, and external services. Truth Firewall does not upload telemetry; Codex service data handling follows your Codex account settings.

## If the command is not found

Run `pipx ensurepath`, reopen PowerShell, and check `pipx list`. pipx installs the CLI in its own environment; your system `py -m truth_firewall` is not expected to see a pipx-installed package. If multiple Python versions are installed, use `pipx install --python 3.11 ...` (or another supported version) to choose one. Developer clone/venv instructions are in [CONTRIBUTING.md](CONTRIBUTING.md).

On Windows Python 3.11, pipx's own shared-library storage can fail in a non-ASCII path (`UnicodeEncodeError` or `No module named pip`). Before installation, set `$env:PIPX_HOME` to a new writable ASCII-only absolute path and `$env:PYTHONUTF8 = "1"`, then retry. Keep that `PIPX_HOME` setting for subsequent pipx lifecycle commands. The hosted Python 3.11 checks use ASCII pipx storage and non-ASCII project directories; they do not establish support for non-ASCII pipx storage. These settings do not change Truth Firewall's acceptance checks.
