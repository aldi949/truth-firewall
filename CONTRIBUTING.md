# Contributing

Truth Firewall is an early Windows pilot for bounded local Python tasks. Keep original task requirements, acceptance checks, evidence, and final verdicts distinct. Add a regression test when changing verification behavior. Do not treat worker prose or passing worker-owned tests as completion authority.

## Local checks

For development, clone the source and use a virtual environment. Normal users can use the pipx source/artifact installation described in [QUICKSTART.md](QUICKSTART.md); the PyPI command remains planned until publication and public verification.

```powershell
git clone https://github.com/aldi949/truth-firewall.git
cd truth-firewall
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest
```

When reporting a problem, include the command, Python version, platform, and a small reproduction. Redact task text, source, private paths, credentials, and local invocation logs before sharing. Do not commit `.env` files, virtual environments, or `.truth-firewall\runs`.

For release checks, build with `python -m build`, validate with `python -m twine check --strict dist/*` and `python tools/check_dist.py dist`, then run `tools/check_pipx.py` against the wheel from a new temporary directory. See [docs/PUBLISHING.md](docs/PUBLISHING.md). These commands do not publish.
