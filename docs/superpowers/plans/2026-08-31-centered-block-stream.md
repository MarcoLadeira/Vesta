# Centered Block Stream Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Center Vesta conversations and render complete provider updates as separate, bounded ChatGPT-style blocks instead of one concatenated wall of text.

**Architecture:** Preserve the existing text callback for every provider, but let block-aware listeners receive an optional `start_block` flag from structured account streams. The browser keeps the full chronological Markdown string for completion/copy while incrementally rendering only the active block; the latest three blocks stay visible and older blocks move into a native disclosure.

**Tech Stack:** Python 3, PySide6 signals, browser JavaScript, CSS design tokens, pytest/unittest, Playwright.

---

### Task 1: Preserve provider message boundaries

**Files:**
- Modify: `vestahub/accounts.py:284, 2767-2786, 2810-3006`
- Modify: `vesta/gui_web.py:2538-2539, 2650-2651`
- Test: `tests/test_ai_model_bugfixes.py`

- [ ] **Step 1: Write the failing account-stream test**

Add a Codex stream containing two completed `agent_message` items. Use a callback with `accepts_block_start = True` and assert:

```python
received = []

def on_text(text: str, start_block: bool = False) -> None:
    received.append((text, start_block))

on_text.accepts_block_start = True
self.assertEqual(received, [("First update.", False), ("Final update.", True)])
self.assertEqual(result["text"], "First update.\n\nFinal update.")
```

- [ ] **Step 2: Run the test and verify the current concatenation fails**

Run: `python -m pytest -q tests/test_ai_model_bugfixes.py -k "message_boundaries"`

Expected: FAIL because the callback receives plain strings and the result is `First update.Final update.`.

- [ ] **Step 3: Implement the compatible boundary callback**

Add `_notify_text(listener, payload, start_block=False)` beside `_notify`. If the listener advertises `accepts_block_start`, call `listener(payload, start_block)`; otherwise retain the existing one-argument callback. In structured Claude/Codex streams, mark every text item after the first as a new block and join structured text parts with `"\n\n"`; keep unstructured/token streams joined with `""`.

Change each Qt bridge callback to:

```python
def emit_text(chunk: str, start_block: bool = False) -> None:
    payload = {"requestId": request_id, "text": chunk}
    if start_block:
        payload["blockStart"] = True
    self.token.emit(json.dumps(payload))

emit_text.accepts_block_start = True
```

- [ ] **Step 4: Run the focused Python tests**

Run: `python -m pytest -q tests/test_ai_model_bugfixes.py tests/test_activity.py tests/test_gui_web.py`

Expected: all selected tests pass; legacy one-argument callbacks still receive strings.

- [ ] **Step 5: Commit the provider contract**

```bash
git add vestahub/accounts.py vesta/gui_web.py tests/test_ai_model_bugfixes.py
git commit -m "Preserve agent message stream boundaries"
git push
```

### Task 2: Render recent blocks incrementally

**Files:**
- Modify: `vesta/assets/web/app.js:56-66, 123-129, 2038-2086, 2391-2435`
- Modify: `vesta/assets/web/__tests__/e2e/mock-bridge.js:570`
- Modify: `vesta/assets/web/__tests__/e2e/helpers/app.js:73-78`
- Test: `vesta/assets/web/__tests__/e2e/streaming-markdown.spec.js`

- [ ] **Step 1: Write failing browser tests**

Extend `emitToken` to accept `{ blockStart }`. Add tests that emit five complete updates and assert:

```javascript
await emitToken(page, id, "First update.");
await emitToken(page, id, "Second update.", { blockStart: true });
await emitToken(page, id, "Third update.", { blockStart: true });
await emitToken(page, id, "Fourth update.", { blockStart: true });
await emitToken(page, id, "Fifth update.", { blockStart: true });
await expect(page.locator(".stream-recent > .stream-block")).toHaveCount(3);
await expect(page.locator(".stream-earlier summary")).toContainText("Earlier progress (2)");
await expect(page.locator(".stream-block").last()).toContainText("Fifth update.");
```

Also emit two ordinary token chunks without `blockStart` and assert they remain one block.

- [ ] **Step 2: Run the new browser tests and verify they fail**

Run: `npx playwright test vesta/assets/web/__tests__/e2e/streaming-markdown.spec.js --grep "progress blocks|token chunks"`

Expected: FAIL because the live response has one `.body.stream` and ignores `blockStart`.

- [ ] **Step 3: Implement the bounded block renderer**

Initialize `state.streamBlocks = [""]`. Change the pending markup to contain `.stream-earlier`, `.stream-earlier-body`, and `.stream-recent` with one active `.stream-block.body.stream.response-prose`.

When `blockStart` arrives:

1. Flush and remove the streaming caret from the previous active block.
2. Append `"\n\n"` to `state.streamedText`.
3. Push a new block string and DOM node.
4. Keep only three nodes in `.stream-recent`; move older nodes into `.stream-earlier-body` and update its count.

