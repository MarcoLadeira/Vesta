# Vesta Auth and Message Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make provider authentication, terminal message state, activity, branding, recovery, and connection diagnostics truthful and safe across the Vesta desktop chat.

**Architecture:** Normalize every provider process outcome at the Python CLI boundary into one safe error/connection contract. Preserve that contract through the pipeline and Qt bridge, then let a pure JavaScript message state machine drive Vesta-first rendering and reject invalid terminal transitions.

**Tech Stack:** Python 3.10+ standard library, PySide6 bridge contract, plain JavaScript, Vitest, Playwright, `unittest`.

---

### Task 1: Provider contract

**Files:**
- Create: `vesta/provider_contract.py`
- Create: `tests/test_provider_contract.py`

- [ ] **Step 1: Write failing normalization tests**

```python
def test_401_is_auth_invalid():
    error = normalize_provider_error("claude", "401 Invalid authentication credentials")
    self.assertEqual(error["code"], "AUTH_INVALID")
    self.assertEqual(error["authStatus"], "invalid")

def test_secret_and_repeated_text_are_sanitized():
    error = normalize_provider_error("claude", "Bearer sk-secret\nBearer sk-secret")
    self.assertNotIn("sk-secret", error["technicalMessage"])
    self.assertEqual(error["technicalMessage"].count("[REDACTED]"), 1)
```

- [ ] **Step 2: Verify RED**

Run: `python -m unittest tests.test_provider_contract -v`

Expected: import failure for `vesta.provider_contract`.

- [ ] **Step 3: Implement the minimal plain-dictionary contract**

```python
def normalize_provider_error(provider, detail, *, returncode=None, timed_out=False):
    safe = dedupe_error_text(redact_secrets(detail))
    code = classify_error_code(safe, returncode=returncode, timed_out=timed_out)
    spec = ERROR_SPECS[code]
    return {"code": code, "authStatus": spec["authStatus"], "title": spec["title"],
            "userMessage": spec["userMessage"], "recoveryActions": list(spec["actions"]),
            "technicalMessage": safe, "provider": provider, "retryable": spec["retryable"]}
```

- [ ] **Step 4: Verify GREEN and regression scope**

Run: `python -m unittest tests.test_provider_contract tests.test_activity -v`

Expected: all tests pass.

- [ ] **Step 5: Commit**

Run: `git add vesta/provider_contract.py tests/test_provider_contract.py && git commit -m "feat: normalize provider authentication errors"`

### Task 2: Truthful account runner outcomes

**Files:**
- Modify: `vestahub/accounts.py:54-68,410-545`
- Modify: `tests/test_streaming.py`
- Modify: `tests/test_copilot_connector.py`

- [ ] **Step 1: Add failing process-outcome tests**

```python
def test_nonzero_auth_exit_is_failure(self):
    proc = FakeProc(stdout='401 Invalid authentication credentials\n', stderr='', returncode=1)
    with mock.patch("vestahub.accounts._popen", return_value=proc):
        result = self.runner.stream("hello")
    self.assertEqual(result["error"]["code"], "AUTH_INVALID")
    self.assertEqual(result["text"], "")
```

- [ ] **Step 2: Verify RED**

Run: `python -m unittest tests.test_streaming.AccountStreamingTests.test_nonzero_auth_exit_is_failure -v`

Expected: missing structured `error`.

- [ ] **Step 3: Capture stdout/stderr and inspect `returncode`**

Use two reader threads tagged `stdout`/`stderr`, wait for both EOFs, and return normalized failure when the exit code is non-zero or output contains a known provider error. Do not include the prompt or command arguments in diagnostics.

- [ ] **Step 4: Verify GREEN**

Run: `python -m unittest tests.test_streaming tests.test_copilot_connector tests.test_cancellation -v`

Expected: all tests pass.

- [ ] **Step 5: Commit**

Run: `git add vestahub/accounts.py tests/test_streaming.py tests/test_copilot_connector.py && git commit -m "fix: preserve provider process failures"`

### Task 3: Connection state and safe health checks

**Files:**
- Modify: `vestahub/accounts.py`
- Modify: `vesta/app_state.py:512-572`
- Create: `tests/test_provider_connections.py`

- [ ] **Step 1: Add failing detected-versus-verified tests**

```python
def test_auth_artifact_is_unverified_not_connected():
    connection = connection_for_account({"id": "claude", "cli_present": True, "authenticated": True})
    self.assertEqual(connection["authStatus"], "unknown")
    self.assertEqual(connection["credentialSource"], "user_account")

def test_claude_status_failure_maps_invalid():
    connection = test_account_connection("claude", run=lambda argv: Completed(1, "", "not logged in"))
    self.assertEqual(connection["authStatus"], "invalid")
```

