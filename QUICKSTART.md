# Quickstart: local Python task with Codex

Use **Windows PowerShell**. Have Python 3.11 or newer, Node.js, Git, and an installed, authenticated Codex CLI available for the current Windows user. Codex installation and authentication time are outside this quickstart.

## Install

```powershell
git clone https://github.com/aldi949/truth-firewall.git
cd truth-firewall
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install .
$tfRoot = (Get-Location).Path
```

## Prepare the task

Use a disposable or nonsensitive local Python repository. In the **same PowerShell session**:

```powershell
Set-Location C:\path\to\your-python-repo
New-Item -ItemType Directory -Force .truth-firewall | Out-Null
Copy-Item "$tfRoot\examples\pilot-task.json" .\.truth-firewall\task.json
notepad .\.truth-firewall\task.json
```

Replace `task` with your bounded request. Replace the example `conditions` with checks for **every mandatory requirement**. Each condition has a unique `id` and a repository-relative `path`:

- `file_exists`: `path` and `exists` (`true` or `false`).
- `python_function`: `path`, top-level `symbol`, and exact `parameters` list.
- `black_box`: `path`, top-level `symbol`, and one or more `cases` with JSON `args` and `expected` return value.

All listed conditions are mandatory. The checks do not discover requirements omitted from the file. If the supported checks cannot establish your task's requirements, do not treat the result as proof of completion.

## Run

Still in the task repository and the same PowerShell session:

```powershell
& "$tfRoot\.venv\Scripts\truth-firewall.exe" run --spec .truth-firewall\task.json
```

Truth Firewall runs Codex, verifies the checks after each attempt, and automatically continues after `REJECT_DONE`, up to three attempts. The final result is:

- `VERIFIED_DONE`: all listed checks passed on the observed final state; the terminal shows the original task, checks, evidence summary, and attempts used.
- `HUMAN_REQUIRED`: completion remains unproven; the terminal names unresolved or failed checks and asks you to review them.
- `ERROR`: execution or setup failed; the terminal gives the runtime reason. This is not a task-failure verdict.

A minimal local run summary is saved in `.truth-firewall\runs\<run-id>\summary.json`. It does not include task text or source. Detailed local invocation logs are stored in the operating system's temporary directory and may contain the task prompt and Codex output.

## Supported scope

This Windows pilot uses a local Python repository, Codex CLI, bounded tasks, and deterministic acceptance checks. Codex runs as your normal user. The pilot does not isolate a hostile worker, prove unlisted requirements, or cover subjective review, UI behavior, deployment, and external services. Truth Firewall does not upload telemetry; Codex service data handling follows your Codex account settings.
