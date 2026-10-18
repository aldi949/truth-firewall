# Publishing Truth Firewall

Version 0.1.0 was published from commit `cadbcc676887e48a2520c02a1582bdb700d1ada4` to [PyPI](https://pypi.org/project/truth-firewall/0.1.0/). The V0.2 release candidate on this feature branch has **not** been merged, tagged, or published. Local builds and pull-request CI artifacts are candidate evidence, not public-index installs.

## Candidate verification

From the candidate checkout, use Python 3.11+ and a fresh disposable output directory. Build and test tools are development dependencies; the product has no runtime dependencies.

```powershell
python -m pip install build==1.6.1 twine==7.0.0 pipx==1.17.14 pytest==9.1.1
python -m pytest
python tools/run_adversarial.py --cases benchmarks/v02/cases.json --work path\to\new-benchmark-dir --source src
python tools/check_benchmark_result.py path\to\new-benchmark-dir\results.json
python -m build
python -m twine check --strict dist/*
python tools/check_dist.py dist
$wheel = (Get-ChildItem dist/*.whl).FullName
python tools/check_pipx.py --wheel "$wheel" --work path\to\new-pipx-dir
```

The benchmark is a verifier-only experiment with an independent source oracle; it does not run a Codex worker. It preserves known false accepts from incomplete or misdirected legacy contracts in its raw report. `HUMAN_REQUIRED` is counted as `UNKNOWN`, not as a true rejection. The separate frozen holdout must be run only after candidate code is frozen. Review the raw records and any false blocks, rather than quoting the aggregate alone.

The first blind V0.2 holdout scored 10/12, the same as published 0.1.0. It exposed two ignored/ambiguous configuration fields. Strict parsing was added afterward; the unchanged holdout then passed 12/12 as a **post-disclosure retest**, not a new blind result. The raw holdout fixtures and reports are preserved outside the source checkout. Four development-benchmark false accepts remain where legacy contracts omit or misdirect task obligations; do not present them as solved.

The `package-checks.yml` workflow runs tests, this benchmark, build/archive checks, pipx lifecycle and sdist install on Windows, Ubuntu, macOS Intel, and macOS Apple Silicon with Python 3.11 and 3.14. It uploads separate distributions and test evidence. These are deterministic checks using local substitutes where needed; authenticated Codex end-to-end execution on a platform is `NOT_RUN` until actually observed there. Hosted results are required before platform support is claimed. A local wheel, GitHub artifact, or source install does not verify public PyPI installation.

For a real bounded integration, run `tools/smoke_installed.py --exe ... --python ... --work ... --real-codex` against an isolated installed candidate and a separately authenticated Codex CLI. Without `--real-codex`, the verifier controls are **not** a Codex end-to-end run. Record source commit, installed distribution version, artifact SHA-256, Python/platform identity, commands, and outcomes. The package version in `pyproject.toml`, `truth_firewall.__version__`, and wheel metadata must agree. Do not reuse an uploaded version.

## Publication remains a separate approval

The repository's `.github/workflows/publish-pypi.yml` runs only on `workflow_dispatch`, checks that the approved version equals project metadata, and runs only from `main`. Its publish job uses the `pypi` GitHub environment and PyPI Trusted Publishing; no token is stored in the repository. The sole maintainer selected a `main` deployment restriction without required reviewers. The manual trigger is an operational approval boundary, and someone with repository write access could dispatch it, so obtain explicit approval for the exact version and source commit before doing so.

For a later release:

1. Review the final pull-request diff, benchmark limitations, package metadata, artifact hashes, and successful CI for the exact commit. Obtain separate authorization to merge into `main`.
2. Confirm `main` points to the approved release commit, the `pypi` environment remains restricted to `main`, and the PyPI Trusted Publisher is `aldi949/truth-firewall`, workflow `publish-pypi.yml`, environment `pypi`. Confirm the workflow still has only `workflow_dispatch` and the intended version has never been uploaded.
3. Obtain separate explicit publication approval for the version and commit. Only then dispatch the manual workflow from `main` with that version. Do not add a push, tag, or release trigger.
4. Inspect the publish run, then install from the **public PyPI index** in a new environment. Confirm distribution identity/version, CLI, acceptance controls, dependencies and artifact hash against the approved upload. Report public installation only after that check succeeds.

Review activation is a SHA-256 integrity check, not an authenticated signature. It does not solve omitted natural-language requirements, stale-target contracts, malicious same-user rewriting of contract and digest, or legacy metadata-removal ambiguity. These remain release-scope limitations.
