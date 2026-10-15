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

## Install the package

**Planned PyPI command — not published or verified from PyPI yet:**

```powershell
pipx install truth-firewall
```

Have **Python 3.11+ and pipx** installed first. On Windows, you can install pipx with `py -3.11 -m pip install --user pipx`, run `py -3.11 -m pipx ensurepath`, and reopen PowerShell. Use an installed supported Python version in those commands. Installing Truth Firewall does not install Python, pipx, Node.js, or Codex, and does not sign you into Codex.

Until PyPI publication, use Git plus the reviewed GitHub revision, or install a locally built wheel:

```powershell
pipx install "git+https://github.com/aldi949/truth-firewall.git@95d0fc00de59134527af979de6f07eda1bd65d8a"
# Local release candidate, after building/downloading the actual artifact:
pipx install .\truth_firewall-0.1.0-py3-none-any.whl
truth-firewall --help
truth-firewall --version
truth-firewall demo
```

The offline demo needs no Codex account and explicitly labels its fixtures as demonstrations, not live accuracy. Package installation is isolated by pipx; the real task runner is currently a **Windows pilot**. See [QUICKSTART.md](QUICKSTART.md) for setup and [publication gates](docs/PUBLISHING.md) for the unpublished candidate. Do not use the planned PyPI command until a release has been published and verified to belong to this repository.

## First-time Codex setup

Real tasks require Node.js and an installed, authenticated Codex CLI for the same Windows user. Install those separately using the [Codex CLI instructions](https://developers.openai.com/codex/cli). Verify `node --version`, `codex --version`, and `codex login status`; sign in with `codex login` if necessary. Truth Firewall's pilot expects the npm Codex JavaScript entry point under the current user's `%APPDATA%\npm\node_modules\@openai\codex\bin\codex.js`. Nonstandard installations can set `TRUTH_FIREWALL_CODEX_JS` to their actual `codex.js` path. Codex may require an eligible account or API billing; pipx installation does not provide authentication or credits.

## Run a bounded Python task

Open PowerShell in the Python repository you want Truth Firewall to work on, then run:

```powershell
truth-firewall init
notepad .\.truth-firewall\task.json
truth-firewall activate
truth-firewall run
```

`init` asks for your task text and an optional required output file. It runs locally without an LLM and validates `.truth-firewall/task.json` using the same spec loader as `run`. A supplied file creates only a file-existence starter check; leaving it blank creates a clearly marked editable template. Both remain unreviewed and cannot authorize completion. Review the file and add checks for **every mandatory requirement**, replacing any `EDIT_ME` placeholder, then explicitly confirm with `truth-firewall activate`. An unreviewed spec stops before Codex starts. Truth Firewall cannot infer omitted requirements or guarantee that review makes the checks complete. Existing task files are preserved unless you explicitly use `truth-firewall init --overwrite`. Existing manually authored `--spec` contracts remain supported; an `EDIT_ME` placeholder always blocks completion. The verifier cannot infer from arbitrary task prose that a check targets the intended source file, so inspect paths carefully. See [QUICKSTART.md](QUICKSTART.md) for examples and PATH troubleshooting; clone/venv development setup is in [CONTRIBUTING.md](CONTRIBUTING.md).

Activation records a SHA-256 digest of the reviewed task and checks. If you edit the task or checks afterward, `run` returns `HUMAN_REQUIRED` before Codex starts; review the changed contract and run `truth-firewall activate` again. The digest detects ordinary edits, not deliberate rewriting of both the contract and its digest by someone with access to your files.

Legacy manually authored specs remain accepted without review metadata. If both `acceptance_review` and `onboarding_note` are removed from a generated concrete starter, it becomes indistinguishable from a legacy spec; keep those fields and use `activate` after reviewing the checks.

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
