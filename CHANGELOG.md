# Changelog

## 0.2.0 Alpha.1

- Added the public static launch funnel under `site/`, ready for Cloudflare
  Pages deployment.
- Added launch CTAs for Free Alpha, Founding Pro, Team Pilot, and opt-in
  benchmark proof reports.
- Added GitHub intake issue forms for Founding Pro, Team Pilot, and benchmark
  proof reports.
- Promoted OPai to public alpha metadata (`0.2.0a1` package version,
  `v0.2.0-alpha.1` release tag).
- Documented the 30-day go-to-market plan, no-code payment loop, public usage
  signals, and release checklist.
- Kept CLI telemetry off by default; site analytics are limited to Cloudflare
  Web Analytics.

## 0.1.1 Pre-Alpha

- Severely reduced default token and credit usage:
  - `opai route` now returns compact evidence by default.
  - `opai route` has an even smaller AI-facing summary path; full evidence is opt-in.
  - `opai slim` writes AI-client ignore files and removes generated project bloat.
  - AI CLI launch wrappers print a one-line badge by default; welcome graphics are opt-in.
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