On ordinary token deltas, append to the current block. Update `flushTokenRender()` to render only `state.streamBlocks.at(-1)` into the active node while retaining the existing 32ms animation-frame batching, selection preservation, and full `state.streamedText` fallback.

- [ ] **Step 4: Run streaming, scrolling, and cancellation tests**

Run: `npx playwright test vesta/assets/web/__tests__/e2e/streaming-markdown.spec.js vesta/assets/web/__tests__/e2e/scroll-follow.spec.js vesta/assets/web/__tests__/e2e/activity.spec.js`

Expected: all tests pass. The 200-token burst remains at four or fewer renders and cancellation keeps received text.

- [ ] **Step 5: Commit the incremental renderer**

```bash
git add vesta/assets/web/app.js vesta/assets/web/__tests__/e2e/mock-bridge.js vesta/assets/web/__tests__/e2e/helpers/app.js vesta/assets/web/__tests__/e2e/streaming-markdown.spec.js
git commit -m "Render live agent updates as bounded blocks"
git push
```

### Task 3: Center the conversation lane

**Files:**
- Modify: `vesta/assets/web/styles.css:390-560, 905-990, 1835-1915`
- Test: `vesta/assets/web/__tests__/e2e/responsiveness.spec.js`
- Test: `vesta/assets/web/__tests__/e2e/agent-workspace-visual.spec.js`

- [ ] **Step 1: Write failing layout assertions**

At desktop and phone widths, assert the assistant header, live generator, prose paragraphs, user bubble container, and composer share the same horizontal center within two pixels. Assert `.response-table-scroll`, `.changeset-card`, and code artifacts can still exceed the prose width without overflowing the chat viewport.

- [ ] **Step 2: Run the layout tests and verify the left anchoring fails**

Run: `npx playwright test vesta/assets/web/__tests__/e2e/responsiveness.spec.js --grep "centered conversation lane"`

Expected: FAIL because assistant prose has a maximum width but no automatic inline margins.

- [ ] **Step 3: Implement centered, branded styles**

Use existing tokens only:

```css
.msg.user,
.msg.bot > .assistant-header,
.msg.bot > .gen {
  max-width: var(--measure-prose);
  margin-inline: auto;
}

.assistant-header,
.response-prose > :not(pre, .response-table-scroll) {
  margin-inline: auto;
}
```

Style `.stream-block` as unboxed prose with generous block spacing, `.stream-earlier` as a quiet borderless disclosure, and use `var(--accent)` only for the live status dot/caret. Preserve existing responsive padding and wide structured-artifact rules.

- [ ] **Step 4: Run responsive and visual verification**

Run: `npx playwright test vesta/assets/web/__tests__/e2e/responsiveness.spec.js vesta/assets/web/__tests__/e2e/agent-workspace-visual.spec.js`

Expected: all scenarios pass at the existing desktop/tablet/phone breakpoints. Update snapshots only if the intentional centering changes them, then re-run without update mode.

- [ ] **Step 5: Commit the centered layout**

```bash
git add vesta/assets/web/styles.css vesta/assets/web/__tests__/e2e/responsiveness.spec.js vesta/assets/web/__tests__/e2e/agent-workspace-visual.spec.js vesta/assets/web/__tests__/e2e/agent-workspace-visual.spec.js-snapshots
git commit -m "Center and simplify the conversation lane"
git push
```

### Task 4: Final verification and PR readiness

**Files:**
- Review: all files changed on the branch

- [ ] **Step 1: Run relevant Python verification**

Run: `python -m pytest -q tests/test_ai_model_bugfixes.py tests/test_activity.py tests/test_gui_web.py tests/test_session_resume.py`

Expected: all selected tests pass.

- [ ] **Step 2: Run relevant web verification**

Run: `npm run test:unit && npm run test:tokens && npx playwright test vesta/assets/web/__tests__/e2e/streaming-markdown.spec.js vesta/assets/web/__tests__/e2e/scroll-follow.spec.js vesta/assets/web/__tests__/e2e/responsiveness.spec.js vesta/assets/web/__tests__/e2e/agent-workspace-visual.spec.js`

Expected: zero failures and stable visual snapshots.

- [ ] **Step 3: Inspect the real desktop app**

Launch the worktree build without invoking the Vesta CLI. Confirm short agent blocks arrive separately, the fourth block folds the oldest into “Earlier progress,” prose is centered, wide artifacts remain usable, and the composer stays aligned at desktop and narrow widths.

- [ ] **Step 4: Review and push final state**

Run: `git diff --check origin/main...HEAD && git status --short --branch && gh pr view 789 --json state,mergeable,url`

Expected: no whitespace errors, clean tracked worktree, branch pushed, and PR #789 open and mergeable.
