# Visual observations — Vesta web UI (commit 2d7185c)

Evidence-only notes from the 2026-09-19 re-capture. Every item references a
captured artifact under `screens/`. States are mock-bridge-driven; where a
fixture plausibly caused the artifact, that is stated.

## Branding / terminology

1. **Hero headline is "The Living Flame."** — the empty-state hero
   (`screens/shell/SHELL-LAUNCH-001-fresh-launch.png`). Anyone expecting the
   earlier "The Fire Behind Your Work." line should note the current copy.
2. **Composer shows two adjacent "Auto …" dropdowns** — `● Auto ⌄` (run mode)
   and `Auto model ⌄` (model routing) sit side by side with nearly identical
   labels (`SHELL-LAUNCH-001`, `EXEC-STREAMING-001`). At a glance the two
   controls are hard to tell apart.

## Clipping / overflow

3. **Collapsed AI-Team strip is clipped at the right viewport edge.** On every
   shell/settings screenshot a sliver of the strip ("✦ agents", or "3 agents"
   when a team exists) is cut off mid-text at the window's right edge
   (`SHELL-LAUNCH-001`, `SETTINGS-USAGE-005-usage.png`,
   `AGENTS-WORKSPACE-004-header-agents-button.png`). The strip reads as a
   rendering overflow rather than a deliberate peek affordance.
4. **Provider status footer wraps inconsistently.** The sidebar footer renders
   "● Claude ● Codex ●" on one line and "Copilot connected" wrapped onto the
   next, so Claude/Codex show dot+name only while Copilot gets a "connected"
   suffix on its own line (`SHELL-LAUNCH-001`).

## Duplicated / ambiguous controls

5. **Two Stop controls are visible at once while streaming** — one in the
   response card header and one replacing Send in the composer
   (`EXEC-STREAMING-001-streaming-in-progress.png`). Both are live; nothing
   distinguishes them.
6. **"Streaming response" label appears four times simultaneously** — top
   status strip chip, response-card header, work-log line, and the SESSION
   side panel — all reading "Streaming response" with independent 00:00
   timers (`EXEC-STREAMING-001`). Heavy repetition of one phrase at different
   hierarchy levels.

## Status framing

7. **Command-approval state is framed as "Failed" in the status strip.** While
   the approval card says "COMMAND BLOCKED — Approve this command once?", the
   top strip chip reads "● Failed · Auto"
   (`PERMISSIONS-CMD-APPROVAL-001-command-approval-card.png`). A blocked,
   awaiting-user-decision run presenting as "Failed" is ambiguous framing.
8. **AI Team panel shows "Team updates are temporarily unavailable.
   Reconnecting…" while the roster and feed render correctly**
   (`AGENTS-TEAM-PANEL-002-team-running.png`). Possibly mock-bridge-driven
   (no live objective stream), but the banner contradicts the healthy-looking
   data directly below it.

## Hierarchy / placeholders

9. **Empty "ADVANCED" section header in the Inspector.** The right SESSION
   panel renders an "ADVANCED" divider with no content between it and
   "TASK FOCUS" (`SHELL-LAUNCH-001`, `EXEC-STREAMING-001`).
10. **"Not reported" placeholder repeated up to twice per row** in the Agents
    workspace: "Budget Not reported", "Parallel limit Not reported",
    "Model not reported · Not reported"
    (`AGENTS-WORKSPACE-004-header-agents-button.png`). The doubled
    "Not reported · Not reported" tail on each assignment row reads as
    unfinished.

## Mock-fixture artifacts (not product bugs, noted for baseline readers)

- Onboarding step 1 says "Connect a provider" while the fixture already has
  "3 providers connected" (`EMPTY-ONBOARDING-001-first-run-step1.png`) — the
  mock boots a fully-connected account set with `onboardingSeen=false`.
- Team toggle capture: a single click on `#teamModeBtn` switched straight to
  "Team ON"; no menu was open at capture time
  (`AGENTS-TEAM-TOGGLE-001-team-toggle-menu.png`).
