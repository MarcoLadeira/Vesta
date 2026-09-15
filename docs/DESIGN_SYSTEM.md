# Vesta — Design System

The shipped Vesta GUI (`vesta gui`) renders with **Chromium (QtWebEngine) + CSS**,
not Qt/QSS. Tokens are CSS custom properties in
`vesta/assets/web/design-tokens.css`; components are DOM elements styled by class
in `styles.css`. This doc is the contract so the surface stays consistent as it
grows. See [`WEB_UI.md`](WEB_UI.md) for the architecture (bridge, boot payload,
module layout).

> The classic Qt/QSS window in `vesta/gui_desktop.py` is a **fallback** only
> (`vesta gui --classic`, or machines without QtWebEngine). Its tokens live as
> Python constants in that module and mirror the palette below; the web surface
> is the product.

## Web UI token contract (#388)

The browser shell uses the same visual language through one source of truth:
`vesta/assets/web/design-tokens.css`. `styles.css` must consume its tokens rather
than adding raw type or layout-spacing pixels. `npm run test:tokens` enforces
this locally and in the web CI job. Open the renderable [token preview](../vesta/assets/web/design-tokens-preview.html)
in a browser for the canonical type, spacing, elevation, and icon examples.

| Area | Tokens | Rules |
| --- | --- | --- |
| Type | `--type-caption` through `--type-display` | Use body (14px) for readable defaults; each semantic role has an approved line-height and weight. |
| Spacing | `--space-1` (4px), `--space-2` (8px), `--space-3` (12px), `--space-4` (16px), `--space-6` (24px), `--space-8` (32px) | Use only the 4px rhythm. The token linter rejects unrecognised spacing token names as well as raw pixels. |
| Radius | `--radius-sm`, `--radius-md`, `--radius-lg`, `--radius-xl` | Components use the alias pair `--r-*`; no component invents a new radius. |
| Elevation | `--elevation-sm`, `--elevation-md`, `--elevation-lg`, `--inset-hi` | Elevation communicates layer, never status. Status remains semantic colour plus text. |
| Icons | `icons.js`, `--icon-sm/md/lg` | Inline SVG only, `currentColor`, 16px default. Decorative icons are `aria-hidden`; controls retain an accessible text or `aria-label`. Emoji belongs only in user-provided content. |

### Rendered web type scale

| Role | Token | Size | Line height | Weight | Rendered example |
| --- | --- | ---: | ---: | ---: | --- |
| Caption | `--type-caption` | 12px | 1.35 | 500 | `Small explanatory copy` |
| Label | `--type-label` | 13px | 1.4 | 600 | `Section label` |
| Body | `--type-body` | 14px | 1.5 | 400 | `Readable default body copy` |
| Body large | `--type-body-lg` | 16px | 1.5 | 400 | `Emphasised supporting copy` |
| Heading | `--type-heading` | 20px | 1.25 | 700 | `Surface heading` |
| Title | `--type-title` | 24px | 1.25 | 700 | `Page title` |
| Display | `--type-display` | 32px | 1.1 | 800 | `Build more. Burn less.` |

The linter intentionally permits one-pixel borders and positional offsets; it
enforces `font-size`, `margin`, `padding`, and `gap`, which are the values that
govern visual rhythm. Verify normal and compact density after changing a core
surface.

## Colour tokens (`design-tokens.css`)

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

**Every `var(--x)` reference in `styles.css` must resolve to a token defined in
`design-tokens.css` (or `styles.css` itself)** — unless it carries a fallback
(`var(--x, …)`). A CI contract test (`tests/test_ui_honesty_sweep.py`) fails on
any no-fallback reference to an undefined custom property, so styling can't
silently break the way a borderless confirm once did.

## Typography

- **UI:** Inter (SIL OFL), loaded via `@font-face` from
  `vesta/assets/fonts/Inter-Variable.ttf` (+ italic), with
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
- Motion only moves when Vesta is genuinely working; transitions use `--ease`.
- Destructive/mutating actions (panic, repair, Full Auto pin) always go through
  an `.inline-confirm`; read actions never prompt.
- **Labels must be honest** (#400): a control's text and `aria-label` name where
  it actually goes. The receipt's "Summary" button opens the aggregate savings
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
- **Do** consume an existing token; if you need a new colour or spacing value,
  add it to `design-tokens.css` first (the token linter and the honesty check
  will otherwise fail).
- **Don't** introduce a second source of truth for run-mode labels — they come
  from `vestahub/autonomy.py` `MODE_LABELS`, delivered to the web via the boot
  payload's `modes` list (#400).
- **Don't** add always-on panels that crowd the chat; power belongs in the
  toggleable inspector, the palette, and pages.
