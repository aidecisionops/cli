# AGENTS.md

## Project Overview

**dops** (DecisionOps CLI) - a Python CLI for binding git repos to DecisionOps orgs/projects, installing editor integrations (skills & MCP configs), and managing architectural decisions. Distributed as cross-platform binaries via PyInstaller.

## Quick Reference

```bash
# Dev setup
python3 -m venv .venv && . .venv/bin/activate && pip install -e .

# Run CLI
python -m dops --help

# Run tests
python -m unittest discover -s tests -v

# Build binary
pip install pyinstaller
pyinstaller --onefile --name dops --hidden-import=certifi --collect-data=certifi --hidden-import=_ssl dops_bootstrap.py

# Publish the next patch release
bash ./publish-new-version.sh
```

## Project Structure

- `dops/` - Main Python package
  - `cli.py` - Entry point, argument parser
  - `command_groups/` - Modular commands (auth, decisions, operations, platforms, repo, update)
  - `api_client.py` - HTTP client for DecisionOps API
  - `auth.py` - OAuth2 PKCE flow & token management
  - `installer.py` - Generates platform-specific skill & MCP config files
  - `ui.py` - Rich-based terminal UI components
  - `platforms.py` - Editor platform definitions (codex, claude-code, cursor, vscode, antigravity)
  - `config.py` - Environment defaults & configuration
- `tests/` - Unit tests (unittest framework)
- `worker/src/index.js` - Cloudflare Worker for installer distribution
- `install/` - Shell/PowerShell installer scripts
- `pyproject.toml` - Package config (Python >=3.13, setuptools)

## Code Conventions

- Python 3.13+, `from __future__ import annotations` in all modules
- Type hints throughout; dataclasses with `slots=True`
- Rich library for terminal output; prompt-toolkit for interactive prompts
- Command groups follow pattern: `register_*_commands()` + `run_<command>()` functions
- Common utilities in `command_groups/shared.py`
- No async/await - uses threading for OAuth HTTP server

## Testing

- Framework: Python `unittest`
- Run all: `python -m unittest discover -s tests -v`
- Tests use subprocess invocation of `python -m dops` for integration testing

## Regression Guardrails

- Prefer smaller, focused changes over broad cross-cutting refactors when possible.
- After adding a happy-path feature, audit failure paths explicitly: corrupt files, empty files, malformed server responses, interrupted flows, and retry exhaustion.
- Do not leave raw `json.loads()` / `tomllib.loads()` on user-managed files without error handling.
- Avoid `assert` in runtime CLI/auth flows; use explicit checks and user-facing errors.
- When adding shared behavior like retries, typo suggestions, or string-distance helpers, extract utilities instead of duplicating logic.
- For packaging or startup changes, smoke test the built binary with more than `--version` when possible.
- Before closing a change, verify:
  - corrupted-file cases are covered
  - malformed API responses are covered
  - retries preserve useful failure context
  - no duplicate helper logic was introduced
  - no new broad exception swallowing was added
  - the full test suite passes

## CI/CD

- Release: triggered by `v*` tags, builds binaries on Ubuntu/macOS/Windows (x64/arm64)
- Worker deploy: on push to main when `worker/` or `wrangler.jsonc` changes
- Prefer `bash ./publish-new-version.sh` for releases. It bumps versions, runs tests, commits, tags, pushes, waits for GitHub Actions, and writes a release report.

## Dependencies

- `certifi` - SSL CA certs
- `rich` - Terminal formatting
- `prompt-toolkit` - Interactive prompts
