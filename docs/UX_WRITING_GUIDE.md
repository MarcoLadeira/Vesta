# OPai UX Writing Guide

Source of truth: `opai/brand.py` (identity + shared copy), `opai/activity.py`
(error/stage copy), `opai/gui_controls.py` (Qt fallback copy). New copy goes
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
5. **OPai-first language.** "OPai routed this to Haiku" — providers are named,
   raw ids stay in code surfaces.

## Before / after (shipped)

| Area | Before | After |
| --- | --- | --- |
| Empty state | "What do you want to build?" (every AI tool's line) | **"Build more. Burn less."** + the receipt promise |
| Composer | "Reply to OPai…" | "Tell OPai what to build, fix, or explain…" |
| Empty sub | "Claude and Codex connected · OPai picks the cheapest safe path." | "Tell OPai the goal. It plans, routes to the cheapest capable model, shows every step, and hands you the receipt." |
| CLI run start | (nothing) | `OPai · plan · account:claude:opus` |
| Cost footer | — | `✓ done in 42s · $0.0312 spent` / `$0.04 saved (estimated)` |

## Patterns

- **Error:** `{What broke}. {Next action}.` — e.g. "Claude hit an error and
  couldn't finish. Try again, or pick a different model."
- **Cancel:** neutral tone, never error styling: "Generation stopped by you.
  You can edit the prompt, retry, or switch model."
- **Cost warning:** "This needs a paid model. Pick your Claude, Codex, or
  Copilot account to run it — OPai won't spend on a paid call automatically."
- **Activity:** `{Verb} {object}: {detail}` — "Read file: opai/activity.py",
  "Ran command: pytest".
- **Buttons:** imperative, ≤3 words: Send · Stop · Retry · Edit prompt ·
  Use prompt · Copy details.
