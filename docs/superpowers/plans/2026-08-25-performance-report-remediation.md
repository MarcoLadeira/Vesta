# Performance Report Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the remaining reproducible performance bottlenecks from the supplied report without duplicating fixes already present on `main`.

**Architecture:** Preserve the existing append-only evidence and GUI contracts. Add bounded, signature-based caches at read hot paths, use persisted heads for constant-time append metadata, and keep honest truncation state when live activity becomes archived.

**Tech Stack:** Python 3.10+, unittest/pytest, vanilla JavaScript, Vitest, Playwright, GitHub Actions.

---

### Task 1: Make audit appends and headline reads bounded

**Files:**
- Modify: `opaihub/audit.py`
- Test: `tests/test_audit_performance.py`

- [ ] **Step 1: Write failing tests**

Assert that two appends with a current checkpoint do not re-read the JSONL log, repeated summaries parse once, a file change invalidates the summary, and a stale checkpoint falls back to verified recovery.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_audit_performance.py -q`

Expected: failures showing `_last_entry` and `summarize_audit` repeatedly read the full log.

- [ ] **Step 3: Implement the minimal bounded path**

Persist the audit file size with the checkpoint, trust the checkpoint only when its size matches the durable log, otherwise verify the chain before appending, and cache `summarize_audit` by `(size, mtime_ns)` while returning defensive copies.

- [ ] **Step 4: Verify and commit**

Run: `python -m pytest tests/test_audit_performance.py tests/test_audit_persistence.py tests/test_audit_integrity.py tests/test_governance.py -q`

Commit: `perf(audit): make append and summary reads bounded`

### Task 2: Coalesce repeated workspace Git probes

**Files:**
- Modify: `opai/app_state.py`
- Test: `tests/test_workspace_summary_cache.py`

- [ ] **Step 1: Write failing tests**

Assert that unchanged calls execute `git ls-files` and branch discovery once, return defensive copies, and invalidate when the Git index or HEAD changes.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_workspace_summary_cache.py -q`

Expected: repeated calls invoke `_git_text` four times instead of twice.

- [ ] **Step 3: Implement the minimal cache**

Resolve directory and worktree-file `.git` layouts, fingerprint `index` and `HEAD` with size and nanosecond mtime, and memoize `workspace_summary` under a lock.

- [ ] **Step 4: Verify and commit**

Run: `python -m pytest tests/test_workspace_summary_cache.py tests/test_desktop_gui.py tests/test_gui_web.py -q`

Commit: `perf(workspace): cache unchanged Git summaries`

### Task 3: Keep event truncation honest after completion

**Files:**
- Modify: `opai/assets/web/app.js`
- Test: `opai/assets/web/__tests__/e2e/event-cap.spec.js`

- [ ] **Step 1: Write a failing browser regression**

Complete a capped synthetic turn and assert that the archived activity timeline still includes the accurate truncation marker.

- [ ] **Step 2: Run the test to verify it fails**

Run: `npx playwright test opai/assets/web/__tests__/e2e/event-cap.spec.js`

Expected: the marker disappears when `timelineRows()` freezes the completed activity.

- [ ] **Step 3: Share one truncation-row renderer**

Render the same honest marker in both the live keyed timeline and the completed HTML snapshot, without restoring dropped events or claiming an unavailable ledger view.

- [ ] **Step 4: Verify and commit**

Run: `npx playwright test opai/assets/web/__tests__/e2e/event-cap.spec.js opai/assets/web/__tests__/e2e/timeline-perf.spec.js`

Commit: `fix(activity): preserve truncation marker after completion`

### Task 4: Audit remaining report categories and close only measured gaps

**Files:**
- Modify only modules with a reproduced blocking or repeated-work path.
- Test alongside each affected module.

- [ ] **Step 1: Run focused static and timing audits**

Inspect GUI bridge slots, async entry points, JSON hot paths, local-provider transport, and CI definitions. Compare current code with the report and existing performance acceptance tests.

- [ ] **Step 2: Add a failing regression for each measured gap**

Each regression must reproduce blocking, unbounded growth, or repeated work and must fail before production changes.

- [ ] **Step 3: Apply one root-cause fix per commit**

Do not add speculative serializers, network dependencies, or CI complexity when local evidence does not demonstrate a benefit.

- [ ] **Step 4: Run final focused verification**

Run the changed Python tests, Vitest activity tests, Playwright performance/event-cap tests, `ruff check` on changed Python files, `git diff --check`, and the repository secret scanner before marking the draft PR ready.

