# Account Result Truth Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Claude and Codex account requests distinguish provider-native failures from real model answers, so authentication and execution failures never appear as completed chat replies.

**Architecture:** Extend the existing provider stream parsers with a small `error` field sourced only from authoritative structured events. Reconcile parser errors, process diagnostics, exit status, and captured answer text in `AccountRunner`, then keep the existing GUI/app-state structured-error contract as the single terminal-state path. Preserve genuine streamed answers on non-zero exit when no native failure event or known provider diagnostic exists.

**Tech Stack:** Python 3.11+, subprocess JSONL adapters, pytest/unittest, JavaScript/Vitest, Playwright, GitHub Actions.

---

### Task 1: Reproduce provider-native stream failures

**Files:**
- Modify: `tests/test_activity.py`
- Modify: `tests/test_ai_model_bugfixes.py`
- Test: `tests/test_activity.py`
- Test: `tests/test_ai_model_bugfixes.py`

- [ ] **Step 1: Write failing Claude parser tests**

Add fixtures for `result` records with `is_error: true`, `subtype: error_during_execution`, and an authentication message. Assert the parser returns no answer text and exposes the diagnostic through `error`.

- [ ] **Step 2: Write failing Codex parser tests**

Add fixtures for top-level `turn.failed` and `error` records. Assert both preserve the full diagnostic in `error`, emit an error activity event, and never produce answer text.

- [ ] **Step 3: Write failing runner tests**

Simulate Claude and Codex subprocesses that report structured failures with exit code zero and non-zero. Assert `AccountRunner.stream()` returns a normalized error, does not call `on_text`, and does not let a Codex last-message error override the structured failure.

- [ ] **Step 4: Verify the tests fail for the expected contract gap**

Run:

```powershell
python -m pytest tests/test_activity.py tests/test_ai_model_bugfixes.py -q
```

Expected: failures show missing parser `error` data and current error text being returned as successful `text`.

### Task 2: Implement structured failure reconciliation

**Files:**
- Modify: `opai/activity.py`
- Modify: `opaihub/accounts.py`
- Test: `tests/test_activity.py`
- Test: `tests/test_ai_model_bugfixes.py`

- [ ] **Step 1: Add parser error output**

Return `error: ""` in both parser result shapes. For Claude, populate it when `is_error` is true or the result subtype starts with `error_`; for Codex, populate it for top-level `turn.failed` and `error` records.

- [ ] **Step 2: Reconcile runner evidence before success**

Collect parser diagnostics separately from raw JSON. Normalize authoritative parser failures first. If none exists, normalize stderr/non-JSON diagnostics and use a known failure only when the process failed or the provider emitted no valid answer. Return real text on non-zero exit only when no authoritative or known provider failure exists.

- [ ] **Step 3: Prevent failed payloads from streaming as answer text**

Do not invoke `on_text` for structured provider errors or a Codex output-file value that classifies as a provider failure in a failed turn.

- [ ] **Step 4: Verify focused tests pass**

Run:

```powershell
python -m pytest tests/test_activity.py tests/test_ai_model_bugfixes.py -q
```

Expected: all focused tests pass.

### Task 3: Harden blocking calls and connection probes

**Files:**
- Modify: `tests/test_provider_connections.py`
- Modify: `tests/test_ai_model_bugfixes.py`
- Modify: `opaihub/accounts.py`
- Test: `tests/test_provider_connections.py`
- Test: `tests/test_ai_model_bugfixes.py`

- [ ] **Step 1: Write failing probe tests**

Assert a zero-exit status command that explicitly says `not logged in` is not promoted to connected, while Codex's successful stderr status remains connected.

- [ ] **Step 2: Write failing blocking-run tests**

Assert `complete()` returns normalized `AUTH_INVALID`, rate-limit, timeout, and model errors instead of putting diagnostics in `text`; assert a normal answer containing the words `401` remains an answer when the provider reports success.

- [ ] **Step 3: Implement probe and complete-path classification**

Parse safe status output before accepting return code zero. In `complete()`, use Claude's JSON `is_error`/subtype and process status/diagnostics; in Codex, classify failed-process output before returning it as text.

- [ ] **Step 4: Invalidate stale connection evidence after auth failures**

Add a narrow cache invalidation helper and call it when an account execution returns `AUTH_INVALID`, `AUTH_EXPIRED`, or `AUTH_MISSING`.

- [ ] **Step 5: Verify focused tests pass**

Run:

```powershell
python -m pytest tests/test_provider_connections.py tests/test_ai_model_bugfixes.py -q
```

Expected: all focused tests pass.

### Task 4: Verify terminal-state and security behavior

**Files:**
- Modify: `tests/test_streaming.py`
- Modify: `tests/test_message_contract.py`
- Modify: `opai/assets/web/__tests__/e2e/chat.spec.js` only if an existing fixture cannot express the failure
- Test: `tests/test_streaming.py`
- Test: `tests/test_message_contract.py`

- [ ] **Step 1: Add pipeline regression tests**

Feed the real account runner a provider-native failure fixture. Assert status is `failed`, exactly one `provider_auth_failed` terminal event is emitted, `completed` is absent, no ledger success is written, and the technical message is deduplicated/redacted.

- [ ] **Step 2: Add duplicate and secret-shape tests**

Assert adjacent stdout/stderr duplicates collapse to one message and API-key, bearer-token, and assignment-shaped values never reach the returned error payload.

- [ ] **Step 3: Verify focused pipeline tests pass**

Run:

```powershell
python -m pytest tests/test_streaming.py tests/test_message_contract.py tests/test_provider_contract.py -q
```

Expected: all focused tests pass.

### Task 5: Full verification, PR, and merge

**Files:**
- Modify: `docs/superpowers/plans/2026-07-04-account-result-truth.md`

- [ ] **Step 1: Run the complete local gate**

Run Python tests, Ruff formatting/lint, Bandit, secret scan, JavaScript unit tests, Playwright, package build/install smoke, and dependency audits using the repository's documented commands.

- [ ] **Step 2: Review the final diff**

Confirm the diff contains no credentials, no unrelated worktree changes, no destructive operations, and no broad provider-call behavior beyond result classification.

- [ ] **Step 3: Commit and push**

Commit the tests, implementation, and this plan on `codex/account-result-truth`, then push to `origin`.

- [ ] **Step 4: Open the pull request**

Describe the root cause, provider-specific evidence, preserved non-zero-answer behavior, tests, security constraints, and the fact that no live paid/cloud completion was made.

- [ ] **Step 5: Wait for all required checks and merge**

Do not merge until every required PR check succeeds and the PR is mergeable. Merge through GitHub, then verify the post-merge `main` workflow is green.
