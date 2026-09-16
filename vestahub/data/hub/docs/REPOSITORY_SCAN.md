# Repository Scan

Current OPcoding capabilities found during Phase 1 scan:

- Python CLI package: `opcoding/`.
- Hub CLI package: `vestahub/`.
- Script wrappers: `scripts/op-*.py`.
- PowerShell launchers: `bin/op.ps1`, `bin/op-hub.ps1`.
- Existing coding agent catalog: `agents/catalog.yaml`.
- Existing MCP profiles: `mcp/profiles/`.
- Existing configs: budgets, routing, permissions, model adapters, automation, Morph, free tools.
- Existing prompt library: `prompts/`.
- Existing workflows: `workflows/`.
- Existing docs: `docs/`.
- Project-local free tool stack: `.opcoding-tools/` with Ruff, Bandit, pip-audit, detect-secrets, Pyright, markdownlint-cli2, Prettier, Biome, OSV-Scanner, Gitleaks, and actionlint.
- Superpowers skill junction: `~/.agents/skills/superpowers`.

Gaps addressed:

- Added central `hub/` namespace.
- Added tool, agent, workflow, MCP, and model registries.
- Added hub security, cost, context, MCP, and prompt policies.
- Added minimal `op-hub` CLI.

Compatibility risks:

- `.opcoding-tools/` must stay ignored by scans, lint, secret scans, and git.
- Some health checks may be noisy until project-specific overlays tune them.
- Cloud/paid tools must remain disabled by default.
