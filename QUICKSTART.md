# Quickstart: local Python task with Codex

Use **Windows PowerShell**. Have Python 3.11 or newer, Node.js, Git, and an installed, authenticated Codex CLI available for the current Windows user. Codex installation and authentication time are outside this quickstart.

## Install

```powershell
py -m pip install "git+https://github.com/aldi949/truth-firewall.git"
```

Open PowerShell in the Python repository you want Truth Firewall to work on, then run:

```powershell
truth-firewall init
truth-firewall run
```

## Prepare the task

Run `init` from the root of a disposable or nonsensitive local Python repository. It asks for your task text and optionally a repository-relative output file that your task must create. It is deterministic, local, and needs no LLM or API key. It validates the generated JSON through the existing task/spec loader before reporting success.

A supplied path creates a `file_exists` starter check only. Leaving the path blank creates a valid, explicitly marked editable template with `EDIT_ME_required_output.py` as a placeholder. **Replace that placeholder before running.** Neither option infers behavior or guarantees full coverage. Open the contract to review and extend it before the final `run` command:

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

All listed conditions are mandatory. Truth Firewall cannot infer requirements omitted from the file, and generated checks do not guarantee complete task coverage. If the supported checks cannot establish your task's requirements, do not treat the result as proof of completion. `init` refuses to overwrite an existing contract; use `truth-firewall init --overwrite` only when you intend to replace it.

## Run

From the task repository after reviewing the contract:

```powershell
truth-firewall run
```

The default spec is `.truth-firewall/task.json`. Explicit paths still work exactly as before: `truth-firewall run --spec path\to\task.json`.

Truth Firewall runs Codex, verifies the checks after each attempt, and automatically continues after `REJECT_DONE`, up to three attempts. The final result is:

- `VERIFIED_DONE`: all listed checks passed on the observed final state; the terminal shows the original task, checks, evidence summary, and attempts used.
- `HUMAN_REQUIRED`: completion remains unproven; the terminal names unresolved or failed checks and asks you to review them.
- `ERROR`: execution or setup failed; the terminal gives the runtime reason. This is not a task-failure verdict.

A minimal local run summary is saved in `.truth-firewall\runs\<run-id>\summary.json`. It does not include task text or source. Detailed local invocation logs are stored in the operating system's temporary directory and may contain the task prompt and Codex output.

## Supported scope

This Windows pilot uses a local Python repository, Codex CLI, bounded tasks, and deterministic acceptance checks. Codex runs as your normal user. The pilot does not isolate a hostile worker, prove unlisted requirements, or cover subjective review, UI behavior, deployment, and external services. Truth Firewall does not upload telemetry; Codex service data handling follows your Codex account settings.

## If the command is not found

The Python installation's Scripts directory must be on your Windows user PATH for `truth-firewall` to work. Add that directory and reopen PowerShell. You can also invoke the installed package with `py -m truth_firewall init` and `py -m truth_firewall run` using the same Python that performed the install. Use `py -3.11` (or another installed supported version) consistently if the default launcher selects an older Python. Developer clone/venv instructions are in [CONTRIBUTING.md](CONTRIBUTING.md).
