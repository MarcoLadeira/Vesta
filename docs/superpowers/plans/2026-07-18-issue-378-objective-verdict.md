# Objective-Based Completion Verdicts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every OPai terminal outcome an evidence-backed verdict, shared by the GUI, CLI, workflow checkpoint, activity ledger, and per-turn receipt.

**Architecture:** `opaihub.completion` becomes the single producer of an immutable objective, normalized evidence references, and a typed verdict. The GUI pipeline attaches that result once at the terminal boundary; renderers consume it without re-inferring success. Compatibility `status`/`completion_state` fields remain for alpha callers, but cannot upgrade a non-completed verdict.

**Tech Stack:** Python 3.10+, pytest, OPai GUI pipeline, terminal CLI, browser JavaScript/Playwright.

---

### Task 1: Define the objective and evidence contract

**Files:**
- Modify: `opaihub/completion.py`
- Test: `tests/test_completion_contract.py`

- [x] Add immutable `ObjectiveRecord`, `EvidenceRef`, and `CompletionVerdict` types.
- [x] Derive acceptance from task mode: read-only answers require a non-empty response; implementation requires changed-file/diff evidence; test/ship requests require successful test evidence as well.
- [x] Evaluate completed, partial, blocked, failed, cancelled, and timeout outcomes with a stable reason code and human sentence.
- [x] Run `python -m pytest tests/test_completion_contract.py -q` after each red/green cycle.

### Task 2: Attach one terminal verdict to the shared pipeline

**Files:**
- Modify: `opaihub/gui_pipeline.py`
- Modify: `opaihub/workflow_state.py`
- Modify: `opaihub/checkpoints.py`
- Test: `tests/test_completion_contract.py`

- [x] Capture the prompt/mode objective before provider execution and evaluate it only at the terminal decoration boundary.
- [x] Persist the serialized verdict in workflow state and checkpoint state, and append one `completion_verdict` workflow/ledger record with bounded evidence.
- [x] Thread the verdict into the per-turn savings receipt, without storing raw prompts.
- [x] Add hermetic fake-runner coverage for clean edit + tests, edits without tests, capability mismatch, cancel, timeout, and crash.

### Task 3: Enforce GUI/CLI/receipt parity

**Files:**
- Modify: `opai/cli_stream.py`
- Modify: `opai/assets/web/app.js`
- Modify: `opai/assets/web/styles.css`
- Test: `tests/test_cli_stream.py`
- Test: `opai/assets/web/__tests__/e2e/activity-truth.spec.js`

- [x] Render verdict plus typed reason before any answer/receipt success affordance.
- [x] Render non-completed verdicts without a success glyph/style and include the safest next action.
- [x] Make CLI exit success only for `completed`, preserving cancellation exit 130.
- [x] Assert a shared fixture matrix produces the same verdict wording in GUI data and CLI output.

### Task 4: Verify release-gate behaviour

**Files:**
- Modify: `docs/QA_E2E_ISSUE378_2026-07-18.md`
- Test: `tests/test_completion_contract.py`
- Test: `tests/test_cli_stream.py`
- Test: `tests/test_edit_approval.py`
- Test: `opai/assets/web/__tests__/e2e/activity-truth.spec.js`

- [x] Add the #378 regression matrix to the QA record, including evidence expectations and no-success-style property coverage.
- [x] Run focused Python/browser suites, lint/format, and full browser tests before opening the PR. The JavaScript unit runner is not installed in this worktree, so it cannot run offline.
