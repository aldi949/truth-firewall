# Contributing

Truth Firewall is an early Windows pilot for bounded local Python tasks. Keep original task requirements, acceptance checks, evidence, and final verdicts distinct. Add a regression test when changing verification behavior. Do not treat worker prose or passing worker-owned tests as completion authority.

## Local checks

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest
```

When reporting a problem, include the command, Python version, platform, and a small reproduction. Redact task text, source, private paths, credentials, and local invocation logs before sharing. Do not commit `.env` files, virtual environments, or `.truth-firewall\runs`.
