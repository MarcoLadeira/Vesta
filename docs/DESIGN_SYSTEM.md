# OPai — Design System

The shipped OPai GUI (`opai gui`) renders with **Chromium (QtWebEngine) + CSS**,
not Qt/QSS. "Tokens" are CSS custom properties declared in `:root` of
`opai/assets/web/styles.css`; components are DOM elements styled by class. This
doc is the contract so the surface stays consistent as it grows. See
[`WEB_UI.md`](WEB_UI.md) for the architecture (bridge, boot payload, module
layout).

> The classic Qt/QSS window in `opai/gui_desktop.py` is a **fallback** only
> (`opai gui --classic`, or machines without QtWebEngine). Its tokens live as
> Python constants in that module and mirror the palette below; the web surface
> is the product.

## Colour tokens (`styles.css` `:root`)

Visual language: *Cockpit × Honest Ledger* — deep instrument-panel surfaces,
glass pods, an emerald money-signal, mono type reserved for truth surfaces
(commands, receipts, the CLI mirror).

| Token | Value | Use |
| --- | --- | --- |
| `--bg` | `#0a0c11` | app backdrop |
| `--surface` / `--surface-2` | `#0d1016` / `#11151c` | sidebar / control panel shells |
| `--panel` / `--panel-2` | `#151a22` / `#1b212b` | cards / hover |
| `--elevated` | `#222a36` | menus, dialogs, raised pods |
| `--glass` / `--glass-hi` | `rgba(255,255,255,.025)` / `.05` | glass fills / hover |
| `--border` / `--border-strong` | `rgba(255,255,255,.06)` / `.14` | hairlines / focus |
| `--ink` / `--muted` / `--faint` | `#eef2f8` / `#9aa6b6` / `#5d6878` | text primary / secondary / tertiary |
| `--accent` / `--accent-2` / `--accent-deep` | `#34d399` / `#2bd4bf` / `#1c9e70` | emerald — primary action + money/working signal |
| `--accent-ink` / `--accent-glow` | `#04241a` / `rgba(52,211,153,.20)` | text on accent / glow |
| `--brand-gradient` | `linear-gradient(135deg,#34d399,#2bd4bf)` | brand accents, meters |
| `--green` / `--amber` / `--red` | `#34d399` / `#e6b15e` / `#f2768a` | success / warning / danger |
| `--blue` | `#74b6ff` | info / links |
| `--claude` / `--codex` | `#e0937a` / `#6cc1e8` | provider dots |

Depth & shape tokens: `--shadow-sm/md/lg`, `--inset-hi`, radii `--r-sm` `8px` ·
`--r-md` `12px` · `--r-lg` `16px` · `--r-xl` `22px`, and motion easing `--ease`
(`cubic-bezier(0.4, 0, 0.2, 1)`).

**Every `var(--x)` reference must resolve to a token defined here** — a
CI contract test (`tests/test_ui_honesty_sweep.py`) fails on any undefined
custom property, so styling can't silently break (e.g. a borderless confirm).

## Typography

- **UI:** Inter (SIL OFL), loaded via `@font-face` from
  `opai/assets/fonts/Inter-Variable.ttf` (+ italic), with
  `-webkit-font-smoothing: antialiased`. Fallback stack: Segoe UI Variable Text
  → Segoe UI → system-ui. Inter is neutral (not rounded) on purpose —
  developer-grade.
- **Mono:** reserved for *truth surfaces* — commands, receipts, raw tool output,
  the CLI mirror.

## Components (class names)

| Class | Component |
| --- | --- |
| `.side`, `.brand`, `.nav-group-btn`, `.new-chat` / `.new-app`, `.side-foot` | left sidebar |
| `.topbar`, `.topbar-icon`, `.topbar-action`, `.ws-switch`, `.header-divider`, `.header-stat` | header + workspace switcher |
| `#panel` inspector rows, `.meter`, privacy badges | right control panel |
| `.page-title`, `.page-sub`, cards, KPI chips | dashboard / settings pages |
| `#composer`, `#input`, model/mode `select`s, send button | composer controls |
| `.user`, bot/tool bubbles, `.footer-note`, `.receipt-card` / `.rc-ledger`, `.tl-row` timeline | conversation |
| `.inline-confirm` (`.danger`) | inline confirmations (e.g. Full Auto pin) |

## Interaction rules

- One primary action per surface (emerald send / new-chat). Everything else is a
  ghost/outline control or a nav item.
- Hover always lightens by one step (`--panel` → `--panel-2`, `--glass` →
  `--glass-hi`, `--border` → `--border-strong`).
- Motion only moves when OPai is genuinely working; transitions use `--ease`.
- Destructive/mutating actions (panic, repair, Full Auto pin) always go through
  an `.inline-confirm`; read actions never prompt.
- **Labels must be honest** (#400): a control's text and `aria-label` name where
  it actually goes. The receipt's "Summary →" opens the aggregate savings
  dashboard (an itemized ledger lands with #390); the truncation marker states
  steps were dropped, not that a full record is retrievable.

## Keyboard & accessibility rules

- Real text everywhere (selectable); no text baked into images.
- Keyboard-first: the command palette (`Ctrl+K`) reaches every command, and
  **every shortcut advertised in the palette or help actually fires** — wired in
  `app.js` `wire()` and asserted by `tests/test_ui_honesty_sweep.py`. Current
  set: `Ctrl+K` palette · `Ctrl+N` new · `Ctrl+L` focus · `Ctrl+P` prompts ·
  `Ctrl+I` panel · `Ctrl+O` folder · `Ctrl+M` model · `Ctrl+B` sidebar · `Esc`
  stop · `?` shortcuts.
- Colour is never the only signal — permission state shows the word
  (`allow/ask/block`) alongside the colour; provider dots pair with names.
- Single-key shortcuts (`?`) are suppressed while typing in a field.

## Do / don't

- **Do** keep logic in the Qt-free Python helpers (`gui_*`, `gui_view_model`) so
  it's unit-tested; the front-end only renders the JSON the bridge hands it.
- **Do** reference an existing `--token`; if you need a new colour, add it to
  `:root` first (the CI check will otherwise fail).
- **Don't** introduce a second source of truth for run-mode labels — they come
  from `opaihub/autonomy.py` `MODE_LABELS`, delivered to the web via the boot
  payload's `modes` list (#400).
- **Don't** add always-on panels that crowd the chat; power belongs in the
  toggleable inspector, the palette, and pages.
