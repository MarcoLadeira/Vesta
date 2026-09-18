# OPai Web GUI — Visual Baseline

**Branch:** `audit/vesta-visual-baseline` · **Base commit:** `8192bf0fff41f7b4776a5957892b86b7500325b5` (`feat/sidebar-recents-only`)
**Captured:** 2026-09-19, from the isolated git worktree `.worktrees/audit-visual-baseline` (no product code modified).

## Purpose

A frozen, machine-readable visual record of the OPai web GUI for the
production-readiness audit: what each key screen and state actually looks like
at this commit, so visual/UX regressions and audit claims can be checked
against evidence instead of memory.

## Capture method

- The **web UI** (`opai/assets/web/`) is the real production UI — it is what
  QtWebEngine renders inside the desktop shell. The PySide6 desktop wrapper is
  **not** captured here (PySide6 is not installed in the capture environment).
- The capture script (`capture/capture.mjs`) starts a local static server on
  `127.0.0.1:8123` serving the worktree root, opens
  `opai/assets/web/index.html` in Playwright Chromium at **1440×900**, and
  injects the **same mock bridge the e2e suite uses**
  (`opai/assets/web/__tests__/e2e/mock-bridge.js` + `helpers/fixtures.js`
  `fullScenario`) via `addInitScript`. No Qt, no real providers, no network.
- All states are driven exactly the way the e2e specs drive them:
  `window.__mock.emitToken / emitReply / emitActivity / confirmCancel`,
  scenario overrides (`commandApproval`, `editApproval`, boot/settings
  overrides), and real clicks/typing for UI navigation.
- Screenshots are full-viewport PNG. Recordings are Playwright `recordVideo`
  `.webm` clips of short focused actions (5–30 s).

## How to re-run

```bash
# from the worktree (or repo) root; needs repo node_modules (playwright)
# and Playwright chromium + ffmpeg installed
node docs/visual-baseline/capture/capture.mjs
```

The script rewrites `screens/`, `recordings/`, and `manifest.json` in place.

## Layout

- `manifest.json` — one structured entry per artifact (id, file, area, state,
  trigger, expected, observed, sourceAreas, commit, capturedAt) plus
  `coverageNote` and `captureFailures`.
- `screens/<area>/` — 33 screenshots across shell, composer, execution,
  permissions, providers, settings, changes, errors, empty-states.
- `recordings/` — 5 short `.webm` workflow clips.
- `workflows/` — prose walkthroughs of the recorded flows.
- `observations/visual-observations.md` — evidence-only notes from the captures.

## Coverage

Captured (mock-driven, faithful to the front-end's real rendering):

- **Shell**: fresh launch / branded empty state, sidebar with recent chats,
  model selector popover, run-mode selector popover.
- **Composer**: typed prompt, command-approval card, edit-approval card,
  approved-after-approve state.
- **Execution**: streaming in progress (tokens + activity), completed run,
  expanded turn diagnostics, stop-requested (unconfirmed), stopped card.
- **Settings**: all 10 rail pages (Overview, Providers & Connections,
  Models & Routing, Cost Firewall, Model Usage, Permissions & Safety,
  Privacy & Data, Appearance, Tools & Insights, About) + 4 insight dashboards
  (Money Saved, Cost Firewall, Agents readiness, Guarded Workflows).
- **Providers**: connected and all-disconnected states.
- **Changes**: proposed changeset card, expanded per-file diff hunks.
- **Errors**: provider error card, account-not-connected error card.
- **Empty states**: empty sidebar, first-run onboarding tour.

**Not captured (and why):**

- Qt/PySide6 desktop wrapper (window chrome, native menus, OS dialogs) — the
  desktop shell is not runnable in this environment; only the web UI it hosts.
- Real provider streaming, latency, auth/OAuth redirects — the mock bridge
  replaces the entire backend.
- OS-native file/folder pickers, keychain prompts, real update downloads —
  mocked at the bridge boundary; their native chrome is not part of the web UI.
- Connection Doctor live refresh and provider login progress were not
  exercised visually (mockable but out of the 30-minute budget).

## Integrity

- All artifacts were produced from commit `8192bf0` in a disposable worktree.
- The capture harness never writes outside `docs/visual-baseline/`.
- No state was faked: every capture corresponds to a UI state the mock bridge
  genuinely drives through the real front-end code.
