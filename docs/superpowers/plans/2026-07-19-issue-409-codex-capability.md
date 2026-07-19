# Issue #409 Codex Account Capability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent a verified ChatGPT-backed Codex account from selecting static model IDs the Codex CLI rejects, while retaining the curated picker for API-key accounts.

**Architecture:** Parse only the already-safe `codex login status` result into a compact account type (`chatgpt`, `api_key`, or `unknown`) and retain it in local connection history. `available_models` uses that type to pass capability context to `account_models`; a ChatGPT account receives one default-model option (`account:codex`) so the Codex CLI selects a supported model itself.

**Tech Stack:** Python 3.10+, `unittest`, pytest-cov; no provider completion, credential-file read, or network request in tests.

## Global Constraints

- Treat unknown Codex account types conservatively: never claim API-key support without the safe status signal.
- Never persist raw CLI status output or credentials; persist the normalized account type only.
- Keep existing picker entries unchanged for a verified API-key account.
- Enforce at least 85% line coverage for the changed account and app-state modules using fixture-only tests.

---

### Task 1: Capture a safe Codex account type

**Files:**
- Modify: `opaihub/accounts.py:55-105, 330-560, 997-1110`
- Test: `tests/test_provider_connections.py`

**Interfaces:**
- Produces: `connection_for_account(..., account_type="chatgpt")` and connection history containing `accountType`.
- Consumes: redacted `codex login status` stdout/stderr.

- [x] **Step 1: Write failing connection tests**

```python
connection = check_account_connection("codex", run=lambda _: _Completed(0, "Logged in using ChatGPT"))
assert connection["accountType"] == "chatgpt"
```

- [x] **Step 2: Run the test and verify it fails because the type is absent**

Run: `python -m pytest tests/test_provider_connections.py -k codex_account_type -q`

- [x] **Step 3: Normalize only `ChatGPT` and `API key` status text into a safe enum**

```python
def _account_type_from_status(account_id: str, detail: str) -> str:
    if account_id != "codex": return "unknown"
    if "chatgpt" in detail.lower(): return "chatgpt"
    if "api key" in detail.lower(): return "api_key"
    return "unknown"
```

- [x] **Step 4: Re-run connection tests**

Run: `python -m pytest tests/test_provider_connections.py -k codex -q`

### Task 2: Filter the picker by verified account type

**Files:**
- Modify: `opaihub/accounts.py:997-1110`
- Modify: `opai/app_state.py:529-650`
- Test: `tests/test_provider_connections.py`

**Interfaces:**
- Consumes: `account_types={"codex": "chatgpt" | "api_key"}`.
- Produces: one `account:codex` default option for ChatGPT; full Codex model list for API-key accounts.

- [x] **Step 1: Write failing picker fixtures for both account types**

```python
assert [option["id"] for option in chatgpt] == ["account:codex"]
assert "account:codex:gpt-5.6" in [option["id"] for option in api_key]
```

- [x] **Step 2: Run the focused tests and verify they fail**

Run: `python -m pytest tests/test_provider_connections.py -k codex_picker -q`

- [x] **Step 3: Add the explicit default option and pass cached account type through `available_models`**

```python
account_models(accounts=detected_accounts, account_types=account_types)
```

- [x] **Step 4: Re-run focused picker tests and coverage**

Run: `python -m pytest tests/test_provider_connections.py --cov=opai.app_state --cov=opaihub.accounts --cov-fail-under=85 -q`

### Task 3: Regression verification and review

**Files:**
- Verify: `tests/test_provider_connections.py`
- Verify: `tests/test_gui2_control_plane.py`
- Verify: `tests/test_reliable_ai_controls.py`

- [x] **Step 1: Run changed-module coverage at or above 85% (100% changed executable lines)**

Run: `python -m pytest tests/test_provider_connections.py tests/test_gui2_control_plane.py tests/test_reliable_ai_controls.py --cov=opai.app_state --cov=opaihub.accounts --cov-fail-under=85 -q`

- [x] **Step 2: Run broader account and GUI contracts without coverage filtering**

Run: `python -m pytest tests/test_provider_connections.py tests/test_connection_doctor.py tests/test_gui2_control_plane.py -q`

- [x] **Step 3: Address independent review findings before release**

- The CLI list and `set-default` command reuse `available_models`, so they cannot
  reintroduce a Codex model that the capability-filtered picker removed.
- Cached Codex account types expire after the existing five-minute connection
  freshness window; stale or unrecognized types use the compatible CLI default.

Run: `python -m pytest tests/test_provider_connections.py tests/test_connection_doctor.py tests/test_gui2_control_plane.py -k "codex or capability" -q`

- [ ] **Step 4: Review, commit, create, and merge the PR closing #409**

Run: `gh pr create --repo MarcoLadeira/OPai --base main --title "fix(codex): filter unsupported subscription models" --body "Closes #409"`
