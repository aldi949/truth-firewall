# Quickstart: local Python task with Codex

Published **0.1.0 is a Windows pilot**. The V0.2 source candidate adds guided setup and cross-platform discovery, but is not yet published. Installing the package, installing/authenticating Codex, and executing a real task are separate steps.

## Package installation

Install the published [Truth Firewall package on PyPI](https://pypi.org/project/truth-firewall/):

```powershell
pipx install truth-firewall
```

Prerequisites: Python 3.11+ and pipx on PATH. Installing the package supplies neither Python nor pipx. If needed, install pipx with `py -3.11 -m pip install --user pipx`, run `py -3.11 -m pipx ensurepath`, and reopen PowerShell. Substitute your installed supported Python version. Check `pipx --version` before continuing.

From any working directory, check the installed executable:

```powershell
truth-firewall --help
truth-firewall --version
truth-firewall demo
```

The offline demo uses bundled fixtures and labels them **NOT LIVE PRODUCT ACCURACY**. It needs no Node.js or Codex authentication. Upgrade with `pipx upgrade truth-firewall` and uninstall with `pipx uninstall truth-firewall`. This installs the published version, not the V0.2 source candidate. To try candidate-only commands, use the clone/venv setup in [CONTRIBUTING.md](CONTRIBUTING.md) and confirm the source checkout before continuing. Publication instructions for future versions are in [docs/PUBLISHING.md](docs/PUBLISHING.md).

## First-time Codex setup

Install and authenticate Codex CLI for the current user, following the [official Codex CLI instructions](https://developers.openai.com/codex/cli). Confirm `codex --version`, run `codex login`, and check `codex login status`. Installing Truth Firewall does not configure an account, provide credits, install Node.js, or install Codex.

Published 0.1.0 requires Node.js and expects the npm entry point at `%APPDATA%\npm\node_modules\@openai\codex\bin\codex.js`. For another npm layout, set `TRUTH_FIREWALL_CODEX_JS` to the real `codex.js` path. Confirm `node --version` for an npm installation.

The V0.2 candidate resolves native `codex` on PATH, or launches npm's JavaScript entry point with Node.js; native Codex needs no Node.js. The JS override is authoritative: an invalid override reports a setup `ERROR` rather than silently selecting a different executable. PATH discovery does not authenticate executables. An unreviewed contract still blocks before runtime discovery or worker launch.

## Guided setup: V0.2 candidate

From the root of a disposable or nonsensitive Python repository, with the candidate installed:

```powershell
truth-firewall init --guided
truth-firewall activate
truth-firewall run
```

For a small task, enter this sequence when prompted:

```text
Task text: In app.py, add_one(value) returns 2 for input 1.
Mandatory criterion: add_one(1) returns integer 2
Check kind: behavior
Checked file: app.py
Python function name: add_one
Number of positional arguments: 1
Argument 1 type: number
Argument 1: 1
Expected result type: number
Expected result: 2
Add another case?: no
Add another mandatory check?: no
```

This intentionally bounded request requires only the named case. For a broader task, one example is insufficient: add checks for every mandatory requirement, and use human review for unsupported or ambiguous requirements. `behavior` accepts text, number, bool, null, and list values; text needs no quotation marks, while lists use JSON arrays. `api` checks the stated function parameter names. `file` checks presence or absence and does not establish modification or behavior. A criterion description is a user annotation, not proof of coverage.

Setup does not run checks, launch Codex, or activate anything. It saves `.truth-firewall/task.json` as **UNREVIEWED**. Run `activate`, inspect the full saved task and checks, and type `ACTIVATE` only after your explicit review. The original task and checks are bound to that activation by a SHA-256 digest.

## Compatible template setup: published 0.1.0 and V0.2

Published 0.1.0 does not have `--guided`. Its supported flow is:

```powershell
truth-firewall init
notepad .\.truth-firewall\task.json
truth-firewall activate
truth-firewall run
```

Default `init` asks for task text and an optional output file. A path creates only a `file_exists` starter; blank input creates an `EDIT_ME_required_output.py` template. Both are unreviewed; replace placeholders and edit checks before activation. Setup is local and deterministic, with no LLM or API key, and validates schema using the same loader as `run`. **Schema validation and explicit review do not establish complete requirement coverage.**

Keep `task` as the original bounded request. Each condition has a unique `id` and repository-relative `path`. Published 0.1.0 supports:

- `file_exists`: `path` and `exists` (`true` or `false`).
- `python_function`: `path`, top-level `symbol`, and exact `parameters` list.
- `black_box`: `path`, top-level `symbol`, and one or more `cases` with JSON `args` and `expected` return value.

For the bounded `add_one(1) == 2` request above, these checks establish file/API shape and that one case:

```json
[
  {"id": "file", "kind": "file_exists", "path": "app.py", "exists": true},
  {"id": "api", "kind": "python_function", "path": "app.py", "symbol": "add_one", "parameters": ["value"]},
  {"id": "behavior", "kind": "black_box", "path": "app.py", "symbol": "add_one", "cases": [{"args": [1], "expected": 2}]}
]
```

All listed conditions are mandatory. Truth Firewall cannot infer requirements omitted from the file, and explicit review does not guarantee complete task coverage. If the supported checks cannot establish your task's requirements, do not treat the result as proof of completion. Check that every condition points to the intended file: the verifier cannot soundly match paths against arbitrary natural-language task text. `init` refuses to overwrite an existing contract; use `truth-firewall init --overwrite` only when you intend to replace it.

V0.2 rejects duplicate JSON keys and unrecognized contract or check fields before starting Codex. A field that names an unsupported command is a configuration error, not evidence that the command ran. Keep only the documented fields, or request human review when the supported checks cannot prove your requirement.

## Additional V0.2 evidence checks

These checks are available in manually authored candidate specs, not the guided prompts:

| Kind | Required fields | What it establishes |
|---|---|---|
| `file_changed` | `path`, `baseline_sha256` | Current file bytes differ from a reviewed baseline hash. A preexisting unchanged file fails; a changed file is not automatically correct. |
| `unittest` | `path`, `suite: {"sha256": "<reviewed source hash>", "test_ids": ["TestClass.test_name"]}` | The exact reviewed suite source executes, with the exact nonempty test IDs and no failures, errors, skips, expected failures, or unexpected successes. |

The unittest check runs the specified Python unittest source using the installed interpreter. It is not an arbitrary shell-command runner, pytest adapter, or complete defense against malicious tests. Test IDs omit the temporary `_protected_suite.` module prefix. Missing hash/IDs or unavailable evidence requires human review; changed suite source fails. The source hash binds that file, not every imported dependency. The verifier executes repository code as your user, without a hostile-code sandbox.

Candidate specs may also declare optional `obligations` with `id`, `text`, `scope`, `condition_ids`, `coverage`, and optional `target_path` / `mandatory`. Supported scopes are `file_state`, `signature`, `examples`, and `test_suite`. A mandatory obligation needs `coverage: "full"`, compatible evidence bindings, and matching declared targets. Unmapped, incompatible, broader unsupported, or unresolved scopes require human review. `examples` means those explicit cases; it does not prove a general transformation rule. This metadata is authored, not inferred from prose; legacy specs without it remain compatible and may still omit requirements. Activation never automatically supplies missing coverage.

## Run

From the task repository after preparing and reviewing the contract, confirm the displayed task and checks, then run:

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

## Platform and verification scope

Published 0.1.0's real runner is a Windows pilot. V0.2 adds Windows/POSIX runtime discovery, process launching and timeout cleanup, with deterministic integration substitutes for hosted CI. CI results and authenticated Codex end-to-end results must be reported separately for Windows, Ubuntu, macOS Intel, and macOS Apple Silicon. Do not infer authenticated support from a passing deterministic substitute; consult the candidate release report for completed platform runs.

The supported task boundary remains a local Python repository and bounded deterministic evidence. Codex runs as your normal user. This does not isolate a hostile worker, prove unlisted requirements, or cover subjective review, UI behavior, deployment, and external services. Truth Firewall does not upload telemetry; Codex service data handling follows your Codex account settings.

## If the command is not found

Run `pipx ensurepath`, reopen PowerShell, and check `pipx list`. pipx installs the CLI in its own environment; your system `py -m truth_firewall` is not expected to see a pipx-installed package. If multiple Python versions are installed, use `pipx install --python 3.11 ...` (or another supported version) to choose one. Developer clone/venv instructions are in [CONTRIBUTING.md](CONTRIBUTING.md).

On Windows Python 3.11, pipx's own shared-library storage can fail in a non-ASCII path (`UnicodeEncodeError` or `No module named pip`). Before installation, set `$env:PIPX_HOME` to a new writable ASCII-only absolute path and `$env:PYTHONUTF8 = "1"`, then retry. Keep that `PIPX_HOME` setting for subsequent pipx lifecycle commands. The hosted Python 3.11 checks use ASCII pipx storage and non-ASCII project directories; they do not establish support for non-ASCII pipx storage. These settings do not change Truth Firewall's acceptance checks.
