# Real Agent Operation Activity Implementation Plan

**Goal:** Emit truthful, secret-safe run-tests / open-PR / CI-watch / merge-PR events for issue #109.

### Task 1: Shared operation-event contract

- [x] Add a best-effort emitter that reuses `make_event` and preserves stable event IDs.
- [x] Emit validation lifecycle events from real ACI test execution.
- [x] Prove callback failures and command output cannot corrupt or leak into events.

### Task 2: GitHub lifecycle events

- [x] Emit PR creation and merge lifecycle events around actual `gh` calls.
- [x] Add a cancellable, bounded CI watcher with injected time/sleep for deterministic tests.
- [x] Handle GitHub CLI's valid failed/pending check return codes without masking transport errors.

### Task 3: End-to-end timeline proof

- [x] Emit representative backend event lifecycles into the browser bridge mock.
- [x] Verify stable-ID updates produce distinct test, PR, CI, and merge rows without duplicates.

### Task 4: Release gate

- [x] Run focused and full Python/JavaScript/Playwright suites, lint, security/dependency scans, registry validation, and isolated install smoke.
- [ ] Review, commit, publish a PR closing #166 and #109, wait for required CI, and merge only when every gate passes.
