# Error, Empty, and Loading State Framework Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Provide consistent, accessible, actionable error, empty, loading, and degraded states across OPai's core web views.

**Architecture:** Add one reusable state-card renderer in `app.js`; preserve the existing rich chat error card as the streaming variant. Dashboard, settings, sidebar recents, and global announcements consume the shared semantics without adding backend reason classes.

**Tech Stack:** Vanilla browser JavaScript, CSS, Playwright mock bridge, existing Qt bridge contracts.

## Global Constraints

- Consume existing canonical reason/recovery data; do not introduce a second error taxonomy.
- Never retry a model call or spend-triggering operation automatically.
- Error detail is redacted and opt-in; no raw prompt, secret, or stack trace is default UI.
- Loading copy must name real observed work and must not show simulated progress.

---

### Task 1: Establish shared accessible state rendering

**Files:**
- Modify: `opai/assets/web/app.js`
- Modify: `opai/assets/web/styles.css`
- Modify: `opai/assets/web/index.html`
- Test: `opai/assets/web/__tests__/e2e/errors-recovery.spec.js`

- [x] Add a failing test that asserts a terminal chat error exposes `role="alert"`, a plain reason, and a visible safe action.
- [x] Run `npx --no-install playwright test opai/assets/web/__tests__/e2e/errors-recovery.spec.js` and verify the new assertion fails.
- [x] Add `stateCardHtml(state)` for loading, empty, error, and degraded cards; use semantic status/alert roles and action data attributes.
- [x] Update `renderErrorCard` to use alert semantics while retaining redaction and existing recovery handlers.
- [x] Make `#toast` a polite status live region.
- [x] Re-run the targeted Playwright test.

### Task 2: Apply the state map to dashboard and settings

**Files:**
- Modify: `opai/assets/web/app.js`
- Test: `opai/assets/web/__tests__/e2e/async-data.spec.js`
- Test: `opai/assets/web/__tests__/e2e/loading-states.spec.js`
- Test: `opai/assets/web/__tests__/e2e/errors-recovery.spec.js`

- [x] Add failing mock-bridge tests for dashboard/settings bridge failures and valid empty payloads; assert title, reason, and retry action.
- [x] Run only these tests and verify they fail against bare `Loading…` / `Couldn't load` text.
- [x] Replace dashboard/settings bare text with `renderViewState`; reuse their existing request functions for a manual retry only.
- [x] Treat malformed bridge JSON as a typed UI failure without exposing parser detail.
- [x] Re-run the focused browser tests.

### Task 3: Complete sidebar recents and copy audit

**Files:**
- Modify: `opai/assets/web/app.js`
- Modify: `docs/UX_WRITING_GUIDE.md`
- Test: `opai/assets/web/__tests__/e2e/folder.spec.js`
- Test: `opai/assets/web/__tests__/e2e/accessibility.spec.js`

- [x] Add a failing empty-recents test that asserts explanatory copy and a New chat action.
- [x] Run the targeted tests and verify the old passive sentence fails the action assertion.
- [x] Render the shared empty card in recents, wire it to `startNewChat`, and document the exact `what happened → why → next action` copy pattern.
- [x] Re-run targeted browser tests.

### Task 4: Audit, verify, and publish the state map

**Files:**
- Create: `docs/QA_E2E_ISSUE391_2026-07-18.md`
- Modify: `docs/superpowers/plans/2026-07-18-issue-391-state-framework.md`
- Test: `opai/assets/web/__tests__/e2e/*.spec.js`

- [x] Document each core view × loading/empty/error/offline behavior, its canonical source, and its action safety.
- [x] Run `npx --no-install playwright test` (341 passed), `python -m ruff check` (passed), and `git diff --check` (passed). `python -m ruff format --check` reports ten pre-existing Python files outside this web-only change.
- [ ] Mark the audited map complete in the GitHub issue and open a PR that closes #391.
