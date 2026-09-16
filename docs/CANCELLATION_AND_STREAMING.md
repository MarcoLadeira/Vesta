# Cancellation & Streaming

How the Stop button really works, how streaming flows, and how stale responses
are prevented — in a PySide6 + QWebEngine app driving user-owned AI CLIs.

## The problem it fixes

The model call is a CLI subprocess (`claude` / `codex` / `copilot`). It used to
run via `subprocess.run(..., timeout=1200)` — a **20-minute blocking call with
no interrupt**. The web Stop button was wired to the same handler as Send and
early-returned while busy, so it did nothing; `Esc` only flipped a local flag
while the worker kept running and its late reply still rendered. Result: Opus
"felt frozen," Stop didn't work, and a stale reply could overwrite the UI.

## Real cancellation

`AccountRunner.stream()` (`vestahub/accounts.py`) runs the CLI through
`subprocess.Popen` (not `run`) and reads stdout through a queue so it can check
a **`threading.Event cancel`** ~5×/second. Setting the event triggers
`_terminate()` → `proc.terminate()`, then `proc.kill()` if it won't exit. The
process is genuinely stopped, and any partial text is returned.

The flag is threaded end to end:

```
JS Stop → bridge.cancel(requestId)  (vesta/gui_web.py)
        → sets the request's threading.Event
        → handle_gui_message(..., cancel)  (vestahub/gui_pipeline.py)
        → app_state.ask(..., cancel) → _ask_account(..., cancel)
        → AccountRunner.stream(..., cancel)  → kills the subprocess
```

Behavior by stage:

- **Before the model call** — the pipeline checks `cancel` before spending and
  returns a clean `cancelled` result; the runner is never invoked.
- **During streaming** — the runner sees the flag between reads, kills the
  process, and returns `{cancelled: True, text: <partial>}`.
- **After completion** — `cancel()` is a no-op (the request already finished).

### Request IDs / stale-response protection

Every send gets a unique `requestId` (JS `crypto.randomUUID`). The bridge tags
every `activity`, `token`, and `replyReady` payload with it. The front-end holds
`state.currentRequest`; **`VestaActivity.shouldApply(current, incoming)`** (mirror
of `vesta.activity.should_apply`) gates every incoming signal. On Stop (or a new
send) `currentRequest` is set to `null`/the new id, so any late output from the
old request is dropped — **no stale response can overwrite the current
message.** This is the primitive that makes double-click stop, retry-after-stop,
and provider-returns-after-stop all safe.

## Streaming lifecycle

- **claude**: `-p --output-format stream-json --verbose` emits JSONL.
  `vesta.activity.parse_claude_line` turns `system`→provider event,
  `assistant`→text deltas + `tool_use`→typed events (Read→`file_read`,
  Edit/Write→`file_edit`, Bash→`command_run`, Grep/Glob→`context_read`), and
  `result`→cost + done. Text streams to the bubble live.
- **codex**: `exec` with an out-file for the final message; stdout is progress.
  Cancellable; final text delivered at completion (structured token streaming =
  backlog).
- **copilot**: `-s` reply streamed as text; cancellable.

Malformed / non-JSON lines degrade to a text delta rather than crashing, so a
CLI format drift never breaks the chat.

## Backend limitations (documented honestly)

- **Child processes**: `terminate()` stops the CLI we launched; a CLI that
  spawns its own children may leave short-lived grandchildren. Acceptable for a
  read-mostly assistant; a process-group kill is a possible future hardening.
- **Local/auto path** (`run_ask`, HTTP to Ollama/LM Studio) is **not** killed
  mid-flight yet — it's stopped *locally* (result ignored via the stale guard).
  True local cancellation is a backlog issue.
- **Codex/Copilot** stream text but not structured token/tool events yet.

## Race conditions handled

Stop before first token · stop during streaming · stop after completion · double
stop (one cancellation, then no-op) · duplicate submit (Enter during generation
ignored) · retry after stop (fresh id; old reply ignored) · provider returns
after stop (ignored by the stale guard).

## How to test it

- **Python** (`tests/test_cancellation.py`): `Popen` is mocked with a scripted
  fake process; asserts `terminate()` is called on cancel/timeout, partial text
  is preserved, and the pipeline returns `cancelled`.
- **Vitest** (`vesta/assets/web/__tests__/activity.test.js`): `shouldApply`
  stale-guard, thresholds, elapsed formatting, activity store.
- **Playwright** (`vesta/assets/web/__tests__/e2e/activity.spec.js`): a mock
  bridge drives streaming/cancellation in Chromium — stop-before-token,
  stop-during-stream (no extra tokens), double-stop, duplicate-submit,
  retry-after-stop, error recovery, slow-model, a11y.

Run: `python -m unittest discover -s tests` · `npm run test:unit` ·
`npm run test:e2e` (needs `npx playwright install chromium` once).
