# Issue #406 Auto Fallback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ensure an Auto-mode confirmation retries the exact configured provider offered to the user, never an unconfigured free-tier model or a recomputed route.

**Architecture:** `handle_gui_message` already selects a locally-known, available fallback and returns its ID with a confirmation card. The web UI will carry that ID into the confirmed retry, converting the provisional Auto request into an explicit account or free-model request. The backend candidate filter will require an explicit availability signal so only catalog entries that OPai can use are offered.

**Tech Stack:** Python 3.10+ (`unittest`), Electron/web JavaScript, Playwright fixtures.

## Global Constraints

- No provider completion is invoked by tests; use mocked bridge replies and catalog fixtures only.
- The first cloud request remains explicit user consent (`allowCloud: true`).
- Prefer configured free API models; otherwise use a connected account; otherwise present the existing honest setup guidance.
- Preserve local-first routing and the existing no-loop account/free failure handling.

---

### Task 1: Preserve the selected fallback through UI confirmation

**Files:**
- Modify: `opai/assets/web/app.js:1942-1944`
- Test: `opai/assets/web/__tests__/e2e/chat-core.spec.js:78-96`

**Interfaces:**
- Consumes: `needs_auto_confirmation` responses with `fallbackModelId`.
- Produces: a second bridge request whose `model` is `fallbackModelId` and whose `allowCloud` is `true`.

- [x] **Step 1: Write the failing browser regression test**

```js
expect(second.model).toBe("free:groq:openai/gpt-oss-120b");
expect(second.allowCloud).toBe(true);
```

- [x] **Step 2: Run the single test and verify it fails because the second request still has `model: "auto"`**

Run: `npx playwright test opai/assets/web/__tests__/e2e/chat-core.spec.js --grep "Auto fallback names"`

- [x] **Step 3: Send the exact fallback model on confirmation**

```js
const fallbackModelId = String(r.fallbackModelId || "").trim();
send(Object.assign({}, state.lastSend || {}, { model: fallbackModelId, allowCloud: true }));
```

- [x] **Step 4: Re-run the browser regression test and verify it passes**

Run: `npx playwright test opai/assets/web/__tests__/e2e/chat-core.spec.js --grep "Auto fallback names"`

### Task 2: Only offer explicitly available fallback candidates

**Files:**
- Modify: `opaihub/gui_pipeline.py:1061-1087`
- Test: `tests/test_reliable_ai_controls.py:527-555`

**Interfaces:**
- Consumes: model catalog entries with `kind`, `available`, `id`, and `label`.
- Produces: `needs_auto_confirmation` only for a free/account entry marked `available: true`; otherwise produces existing no-model guidance.

- [x] **Step 1: Write failing backend fixtures**

```python
models = {"models": [
    {"id": "free:gemini:unverified", "kind": "free"},
    {"id": "account:claude:haiku", "kind": "account", "available": True},
]}
assert result["fallbackModelId"] == "account:claude:haiku"
```

- [x] **Step 2: Run the focused Python test and verify the pre-fix predicate admits entries without an explicit availability result**

Run: `python -m pytest tests/test_reliable_ai_controls.py -k auto -q`

- [x] **Step 3: Require `item.get("available") is True` for both free and account candidates**

```python
if item.get("kind") == "free" and item.get("available") is True
```

- [x] **Step 4: Run focused backend tests and verify they pass**

Run: `python -m pytest tests/test_reliable_ai_controls.py tests/test_message_contract.py -q`

### Task 3: Release verification and review

**Files:**
- Verify: `tests/test_reliable_ai_controls.py`
- Verify: `tests/test_message_contract.py`
- Verify: `opai/assets/web/__tests__/e2e/chat-core.spec.js`

- [x] **Step 1: Run the relevant Python and browser suites**

Run: `python -m pytest tests/test_reliable_ai_controls.py tests/test_message_contract.py -q`

Run: `npm run test:e2e -- --grep "Auto fallback"`

- [x] **Step 2: Inspect the final diff and request independent review**

Run: `git diff --check && git diff --check origin/main...HEAD`

### Task 4: Exclude accounts with known failed health

**Files:**
- Modify: `opai/app_state.py:529-630`
- Modify: `opaihub/gui_pipeline.py:1061-1115`
- Test: `tests/test_provider_connections.py`
- Test: `tests/test_reliable_ai_controls.py`

**Interfaces:**
- Consumes: local connection-health history containing `providerId`, `authStatus`, and `safeDiagnostic`.
- Produces: disabled catalog entries and no Auto fallback for account statuses `misconfigured`, `provider_unavailable`, `invalid`, `expired`, or `disconnected`.

- [x] **Step 1: Write failures for an expired account in both catalog and Auto selection**

```python
assert model["available"] is False
assert result["status"] == "needs_model"
```

- [x] **Step 2: Use cached local health only; do not add a provider probe**

```python
provider_connection_doctor(..., include_cli_versions=False, include_history=True)
```

- [x] **Step 3: Re-run account-health and fast-payload regressions**

Run: `python -m pytest tests/test_reliable_ai_controls.py -k "auto_ or FastPayloadTests" -q`

- [x] **Step 4: Re-request independent review after the health change**

- [ ] **Step 3: Create, push, and merge a PR that closes #406**

Run: `gh pr create --repo MarcoLadeira/OPai --base main --head codex/issue-406-auto-fallback --title "fix(auto): retry the configured fallback" --body "Closes #406"`
