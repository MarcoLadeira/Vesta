# Issue #621 mandatory CI implementation plan

> Execute this plan in one cohesive branch and PR. Use red/green tests for each
> behavioral change and retain exact command/run evidence.

## 1. Freeze the qualification contract

- Expand `.github/required-checks.json` into the canonical, versioned gate
  inventory.
- Add failing architecture tests for triggers, stable names, permissions,
  immutable action pins, exact candidate checkout, trust boundaries, rerun-safe
  artifacts, and manifest/workflow parity.

## 2. Make local evidence truthful

- Add failing unit tests for required executable/module absence, timeout,
  security/product/infrastructure classification, candidate mismatch, inventory
  deletion, component selection, and bounded redacted diagnostics.
- Refactor `scripts/ci_local.py` into real `fast`, `full`, `native`,
  `provider-canary`, and `release` contracts with exact-SHA evidence.
- Add an enforcing secret-scan wrapper and test it against controlled findings.

## 3. Make hosted qualification automatic

- Bind every checkout and local evidence manifest to the exact event candidate.
- Run the three required components automatically for PRs and fresh `main`/
  release-branch commits; run full/native qualification on schedule.
- Harden caches, dependency installation, browser diagnostics, timeouts,
  concurrency, and immutable evidence upload names.

## 4. Enforce trusted infrastructure boundaries

- Add a hosted self-hosted-runner health preflight and main-only execution guard.
- Add a protected provider-canary workflow with non-production and budget gates.
- Make unsigned tagged native rehearsals automatic while keeping production
  signing protected; bind every downloaded artifact/evidence item to the same
  run and candidate SHA.

## 5. Make release readiness fail closed

- Add candidate identity and mandatory evidence validation to release preflight.
- Reject skipped tests/artifacts and stale/mismatched qualification manifests.
- Make workflow artifacts rerun-idempotent and require all evidence files.

## 6. Fix downstream required-check consumption

- Load the exact manifest in the GitHub workflow adapter.
- Reject missing, skipped, neutral, stale, duplicate, or wrong-source required
  checks and add unit coverage for moving PR heads.

## 7. Document, verify, and publish

- Update CI, self-hosted, provider, native, release, and administrator docs.
- Run focused tests at each red/green cycle, then the complete Python, frontend,
  browser, architecture, workflow, missing-tool, deliberate-failure, SHA,
  rerun, and native drills.
- Review the final diff independently; commit, push, open the requested PR, and
  inspect every real GitHub Actions result. Fix repository-controlled failures;
  record GitHub billing/plan/runner/environment blockers without claiming green.
