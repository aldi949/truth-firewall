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

Install the published [Truth Firewall package on PyPI](https://pypi.org/project/truth-firewall/):

```powershell
pipx install truth-firewall
```

Have **Python 3.11+ and pipx** installed first. On Windows, you can install pipx with `py -3.11 -m pip install --user pipx`, run `py -3.11 -m pipx ensurepath`, and reopen PowerShell. Use an installed supported Python version in those commands. Installing Truth Firewall does not install Python, pipx, Node.js, or Codex, and does not sign you into Codex.

Check the installed executable:

```powershell
truth-firewall --help
truth-firewall --version
truth-firewall demo
```

The offline demo needs no Codex account and explicitly labels its fixtures as demonstrations, not live accuracy. Package installation is isolated by pipx. **PyPI currently supplies 0.1.0, whose real task runner is a Windows pilot.** The V0.2 source candidate adds guided setup and cross-platform runtime discovery; those changes are not in the published 0.1.0 artifact. Hosted deterministic CI and authenticated Codex runs are separate checks: passing CI does not establish authenticated Codex support on Linux or either macOS architecture. See [QUICKSTART.md](QUICKSTART.md) for the two version paths and [publication gates](docs/PUBLISHING.md) for future releases.

## First-time Codex setup

Real tasks require an installed, authenticated Codex CLI for the same user. Install it separately using the [Codex CLI instructions](https://developers.openai.com/codex/cli), then check `codex --version` and `codex login status`; sign in with `codex login` if necessary. For published 0.1.0, install Node.js and npm Codex; the Windows pilot expects `%APPDATA%\npm\node_modules\@openai\codex\bin\codex.js`. Set `TRUTH_FIREWALL_CODEX_JS` to the actual entry point for a different npm layout.

The V0.2 candidate discovers a native `codex` executable on PATH first, or launches the npm JavaScript entry point with Node.js. A native installation does not require Node.js. The explicit `TRUTH_FIREWALL_CODEX_JS` override remains supported and a broken override fails instead of silently choosing another executable. PATH discovery is a convenience, not executable authentication. Codex may require an eligible account or API billing; pipx installation does not provide authentication or credits.

## Run a bounded Python task

With the **V0.2 source candidate** installed using [CONTRIBUTING.md](CONTRIBUTING.md), open a terminal in the Python repository you want Truth Firewall to work on:

```powershell
truth-firewall init --guided
truth-firewall activate
truth-firewall run
```

`init --guided` asks for the original task and each bounded mandatory criterion, then collects behavior cases, a Python function signature, or file-existence checks. Ordinary text, numeric, boolean, and null values need no raw contract editing; list values use JSON arrays. All generated checks remain **unreviewed**. `activate` displays the saved task and full checks; type `ACTIVATE` only after reviewing every mandatory requirement. Setup runs locally without an LLM and never activates automatically. Unsupported or ambiguous criteria stop setup instead of inventing evidence. An unreviewed spec stops before Codex starts.

For **published 0.1.0**, use `truth-firewall init`, edit `.truth-firewall/task.json`, then run `activate` and `run`; `--guided` is not available there. The existing `init` template workflow remains supported in V0.2. A supplied path creates only a file-existence starter; blank input creates an `EDIT_ME` template that must be replaced. Existing task files are preserved unless explicitly overwritten. Truth Firewall cannot infer omitted requirements, infer task-to-path correspondence from arbitrary prose, or guarantee that review makes the contract complete. See [QUICKSTART.md](QUICKSTART.md) for a guided example and version-specific instructions.

Activation records a SHA-256 digest of the reviewed task and checks. If you edit the task or checks afterward, `run` returns `HUMAN_REQUIRED` before Codex starts; review the changed contract and run `truth-firewall activate` again. The digest detects ordinary edits, not deliberate rewriting of both the contract and its digest by someone with access to your files.

Legacy manually authored specs remain accepted without review metadata. If both `acceptance_review` and `onboarding_note` are removed from a generated concrete starter, it becomes indistinguishable from a legacy spec; keep those fields and use `activate` after reviewing the checks.

The guided flow supports `file_exists` (presence or absence), `python_function` (a top-level function's exact supported parameter list), and `black_box` (specified input/output cases, with exact JSON types). File existence does not establish modification, signatures do not establish behavior, and finite cases do not establish broader behavior.

V0.2 also accepts manually authored `file_changed` checks against a reviewed content hash and `unittest` checks binding a suite source hash and exact test IDs. The latter rejects changed suite source, missing tests, skips, and expected failures; it is not a universal defense against malicious tests or arbitrary command substitution. Optional explicit obligation metadata can bind declared scopes and target paths to supported evidence; unresolved bindings require human review. It does not discover omitted natural-language requirements, and legacy contracts without that metadata remain compatible. See [QUICKSTART.md](QUICKSTART.md) for the exact schema and limitations.

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

Published 0.1.0 supports bounded local Python tasks with a Windows Codex worker. The V0.2 candidate adds native/npm discovery and process-group cleanup for Windows, Ubuntu, and macOS, with deterministic substitutes for CI where Codex credentials are unavailable. Authenticated Codex end-to-end support must be validated separately on each platform and architecture. Both versions allow at most three worker attempts. A small local run summary is written under `.truth-firewall/runs/<run-id>/summary.json`; it records the decision and attempt counts without task text or source code.

## Limitations

This is not a hostile-worker security boundary or a claim of universal correctness. Codex runs as your normal user and can access files available to that account, including the task specification. Use disposable or nonsensitive repositories for the pilot. Subjective quality, UI behavior, deployment, external services, broad repository work, and requirements that cannot be observed with the supported checks are outside this pilot.

## Privacy

Truth Firewall does not automatically upload telemetry, source code, or task text. Codex sends task instructions and the context it reads to its configured service under your account settings. Detailed local invocation logs are stored in the operating system's temporary directory and may contain the task prompt and worker output. Review those settings before using private code.

## Feedback and license

Trying Truth Firewall on a real task? Please use [PILOT_FEEDBACK.md](PILOT_FEEDBACK.md). Contribution guidance and local test commands are in [CONTRIBUTING.md](CONTRIBUTING.md). Truth Firewall is [MIT licensed](LICENSE).
