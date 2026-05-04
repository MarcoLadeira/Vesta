# Contributing To OPai

Thanks for helping OPai become a low-cost, local-first AI coding hub.

## Development Setup

```sh
git clone https://github.com/MarcoLadeira/OPai.git
cd OPai
python -m pip install -e . --no-deps
python -m unittest discover -s tests
python -m opaihub validate
```

## Local Rules

- Prefer deterministic scripts, tests, linters, and cached context before AI.
- Keep new tools disabled by default unless they are free, local, and safe.
- Never commit secrets, tokens, generated caches, local logs, or private project data.
- Add or update tests for behavior changes.
- Run `python -m ruff check .` and `python -m unittest discover -s tests` before opening a PR.

## Pull Requests

Use a branch name like `codex/fix-route-costs` or `feature/local-model-setup`.

PRs should include:

- What changed.
- Why it matters for cost, safety, or coding power.
- Verification commands and results.
- Any new permissions, network use, or install requirements.

## Tool Registry Changes

Every new tool must declare cost risk, permission risk, install status, required env vars, enabled-by-default status, and a health check when practical.
