# Contributing

The published 0.1.0 release is a Windows pilot for bounded local Python tasks. The V0.2 candidate adds platform support that must be checked on hosted runners. Keep original task requirements, acceptance checks, evidence, and final verdicts distinct. Add a regression test when changing verification behavior. Do not treat worker prose or passing worker-owned tests as completion authority.

## Local checks

For development, clone the source and use a virtual environment. Users can install the published 0.1.0 release with `pipx install truth-firewall`. Candidate features on this branch require a local candidate install until a later release is explicitly approved and published.

```powershell
git clone https://github.com/aldi949/truth-firewall.git
cd truth-firewall
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest
```

When reporting a problem, include the command, Python version, platform, and a small reproduction. Redact task text, source, private paths, credentials, and local invocation logs before sharing. Do not commit `.env` files, virtual environments, or `.truth-firewall\runs`.

For release checks, build with `python -m build`, validate with `python -m twine check --strict dist/*` and `python tools/check_dist.py dist`, then run `tools/check_pipx.py` against the wheel from a new temporary directory. See [docs/PUBLISHING.md](docs/PUBLISHING.md). These commands do not publish.
