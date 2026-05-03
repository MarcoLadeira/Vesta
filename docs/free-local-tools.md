# Free Local Tools

OPcoding can install a local toolchain under `.opcoding-tools/`. This keeps tools project-scoped and avoids global pollution.

## Core Tool Set

- Ruff: fast Python linting/formatting.
- Bandit: Python security linting.
- pip-audit: Python dependency vulnerability audit.
- detect-secrets: baseline-style secret scanning.
- Pyright: Python static type checking.
- markdownlint-cli2: Markdown linting.
- Prettier: formatting for JS/TS/JSON/CSS/Markdown.
- Biome: fast JS/TS formatter/linter.

Install:

```powershell
python -m opcoding tools . install --set core
```

Check:

```powershell
python -m opcoding tools . doctor
```

Run individual tools:

```powershell
python -m opcoding tools . run ruff
python -m opcoding tools . run ruff-format
python -m opcoding tools . run bandit
python -m opcoding tools . run detect-secrets
python -m opcoding tools . run pyright
python -m opcoding tools . run markdownlint
python -m opcoding tools . run prettier
python -m opcoding tools . run biome
```

## Security Tool Set

Optional Go-based tools:

- OSV-Scanner: dependency vulnerability scanning.
- Gitleaks: repository secret scanning.
- actionlint: GitHub Actions workflow linting.

Install:

```powershell
python -m opcoding tools . install --set security
```

These are free/open-source, but OSV vulnerability lookups require network access. Link checkers and vulnerability database lookups are intentionally not run automatically.

## Sources

- [Ruff installation](https://docs.astral.sh/ruff/installation/)
- [Ruff formatter](https://docs.astral.sh/ruff/formatter/)
- [detect-secrets](https://github.com/Yelp/detect-secrets)
- [Gitleaks](https://github.com/gitleaks/gitleaks)
- [OSV-Scanner usage](https://google.github.io/osv-scanner/usage/)
- [Trivy installation](https://trivy.dev/docs/latest/getting-started/installation/)
- [markdownlint-cli2](https://www.npmjs.com/package/markdownlint-cli2)
- [lychee](https://github.com/lycheeverse/lychee)
- [Pyright npm package](https://www.npmjs.com/package/pyright)
- [actionlint GitHub Action](https://github.com/marketplace/actions/actionlint)
