# Changelog

## 0.1.1 Pre-Alpha

- Severely reduced default token and credit usage:
  - `opai route` now returns compact evidence by default.
  - Release, deploy, publish, and security tasks start with local preflight instead of strong-model routing.
  - `op ask` no longer stores full prompts by default.
  - Default context cap lowered to 6,000 characters with a 12,000-character hard guard.
  - Default budgets lowered to `$0.50/day`, `$5/month`, and `$0.10` per-task soft limit.
- Added public repo community files, issue forms, discussion forms, support policy, PR template, and security policy.
- Updated installer docs around the single-command OPai install.
- Added OPai skills/model-routing MVP and optional disabled-by-default cost-saving tools.

## 0.1.0 Pre-Alpha

- Added OPai local-first AI hub CLI.
- Added `op` and `opai` command entry points.
- Added automatic project activation for AI coding clients.
- Added Superpowers bridge support through OPai activation.
- Added packaged hub registries, prompts, docs, MCP examples, and security policy.
- Added evidence router, safe command runner, security scans, and isolated install smoke test.
