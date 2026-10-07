# Stop babysitting your coding agent.

**Truth Firewall won’t accept DONE until the task is independently verified.**

Your coding agent can say a task is done while an edge case is broken, a required API is missing, or its own tests miss the real requirement. That leaves you watching every turn and checking the work yourself.

Truth Firewall gives a bounded Python task to Codex and checks the result against acceptance checks you specify. When a check fails, it blocks completion and sends the failure back for another attempt. You can step away and return to a clear outcome.

**Codex works → Truth Firewall checks → `VERIFIED_DONE` / `REJECT_DONE` / `HUMAN_REQUIRED`**

`REJECT_DONE` continues the same workflow automatically, up to three attempts. If completion still cannot be proven, the final result is `HUMAN_REQUIRED`. An infrastructure failure is reported as `ERROR`.

## Quickstart

Use Windows PowerShell with Python 3.11+, Node.js, and an installed, authenticated Codex CLI. Codex installation and sign-in are separate from this setup.

```powershell
git clone https://github.com/aldi949/truth-firewall.git
cd truth-firewall
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install .
$tfRoot = (Get-Location).Path
Set-Location C:\path\to\your-python-repo
New-Item -ItemType Directory -Force .truth-firewall | Out-Null
Copy-Item "$tfRoot\examples\pilot-task.json" .\.truth-firewall\task.json
notepad .\.truth-firewall\task.json
& "$tfRoot\.venv\Scripts\truth-firewall.exe" run --spec .truth-firewall\task.json
```

Before the last command, edit the copied JSON to describe **your** task and every mandatory acceptance check. The example checks a small `slugify` function; it is a template, not a universal task specification. See [QUICKSTART.md](QUICKSTART.md) for the three supported check types and an explanation of the final result.

## What the result means

- `VERIFIED_DONE`: every listed mandatory check passed for the observed final repository state. It does not prove requirements missing from the check file.
- `HUMAN_REQUIRED`: completion could not be proven, including when attempts are exhausted or a mandatory condition cannot be checked. Review the named requirements and decide what to do next.
- `ERROR`: the worker, verifier, or runtime could not complete the run. This is an execution problem, not a verdict on the coding task.

The terminal prints the task, check summary, and attempts used for verified completion. Each run also writes a small local summary under `.truth-firewall\runs\`; it contains no task text or source code and is ignored by Git.

## Pilot scope and limits

This first release supports local repositories, bounded Python coding tasks, a Codex CLI worker, and explicit deterministic checks for file existence, exact Python function parameters, and JSON input/output cases. Checks must cover the original task: a requirement omitted from the JSON cannot be independently verified. The worker gets at most three attempts.

This is **not** a hostile-worker security boundary or a claim of universal correctness. Codex runs as your normal user and can access files that user can access, including the task specification. Use disposable or nonsensitive repositories for the pilot. Subjective quality, UI behavior, deployment, external services, and requirements that cannot be observed with the supported checks are outside this pilot.

Truth Firewall does not automatically upload telemetry, source, or task text. Codex sends task instructions and the context it reads to its configured service under your account settings. Detailed local invocation logs may contain task text and worker output.

Trying it with a real task? Please use [PILOT_FEEDBACK.md](PILOT_FEEDBACK.md). Contributions and local test commands are in [CONTRIBUTING.md](CONTRIBUTING.md). The code is [MIT licensed](LICENSE).