- [ ] **Step 2: Verify RED**

Run: `python -m unittest tests.test_provider_connections -v`

Expected: connection functions are missing.

- [ ] **Step 3: Implement read-only checks**

Use `claude auth status` and `codex login status`. Copilot remains `unknown` with a safe diagnostic because its installed CLI exposes no status command. Never run a model completion.

- [ ] **Step 4: Verify GREEN**

Run: `python -m unittest tests.test_provider_connections tests.test_copilot_connector -v`

Expected: all tests pass.

- [ ] **Step 5: Commit**

Run: `git add vestahub/accounts.py vesta/app_state.py tests/test_provider_connections.py && git commit -m "feat: expose honest provider connection states"`

### Task 4: Pipeline terminal truth and Vesta activity vocabulary

**Files:**
- Modify: `vesta/activity.py:21-41,158-312`
- Modify: `vesta/app_state.py:633-776`
- Modify: `vestahub/gui_pipeline.py:136-281`
- Modify: `tests/test_activity.py`
- Modify: `tests/test_message_contract.py`
- Modify: `tests/test_streaming.py`

- [ ] **Step 1: Add failing terminal-event tests**

```python
def test_auth_failure_never_emits_completed(self):
    events = []
    result = handle_gui_message(root, "hi", model_id="account:claude:haiku",
        account_runner=AuthFailureRunner(), on_event=events.append)
    self.assertEqual(result["status"], "failed")
    self.assertIn("provider_auth_failed", [e["type"] for e in events])
    self.assertNotIn("completed", [e["type"] for e in events])
```

- [ ] **Step 2: Verify RED**

Run: `python -m unittest tests.test_streaming.PipelineStreamingTests.test_auth_failure_never_emits_completed -v`

Expected: result/event mismatch.

- [ ] **Step 3: Implement canonical terminal mapping**

Emit `provider_checking`, `provider_authenticated`, `provider_auth_failed`, `failed`, `cancelled`, `retrying`, and `completed`. Put raw provider/model IDs in metadata. User-facing titles say Vesta.

- [ ] **Step 4: Verify GREEN**

Run: `python -m unittest tests.test_activity tests.test_message_contract tests.test_streaming tests.test_cancellation -v`

Expected: all tests pass.

- [ ] **Step 5: Commit**

Run: `git add vesta/activity.py vesta/app_state.py vestahub/gui_pipeline.py tests/test_activity.py tests/test_message_contract.py tests/test_streaming.py && git commit -m "fix: make request terminal states truthful"`

### Task 5: Browser message state and branding

**Files:**
- Create: `vesta/assets/web/message-state.js`
- Create: `vesta/assets/web/__tests__/message-state.test.js`
- Modify: `vesta/assets/web/index.html`
- Modify: `vesta/assets/web/app.js:28-35,245-487`
- Modify: `vesta/assets/web/activity.js`

- [ ] **Step 1: Add failing reducer tests**

```javascript
it("rejects completed after failed", () => {
  const failed = transition({ status: "streaming" }, "failed");
  expect(transition(failed, "completed").status).toBe("failed");
});

it("deduplicates retry IDs", () => {
  expect(canApply({ requestId: "new", status: "retrying" }, "old")).toBe(false);
});
```

- [ ] **Step 2: Verify RED**

Run: `npm run test:unit -- vesta/assets/web/__tests__/message-state.test.js`

Expected: module/script missing.

- [ ] **Step 3: Implement reducer and Vesta-first rendering**

The assistant role, pending label, completed response, and primary selector use Vesta names. `advancedLabel`, provider, and model remain in Inspector/details. Error cards consume `result.error` and render each sanitized message once.

- [ ] **Step 4: Verify GREEN**

Run: `npm run test:unit`

Expected: all Vitest files pass.

- [ ] **Step 5: Commit**

Run: `git add vesta/assets/web/message-state.js vesta/assets/web/__tests__/message-state.test.js vesta/assets/web/index.html vesta/assets/web/app.js vesta/assets/web/activity.js && git commit -m "feat: add truthful Vesta message states"`

### Task 6: Connections bridge and settings UX

**Files:**
- Modify: `vesta/gui_web.py:174-470`
- Modify: `vesta/assets/web/app.js:553-584`
- Modify: `vesta/assets/web/styles.css`
- Modify: `vesta/assets/web/__tests__/e2e/mock-bridge.js`
- Modify: `tests/test_gui_web.py`

- [ ] **Step 1: Add failing bridge payload tests**

