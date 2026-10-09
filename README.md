# Your coding agent says “DONE”. That doesn’t mean the job is done.

**Truth Firewall won’t accept `DONE` until the work is independently verified.**

Your coding agent says:

> “Done. Tests pass.”

Maybe it is.

Maybe it skipped a requirement.\
Maybe an edge case is broken.\
Maybe it changed something it shouldn’t have.

And then **you** have to check everything anyway.

That defeats the whole point of having an agent.

# Truth Firewall stops that.

**The agent does the work.**\
**Truth Firewall decides whether `DONE` is actually earned.**

Give Codex the task.

Walk away.
```text
Codex works
    ↓
Codex says DONE
    ↓
Truth Firewall verifies the required work
    ↓
┌──────────────────┬──────────────────┬──────────────────┐
↓                  ↓                  ↓
VERIFIED_DONE    REJECT_DONE       HUMAN_REQUIRED
                     ↓
              Codex keeps working
```

## Quickstart: try it in 5 minutes

Use Windows PowerShell with Python 3.11 or newer, Node.js, Git, and an installed, authenticated Codex CLI. Codex installation and sign-in are separate from this setup.

```powershell
py -m pip install "git+https://github.com/aldi949/truth-firewall.git"
```

Open PowerShell in the Python repository you want Truth Firewall to work on, then run:

```powershell
truth-firewall init
truth-firewall run
```

`init` asks for your task text and an optional required output file. It runs locally without an LLM and validates `.truth-firewall/task.json` using the same spec loader as `run`. A supplied file creates only a file-existence starter check; leaving it blank creates a clearly marked editable template. Before `run`, review the file and add checks for **every mandatory requirement**, replacing any `EDIT_ME` placeholder. Truth Firewall cannot infer omitted requirements or guarantee that these checks completely cover your task. Existing task files are preserved unless you explicitly use `truth-firewall init --overwrite`. See [QUICKSTART.md](QUICKSTART.md) for examples and PATH troubleshooting; clone/venv development setup is in [CONTRIBUTING.md](CONTRIBUTING.md).

The supported checks are `file_exists` (a repository-relative path exists or does not exist), `python_function` (a top-level function has the exact parameter list), and `black_box` (JSON argument lists produce expected return values). Give each condition a unique `id`. Every listed condition is mandatory, and requirements missing from `task.json` cannot be verified.

## What the decisions mean

- `VERIFIED_DONE`: all listed mandatory checks passed for the observed final repository state. The terminal shows the original task, check summary, and attempts used.
- `REJECT_DONE`: a worker completion claim failed at least one mandatory check. This is an intermediate decision; Truth Firewall sends actionable failures back to Codex and continues within the three-attempt limit.
- `HUMAN_REQUIRED`: completion could not be proven, including when attempts are exhausted or a mandatory condition cannot be checked. Review the named requirements and decide what to do next.
- `ERROR`: the worker, verifier, or runtime could not complete the run. This is an execution problem, not a verdict that the task failed.

## Why this exists

A worker saying “done,” code existing, or worker-owned tests passing does not establish that your original requirements were met. Truth Firewall checks the acceptance conditions you supplied after each attempt and blocks an unsupported completion claim. It can continue Codex automatically when a check fails, so you do not have to watch each turn.

## What `VERIFIED_DONE` actually means

It means the listed mandatory checks passed on the final observed workspace state. It does **not** prove that an omitted requirement was met, that the checks themselves are complete, or that all possible behavior is correct. Author the checks to cover the task before you start; if the supported checks cannot establish completion, treat the outcome as requiring human judgment.

## Current pilot scope

This first release supports local repositories, bounded Python coding tasks, a Codex CLI worker, deterministic file/API/input-output checks, and at most three worker attempts. A small local run summary is written under `.truth-firewall\runs\<run-id>\summary.json`; it records the decision and attempt counts without task text or source code.

## Limitations

This is not a hostile-worker security boundary or a claim of universal correctness. Codex runs as your normal user and can access files available to that account, including the task specification. Use disposable or nonsensitive repositories for the pilot. Subjective quality, UI behavior, deployment, external services, broad repository work, and requirements that cannot be observed with the supported checks are outside this pilot.

## Privacy

Truth Firewall does not automatically upload telemetry, source code, or task text. Codex sends task instructions and the context it reads to its configured service under your account settings. Detailed local invocation logs are stored in the operating system's temporary directory and may contain the task prompt and worker output. Review those settings before using private code.

## Feedback and license

Trying Truth Firewall on a real task? Please use [PILOT_FEEDBACK.md](PILOT_FEEDBACK.md). Contribution guidance and local test commands are in [CONTRIBUTING.md](CONTRIBUTING.md). Truth Firewall is [MIT licensed](LICENSE).
