# Provider Connection Doctor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver one-view provider diagnostics and a guided interactive sign-in flow for issues #166 and #167.

**Architecture:** Add a secret-safe diagnostics/login service in `opaihub.accounts`, expose login asynchronously through the existing Qt bridge, and render the resulting structured data in the existing Settings and error-card UI. Reuse the current provider probe and failed-request retry contracts.

**Tech Stack:** Python 3.10+, PySide6/QWebChannel, vanilla JavaScript/CSS, unittest, Playwright.

---

### Task 1: Diagnostics and login service

**Files:**
- Modify: `opaihub/accounts.py`
- Create: `tests/test_connection_doctor.py`

- [ ] Write failing tests importing `provider_connection_doctor` and `interactive_provider_login`, asserting normalized account/API diagnostics, no secret values, fixed provider login argv, sanitized environment use, visible-terminal flags, forced post-exit probing, and fail-closed unknown/missing providers.
- [ ] Run `python -m unittest tests.test_connection_doctor -v` and verify the imports fail before implementation.
- [ ] Implement a locked safe-history map, cached local CLI-version reader, normalized doctor aggregation, allowlisted login commands, visible-terminal launcher, timeout/error results, and forced post-login probe.
- [ ] Run `python -m unittest tests.test_connection_doctor tests.test_provider_connections tests.test_provider_env_sanitization -v` and verify all pass.

### Task 2: Bridge and Settings surface

**Files:**
- Modify: `opai/gui_web.py`
- Modify: `opai/assets/web/app.js`
- Modify: `opai/assets/web/styles.css`
- Modify: `opai/assets/web/__tests__/e2e/mock-bridge.js`
- Create: `opai/assets/web/__tests__/e2e/connection-doctor.spec.js`

- [ ] Write failing Playwright scenarios that expect a labelled Connection Doctor card, CLI/credential/env/check/error evidence, a Settings sign-in action, and no secret values.
- [ ] Add `connectionDoctor` to `settings_payload` and a `providerLoginReady` signal plus `startProviderLogin(provider, requestId)` worker-backed slot.
- [ ] Render compact diagnostic cards with Test, Sign in, Disconnect, and Repair actions while preserving existing free-provider key controls.
- [ ] Extend the browser mock with correlated login calls/results and run `npx playwright test opai/assets/web/__tests__/e2e/connection-doctor.spec.js`.

### Task 3: Auth-error retry and verification

**Files:**
- Modify: `opai/assets/web/app.js`
- Modify: `opai/assets/web/__tests__/e2e/connection-doctor.spec.js`

- [ ] Add a failing scenario where an `AUTH_MISSING` error offers Sign in, receives a correlated successful login result, and retries exactly the saved failed request once.
- [ ] Add shared login state/event handling; retry only on verified success and never for stale, failed, or unverified events.
- [ ] Run focused Python and Playwright suites, then Ruff, Bandit, full Python, JavaScript unit, full Playwright, registry validation, dependency audits, and isolated install smoke.
- [ ] Review the final diff for secrets and unrelated files, commit, push, open a PR closing #166/#167, monitor required CI, and merge only if every gate passes.

