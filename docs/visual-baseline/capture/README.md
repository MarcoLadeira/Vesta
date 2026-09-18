# Capture tooling

`capture.mjs` — standalone Node script (ESM) that produces the entire visual
baseline. It is deliberately self-contained and does not use the Playwright
test runner.

## What it does

1. Starts a static file server on `127.0.0.1:8123` rooted at the repo/worktree
   root (zero-dependency `node:http`, threaded enough for one browser).
2. Launches Playwright Chromium at 1440×900.
3. For each capture session, creates a fresh browser context and injects, via
   `addInitScript`:
   - `window.__OPAI_TEST_SCENARIO__ = fullScenario(overrides)` — the shared
     e2e fixture (`opai/assets/web/__tests__/e2e/helpers/fixtures.js`);
   - the mock bridge itself
     (`opai/assets/web/__tests__/e2e/mock-bridge.js`), which stubs
     `qt.webChannelTransport` / `QWebChannel` so the real front-end boots
     without Qt.
4. Drives states the same way the specs do (`window.__mock.emitToken`,
   `emitReply`, `emitActivity`, `confirmCancel`, scenario overrides such as
   `commandApproval` / `editApproval`) and takes PNG screenshots.
5. For recordings, creates contexts with `recordVideo` and saves short `.webm`
   clips.
6. Rewrites `../manifest.json` with one entry per artifact plus
   `captureFailures` and a `coverageNote`.

## Run

```bash
node docs/visual-baseline/capture/capture.mjs
```

Requirements: repo `node_modules` with `playwright` installed (resolved by
walking up from this directory), Playwright chromium + ffmpeg in the default
browsers path. Runtime is ~70 s for the full baseline.

Nothing here is imported by the product; the script only reads product files
and writes inside `docs/visual-baseline/`.
