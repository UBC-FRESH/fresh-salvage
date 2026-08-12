# Contributing

`fresh-salvage` is pre-release research software from the UBC FRESH Lab.
Contributions should keep the repository public-safe, reproducible, and aligned
with the active roadmap phase.

## Development Setup

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .[dev]
```

On Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .[dev]
```

## Local Checks

```bash
python -m ruff check .
python -m pytest
sphinx-build -b html docs _build/html -W
python -m build
twine check dist/*
```

## Workflow

- Check `ROADMAP.md` before starting non-trivial work.
- Use the active phase branch and linked GitHub issues.
- Keep `CHANGE_LOG.md`, roadmap checklists, issue comments, and PR descriptions
  synchronized with completed work.
- Keep CLI commands thin wrappers over importable Python APIs.
- Do not commit private data, raw transcripts, credentials, generated local
  outputs, or machine-specific paths.

See `AGENTS.md` for the full agent and development workflow contract.
