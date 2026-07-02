# OPai Brand System

**One canonical source:** all product copy and identity constants live in
`opai/brand.py`. The web GUI reads it through the boot payload, the CLI reads
it directly, docs quote it. Change the brand there and every surface changes
together — the GUI and terminal can never drift into different products.

## Position

- **Category:** the **cost-aware AI coding cockpit**.
- **Tagline:** *Every step visible. Every dollar accounted.*
- **Positioning:** OPai is the cost-aware AI coding cockpit: one command center
  over Claude, Codex, Copilot, and local models — every step visible, every
  dollar accounted.
- **The enemy:** black-box AI spend — tools that burn tokens invisibly, hide
  what the agent did, and call estimates "savings".
- **For:** builders who use paid AI daily and feel the cost.
  **Not for:** people who want a magic button and don't care what it costs.

## Personality

**Is:** calm · honest · technical · protective · builder-first.
**Is not:** hype, corporate fluff, fake magic, fake precision, provider-worship.

OPai speaks like a senior engineer who respects your time. It tells you what it
did, what it cost, and what to do next — and nothing else.

## Voice rules (enforced by tests)

- **Cost:** estimates are labeled *estimated*; real spend is labeled *spent*;
  **a paid call is never presented as a saving** (`test_cli_stream`,
  `test_savings_honesty`).
- **Errors:** say what happened + what to do next. Never a bare "something went
  wrong" (`opai/activity.py::error_card`, `_FRIENDLY_ERRORS`).
- **Activity:** truthful, present-tense steps ("Read file: app.py", "Waiting
  for Claude") — never fake completion (`test_activity`, Playwright specs).
- **Buttons:** verbs, ≤3 words ("Send", "Stop", "Retry", "Use prompt").

## Signature moments (the brand in behavior)

| Moment | Where | Why it's ours |
| --- | --- | --- |
| **Activity Rail** | timeline in every reply + CLI glyph lines | transparent execution, GUI & terminal alike |
| **Honest Receipt** | footer after every run + `opai receipt` (signed) | proof, not marketing numbers |
| **Cost Firewall** | Safe Auto gates, panic mode, paid-needs-consent | protection before spend |
| **CLI Mirror** | Inspector shows the terminal twin of the current selection, ready to copy | GUI/CLI parity made visible |
| **Empty-state promise** | "Build more. Burn less." + the receipt promise | 10-second identity |

## Visual identity (implemented in `opai/assets/web/styles.css`)

- **Direction:** *Command Center × Honest Ledger* — a dark engineering cockpit
  whose accent color is the money signal.
- **Brand primary:** emerald `#34d399` (`--accent`) = savings / OPai working /
  primary action. Amber = caution/spend attention. Red = stop/danger. Provider
  dots: Claude terracotta, Codex blue, Copilot violet — providers are guests,
  never the brand.
- **Type:** Inter (bundled), `-webkit-font-smoothing: antialiased`; mono
  (Cascadia Code) is reserved for *truth surfaces*: commands, diffs, receipts,
  the CLI mirror.
- **Motifs:** the pulsing emerald dot (OPai working), the vertical activity
  rail, receipt-style footers with tabular numerals, glyph set `✓ ◐ ! ✗ ⊘`
  shared by GUI and CLI.
- **Motion:** short (≤280ms), purposeful, `cubic-bezier(0.4,0,0.2,1)`; pulse
  only while genuinely working.

## Do / don't

- **Do** route new copy through `opai/brand.py` (GUI) or reuse its constants (CLI).
- **Do** keep provider names human ("Claude", not `account:claude:opus`) in
  prose; raw ids belong in code surfaces (CLI mirror, JSON).
- **Don't** invent savings, round costs to look better, or show "completed"
  unless the pipeline returned answered.
- **Don't** add always-on panels; power stays in the palette, inspector, and CLI.
