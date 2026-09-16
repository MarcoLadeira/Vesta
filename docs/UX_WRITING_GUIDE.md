# Vesta UX Writing Guide

Source of truth: `vesta/brand.py` (identity + shared copy), `vesta/activity.py`
(error/stage copy), `vesta/gui_controls.py` (Qt fallback copy). New copy goes
through those modules, not inline strings.

## Principles

1. **Say what happened, then what to do.** Every error names the cause and the
   next action.
2. **Label the money.** *spent* = real provider-reported cost; *estimated* =
   model math; a paid call is never a "saving".
3. **Present tense while working, past tense when done.** "Reading file…" →
   "Read file". Never "completed" unless it completed.
4. **Short, technical, human.** No exclamation marks in system copy. No hype
   adjectives ("magical", "blazing", "supercharged").
5. **Vesta-first language.** "Vesta routed this to Haiku" — providers are named,
   raw ids stay in code surfaces.

## Before / after (shipped)

| Area | Before | After |
| --- | --- | --- |
| Empty state | "What do you want to build?" (every AI tool's line) | **"Build more. Burn less."** + the receipt promise |
| Composer | "Reply to Vesta…" | "Tell Vesta what to build, fix, or explain…" |
| Empty sub | "Claude and Codex connected · Vesta picks the cheapest safe path." | "Tell Vesta the goal. It plans, routes to the cheapest capable model, shows every step, and hands you the receipt." |
| CLI run start | (nothing) | `Vesta · plan · account:claude:opus` |
| Cost footer | — | `✓ done in 42s · $0.0312 spent` / `$0.04 saved (estimated)` |

## Calm Stream phase copy (`{rid}:phase` row)

One row, detail advancing in place — present tense while working, past tense
when done (Principle 3). Canonical strings:

| Phase | Detail while running | On completion |
| --- | --- | --- |
| prepare | "Preparing request…" | — (superseded by next step) |
| context | "Reading project context…" | "Read project context" |
| model | "Model: {name}" | — (also mirrored to the status strip) |
| connect | "Checking connection…" | — (status strip shows "Connected · {provider}") |
| send | "Sending request…" | row flips `success`, title "Request sent" |
| (failure) | — | row flips `error`: "{Step} failed. {Next action}." |
| (cancel) | — | row flips `cancelled`: "Stopped by you." |

Status strip copy: "Connected · {provider} · {model}" — providers named,
raw ids stay in code surfaces (Principle 5). Grouped tool rows: `{Verb} {N}
{objects}` — "Read 4 files", "Ran 3 commands"; expanding shows the raw
`{Verb} {object}: {detail}` lines.

## Patterns

- **View state:** `{title}` + `{plain-language reason}` + `{safe next action}`.
  Error cards use an assertive alert; loading, empty, and degraded cards use a
  polite status. The action must be user initiated: never retry a model call
  or spend-triggering operation automatically.
- **Loading:** name the work actually under way — "Loading settings" / "Checking
  local preferences and connections." Never show a simulated percentage or a
  fake progress bar. Workspace generation uses the canonical prepare, context,
  model, connect, and send phases above.
- **Empty:** say what is absent and expose the shortest productive path —
  "No saved chats yet" / "Start a chat and it will appear here." / "New chat".
- **Degraded:** say what is temporarily unavailable, preserve the safe path,
  and do not imply a retry is running — "Dashboard is temporarily unavailable"
  / "You appear to be offline, so fresh savings data is unavailable." /
  "Open chat".
- **Error:** `{What broke}. {Next action}.` — e.g. "Claude hit an error and
  couldn't finish. Try again, or pick a different model."
- **Cancel:** neutral tone, never error styling: "Generation stopped by you.
  You can edit the prompt, retry, or switch model."
- **Cost warning:** "This needs a paid model. Pick your Claude, Codex, or
  Copilot account to run it — Vesta won't spend on a paid call automatically."
- **Composer setup:** show the effective mode, its one-line consequence, the
  selected model, and one conservative cost posture before every send. Use
  "No provider spend" only for local or free routes; use "May spend within
  your limits" for an account route. Never imply a price Vesta has not read
  from the local ledger.
- **Disabled send:** state the cause and name one local remedy: "Write a prompt
  before sending." or "Connect Claude before sending." The Settings action is
  an explicit user choice; Vesta never starts connection or spend workflows on
  its own.
- **Repository context:** call these "references", not uploads or attachments.
  The composer may send a repository-relative path, never claim the file bytes
  were included, and reminds people that existing context caps still apply.
- **Keyboard:** keep the compact hint literal: "Enter to send · Shift+Enter for
  a new line". The same behavior must work in the composer regardless of
  whether it was reached through the sidebar or command palette.
- **Activity:** `{Verb} {object}: {detail}` — "Read file: vesta/activity.py",
  "Ran command: pytest".
- **Buttons:** imperative, ≤3 words: Send · Stop · Retry · Edit prompt ·
  Use prompt · Copy details.
