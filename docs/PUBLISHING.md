# Publication checklist (no publication is configured)

The initial candidate is `truth-firewall` version `0.1.0`, from
`https://github.com/aldi949/truth-firewall`. The currently published GitHub baseline
is `95d0fc00de59134527af979de6f07eda1bd65d8a`. Packaging changes must be reviewed
before they are committed, pushed, tagged, or published. None of those actions is
authorized by running the local checks.

On 2026-10-13, `https://pypi.org/pypi/truth-firewall/json` returned HTTP 404.
No existing project was visible. This does not reserve the name or establish that
PyPI will accept it. Recheck immediately before publication. If another publisher
owns the name, stop: do not install or upload an unrelated package. Choose a new
distribution name with the owner and update metadata/docs/tests together.

## Build and inspect

Use Python 3.11+ in a development environment. The product has no runtime
dependencies. Build, test, pipx and upload-validation tools are development tools:

```powershell
python -m pip install build==1.6.1 twine==7.0.0 pipx==1.17.14 pytest==9.1.1
python -m pytest
python -m build
python -m twine check --strict dist/*
python tools/check_dist.py dist
$wheel = (Get-ChildItem dist/*.whl).FullName
python tools/check_pipx.py --wheel "$wheel" --work "$env:TEMP\TF candidate check"
```

The check directory must be new. `check_pipx.py` uses private pipx paths, launches
the installed executable outside the repository, checks all seven bundled demo
fixtures, completion controls, review invalidation, and missing-runtime errors,
then tests reinstall, same-artifact upgrade, and uninstall. It does not modify
the user's normal pipx installation or PATH. An optional `--upgrade-wheel` can
test a later locally built version via a local-only package resolver. Such a
test-only version must never be published as the release candidate.

Rebuild the sdist into a wheel in a fresh directory and install that wheel too.
Record archive contents and SHA-256 hashes. Check that version `0.1.0` in
`pyproject.toml`, `truth_firewall.__version__`, and wheel metadata agree. Update
both version declarations deliberately for a later release; never overwrite a
version already uploaded to PyPI. The `package-checks.yml` workflow runs tests,
builds, archive checks, pipx checks and sdist installation on Windows and Linux
with Python 3.11 and 3.14, and uploads downloadable artifacts. It has only
`contents: read`, no OIDC permission, no publishing step, and no credentials
persisted in its checkout. New hosted CI results remain NOT_RUN until the changes
are explicitly approved and pushed; local Windows execution is separate evidence.

For a bounded real integration check, run `tools/smoke_installed.py --exe ...
--python ... --work ... --real-codex` against the actual installed executable and
its pipx interpreter. This requires separately installed Node.js, Codex CLI, and
valid Codex authentication. Without the flag, the positive/negative controls call
the installed verifier directly and are **not** a real end-to-end Codex run.

## Deliberate Trusted Publishing setup

No active publishing workflow is included. After explicit publication approval:

1. Recheck the name and the exact approved source revision and artifacts. Require
   all local gates and hosted Windows checks for that revision to pass.
2. Create a GitHub environment named `pypi`, with required reviewers, prevention
   of self-approval where available, and deployment restricted to the approved
   release branch/tags. Verify the protection actually applies to this repository.
3. In the PyPI owner's account, add a **pending Trusted Publisher** with project
   `truth-firewall`, owner `aldi949`, repository `truth-firewall`, workflow filename
   `publish-pypi.yml`, and environment `pypi`. A pending publisher does not reserve
   the name. Do not put an API token in this repository.
4. Only after authorization, create `.github/workflows/publish-pypi.yml` from the
   configuration below. Keep it manual-only, with a protected publish environment.
   Do not add push, tag, release, or pull-request publication triggers.

This inactive example uses official actions pinned to revisions inspected during
preparation. Review those revisions again before enabling publication:

```yaml
name: Deliberate PyPI publication
on:
  workflow_dispatch:
    inputs:
      version:
        description: Approved version (must match pyproject.toml)
        required: true
        type: string
permissions:
  contents: read
jobs:
  build:
    if: github.ref == 'refs/heads/main'
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262
        with:
          persist-credentials: false
      - uses: actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065
        with:
          python-version: '3.11'
      - run: python -m pip install build==1.6.1 twine==7.0.0 pytest==9.1.1
      - name: Confirm approved version
        env:
          APPROVED_VERSION: ${{ inputs.version }}
        run: python -c "import os,tomllib; assert tomllib.load(open('pyproject.toml','rb'))['project']['version'] == os.environ['APPROVED_VERSION']"
      - run: python -m pytest
      - run: python -m build
      - run: python -m twine check --strict dist/*
      - run: python tools/check_dist.py dist
      - uses: actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02
        with:
          name: approved-pypi-artifacts
          path: dist/*
          if-no-files-found: error
  publish:
    needs: build
    runs-on: ubuntu-latest
    environment: pypi
    permissions:
      id-token: write
    steps:
      - uses: actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093
        with:
          name: approved-pypi-artifacts
          path: dist
      - uses: pypa/gh-action-pypi-publish@dc37677b2e1c63e2034f94d8a5b11f265b73ba33
```

This is documentation, not an enabled workflow. Confirm required environment
approval and approve the exact commit before enabling it. See
[PyPI's pending-publisher instructions](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/)
and [environment restrictions](https://docs.pypi.org/trusted-publishers/adding-a-publisher/).

## Public verification after publication (G12)

Before publication, G12 and `PUBLIC_PYPI_INSTALL` must remain **NOT_RUN**. Local
wheels and GitHub installs cannot satisfy this gate.

After approval and upload, use a new environment and the real public index:

```powershell
pipx install truth-firewall
pipx list
truth-firewall --version
truth-firewall --help
truth-firewall demo
```

Confirm the installed distribution identity, version, dependencies and bundled
fixtures against the approved artifact; then repeat unreviewed blocking, activation,
review invalidation and a bounded real task. Independently download the PyPI wheel
and compare its SHA-256 to the approved upload. Only then remove the planned-command
label from README/QUICKSTART and claim verified public PyPI installation.

Review and hashing do not solve omitted natural-language requirements, stale-target
or incomplete acceptance checks, malicious same-user rewriting of contract/digest,
or legacy metadata-removal ambiguity. Preserve those existing limitations.
