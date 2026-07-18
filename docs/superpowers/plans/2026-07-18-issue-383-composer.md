# Composer Task Setup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every send explain its effective mode, authority, model, cost posture, context hints, and state.

**Architecture:** A small `renderComposerContext()` renderer derives display-only data from canonical boot state. Existing mode/model selectors and send/stop functions remain the only state-changing paths.

**Tech Stack:** Vanilla JavaScript, CSS, existing Qt bridge, Playwright.

## Global Constraints

- No automatic provider connection, retry, or spend.
- Context references contain paths only; backend context caps remain authoritative.
- Cost copy must be conservative and never invent a price.

### Task 1: Accessible task setup surface

- [x] Write failing E2E tests for mode/model/cost summary and disabled reasons.
- [x] Add context row, reason region, and task-context controls to the composer.
- [x] Render canonical autonomy and cost posture after boot and selection changes.

### Task 2: Draft, context, and send-state behavior

- [x] Write failing E2E tests for file references, drag-in names, drafts, and Enter/Shift+Enter.
- [x] Add removable path-only context hints and send them as `contextHints`.
- [x] Preserve the existing Send/Stop state machine and make disabled remedies explicit.

### Task 3: Audit and release

- [x] Update UX writing guidance and issue QA map.
- [x] Run focused and full Playwright suites, lint, syntax, and diff checks.
- [ ] Create and merge the PR closing #383.
