# OPai Desktop UX — Control Layer

_Stack: PySide6 desktop GUI (`opai/gui_desktop.py`). Not a web app — there is no
React/Tailwind/Playwright layer._

## Design principle (deliberate)

`opai gui` stays a **calm, Claude-desktop-style chat**: sidebar + conversation +
composer. We add **control and clarity without a busy IDE pane**. Power lives in
a command palette and keyboard shortcuts, not in always-on side panels.

## What this layer adds

| Area | Before | After |
| --- | --- | --- |
| Command palette | none | **Ctrl+K** — fuzzy-filtered commands (new chat, change model/mode, connect, settings, savings, doctor, shortcuts) |
| Keyboard shortcuts | Ctrl+Enter only | Ctrl+K / Ctrl+N / Ctrl+L / Ctrl+M / Ctrl+/ / Esc, with a **Ctrl+/ help dialog** |
| Model clarity | label only | **capability badge tooltip** per model (speed · quality · cost; Auto = "routes cheapest", local = "free · private") |
| Session visibility | "$X saved · $Y spent" | **model · mode · spent today · saved** status strip — always-on awareness of which AI is active and what it costs |
| Empty state | hero + chips | + a "Press Ctrl+K for commands" discovery hint |
| Thinking state | "Working…" | "**{model} is working…**" |
| Error/empty/thinking copy | inline strings | centralized, calm, actionable text in `gui_controls.py` |

## Engineering shape

The data + formatting live in **`opai/gui_controls.py`** — Qt-free and
dependency-light, so the palette commands, model badges, header strip, shortcut
list, and state messages are **unit-tested headlessly** (`tests/test_gui_controls.py`,
24 pure tests). The PySide widgets consume those helpers. A guarded headless
**render smoke** (skipped where PySide6 isn't installed, e.g. CI `--no-deps`)
constructs the real window offscreen so the wiring can't crash the renderer.

Why this split: the GUI file is a single large PySide module; keeping logic in a
pure helper module means CI (which doesn't install the desktop extra) still
covers the behavior, and future changes are testable without a display.

## Model capability metadata

`[ASSUMPTION]` The engine does not expose per-model speed/quality/cost, so
`_MODEL_TIERS` in `gui_controls.py` is a curated map by model family
(opus/sonnet/haiku/codex). Tune it as providers change; unknown models fall back
to a safe "cloud · paid" badge.

## Deliberately NOT built (and why)

The maximalist "AI command center" (always-on right-side control panel, session
inspector, workflow queue, multi-model comparison) was **declined on purpose** —
it conflicts with the product's calm-chat identity. Those remain candidates for
the roadmap if user demand appears, but they are not the direction today.

## Future UX space (tracked as issues, not stubs)

- Capture-rate / live cost in the header — #93
- Prompt library / saved prompts — _backlog_
- Multi-model compare — _backlog_ (only if it doesn't crowd the chat)
