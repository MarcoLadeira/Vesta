# Vesta visual baseline — 2026-09-19

> **Supersedes** branch `audit/vesta-visual-baseline`, which was captured
> against the outdated commit `8192bf0` (pre-rebrand `opai`/`opaihub` paths).
> This baseline is captured against current `main` at `2d7185c` on branch
> `audit/vesta-visual-baseline-2026-09-19`.

## Purpose

A frozen visual record of the Vesta desktop web UI (the real production UI
code that ships inside the QtWebEngine shell) at a known commit: screenshots
of every major surface and state, plus short recordings of the core
workflows. Use it to review visual UX, to diff future UI changes, and as
evidence for design decisions.

## What was captured

- **42 screenshots** (PNG, 1440×900) across `screens/`: shell, composer,
  execution (streaming / complete / turn details / stop-pending / stopped),
  permissions (command + edit approval cards), changes (changeset + diff),
  errors, all 9 settings rail pages, tools-&-insights disclosure, 2 insight
  dashboards, 3 agents dashboards, providers connected + disconnected,
  empty sidebar, 3 onboarding steps, and the Agents / AI Team surface
  (toggle, running team roster, agent detail, header Agents workspace).
- **5 recordings** (.webm, 1440×900): open/ready, send→stream→complete,
  stop/cancel, settings tour, command-approval flow.
- `manifest.json` — machine-readable index (id/file/type/area/state/trigger/
  expected/observed/sourceAreas/commit/capturedAt/bytes) plus a
  `coverageNote` of what was NOT captured and why.
- `observations/visual-observations.md` — evidence-only visual UX notes.

Total media ≈ 11 MB. Capture run: 47 artifacts, 0 failures, ~73 s.

## Capture method

No Qt/PySide6 required. `capture/capture.mjs`:

1. serves the worktree root over a local static server (`127.0.0.1:8124`),
2. boots the real UI (`vesta/assets/web/index.html`) in headless Chromium,
3. injects the e2e suite's own mock bridge
   (`vesta/assets/web/__tests__/e2e/mock-bridge.js`) and the shared
   `fullScenario()` fixture from `helpers/fixtures.js`
   (`window.__VESTA_TEST_SCENARIO__`),
4. drives states exactly the way the specs do (`window.__mock.emitToken`,
   `emitReply`, `emitActivity`, `confirmCancel`, `emitObjective`, rail clicks,
   popover triggers), then screenshots / records.

## How to re-run

```bash
# from the worktree/repo root; playwright must be resolvable from the repo
# (the script resolves it from the enclosing main checkout when run inside
# .worktrees/<name>) and Playwright browsers must be installed
node docs/visual-baseline/capture/capture.mjs
```

The script regenerates `screens/`, `recordings/`, and `manifest.json` in
place. `observations/` and this README are hand-maintained.

## Coverage gaps (see manifest.json `coverageNote` for the canonical list)

- Qt/PySide6 desktop chrome, native menus, OS dialogs — not captured
  (PySide6 unavailable in the capture environment).
- Real provider streaming/latency — everything is mock-bridge-driven.
- OAuth login redirect, Connection Doctor refresh, native file pickers,
  real update downloads, keychain prompts — not exercised visually.
- The composer Team toggle was captured in its Team ON state; no dropdown
  menu appeared on click in this build.