```python
def test_settings_exposes_normalized_connections(self):
    payload = settings_payload(root)
    self.assertIn("connections", payload)
    self.assertIn("authStatus", payload["connections"][0])
```

- [ ] **Step 2: Verify RED**

Run: `python -m unittest tests.test_gui_web.GuiWebTests.test_settings_exposes_normalized_connections -v`

Expected: `connections` missing.

- [ ] **Step 3: Implement cards and explicit test action**

Render status, source, last checked, safe diagnostic, and Connect/Reconnect/Test/Details actions. Run status checks in a worker and send only normalized JSON to the browser.

- [ ] **Step 4: Verify GREEN**

Run: `python -m unittest tests.test_gui_web tests.test_provider_connections -v && npm run test:unit`

Expected: all tests pass.

- [ ] **Step 5: Commit**

Run: `git add vesta/gui_web.py vesta/assets/web/app.js vesta/assets/web/styles.css vesta/assets/web/__tests__/e2e/mock-bridge.js tests/test_gui_web.py && git commit -m "feat: add provider connections manager"`

### Task 7: Playwright auth/message flows

**Files:**
- Create: `vesta/assets/web/__tests__/e2e/auth-message.spec.js`
- Modify: `vesta/assets/web/__tests__/e2e/mock-bridge.js`

- [ ] **Step 1: Add invalid-auth, missing-auth, success, retry, branding, and activity assertions**

```javascript
test("invalid auth is one failed Vesta card without completed activity", async ({ page }) => {
  await send(page, "test");
  await page.evaluate(() => window.__mock.failAuth());
  await expect(page.locator(".error-card")).toHaveCount(1);
  await expect(page.locator(".error-card")).toContainText("Vesta could not authenticate");
  await expect(page.locator(".timeline")).not.toContainText("Completed");
});
```

- [ ] **Step 2: Verify RED**

Run: `npx playwright test vesta/assets/web/__tests__/e2e/auth-message.spec.js`

Expected: new assertions fail against current UI.

- [ ] **Step 3: Make minimal fixture/UI corrections**

Add deterministic mock outcomes and selectors only where the E2E contract needs them.

- [ ] **Step 4: Verify GREEN**

Run: `npm run test:e2e`

Expected: all Playwright tests pass.

- [ ] **Step 5: Commit**

Run: `git add vesta/assets/web/__tests__/e2e/auth-message.spec.js vesta/assets/web/__tests__/e2e/mock-bridge.js vesta/assets/web/app.js vesta/assets/web/styles.css && git commit -m "test: cover auth and message reliability flows"`

### Task 8: Security and operator documentation

**Files:**
- Create: `AUTH_SECURITY_REVIEW.md`
- Create: `docs/AUTHENTICATION_DEBUGGING.md`
- Create: `docs/PROVIDER_CONNECTIONS.md`
- Create: `docs/VESTA_ERROR_SYSTEM.md`
- Create: `docs/MESSAGE_STATE_MACHINE.md`
- Modify: `docs/TESTING.md`
- Modify: `README.md`

- [ ] **Step 1: Run deterministic evidence commands**

Run: `npm audit --json`, targeted secret-pattern `rg`, `python -m pip check`, and `vesta scan`. Save no secret values; record only file/line evidence and advisory summaries.

- [ ] **Step 2: Write documentation from the implemented contract**

Document credential ownership, status commands, 401/403/429 diagnosis, state transitions, provider labels, test commands, and unresolved dependency advisories. Do not claim direct API-key support.

- [ ] **Step 3: Verify docs**

Run: `rg -n "TBD|TODO|sk-[A-Za-z0-9]{8}" AUTH_SECURITY_REVIEW.md docs README.md`

Expected: no placeholders or embedded secrets in changed documentation.

- [ ] **Step 4: Commit**

Run: `git add AUTH_SECURITY_REVIEW.md docs README.md && git commit -m "docs: add auth security and reliability guides"`

### Task 9: Full quality gate and final review

**Files:**
- Review all changed files.

- [ ] **Step 1: Run targeted and full tests**

Run: `python -m unittest discover -s tests`, `npm run test:unit`, and `npm run test:e2e`.

- [ ] **Step 2: Run static/package checks**

Run: `python -m compileall -q vesta vestahub opcoding`, `python -m pip check`, `npm audit --omit=dev`, and `git diff --check main...HEAD`.

- [ ] **Step 3: Inspect final diff against the design**

Confirm every terminal path, recovery action, branding boundary, connection status, redaction rule, and requested document has evidence. Record gaps honestly in the final report.

- [ ] **Step 4: Commit any verification-only corrections**

Run: `git status --short`; if corrections were required, commit only those reviewed files with `git commit -m "fix: close auth reliability verification gaps"`.
