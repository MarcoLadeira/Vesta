# Publishing OPai 0.2.0 Alpha.1

OPai should be published from the `codingOP` directory as its own git repository.
Do not publish it from the parent website repository.

## Local Checks

Run:

```powershell
python -m ruff format --check .
python -m ruff check .
python -m unittest discover -s tests
python -m opaihub validate
python -m opcoding tools . run bandit
python -m opcoding tools . run pip-audit
python -m opcoding tools . run detect-secrets
python -m opcoding tools . run markdownlint
python scripts\smoke-install.py
python -m opai publish status
python -m opai benchmark run --suite max --mode both
python -m opai benchmark gate --min-effectiveness-index 95 --require-risk-blocks
```

## First Git Repo

From `codingOP`:

```powershell
git init -b main
git status --short
git add .
git commit -m "Release OPai 0.2.0 alpha.1"
git remote add origin <your-git-url>
git push -u origin main
```

## User Install Story

Alpha GitHub install:

```powershell
pipx install git+https://github.com/<owner>/<repo>.git
op activate --repair --shell-aliases
```

Local developer install:

```powershell
python -m pip install -e . --no-deps
op activate --repair --shell-aliases
op status
```

Both `op` and `opai` launch OPai. The legacy OPcoding CLI remains available as `opcoding`.

## Superpowers

OPai activation treats Superpowers as part of OPai when Superpowers exists under:

```text
~/.codex/superpowers/skills
```

Activation ensures the bridge exists at:

```text
~/.agents/skills/superpowers
```

Restart AI clients after first activation so skills are rediscovered.
