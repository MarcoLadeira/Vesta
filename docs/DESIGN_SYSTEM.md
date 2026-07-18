# OPai Desktop — Design System

## Web UI token contract (#388)

The browser shell uses the same visual language through one source of truth:
`opai/assets/web/design-tokens.css`. `styles.css` must consume its tokens rather
than adding raw type or layout-spacing pixels. `npm run test:tokens` enforces
this locally and in the web CI job. Open the renderable [token preview](../opai/assets/web/design-tokens-preview.html)
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

The OPai GUI is **PySide6 + QSS**, not CSS. "Tokens" are Python constants in
`opai/gui_desktop.py`; components are Qt widgets styled by object name. This doc
is the contract so the surface stays consistent as it grows.

## Colour tokens (`gui_desktop.py`)

| Token | Hex | Use |
| --- | --- | --- |
| `BG` | `#1b1d21` | main surface |
| `SIDEBAR` | `#16181b` | left sidebar + right control panel |
| `COMPOSER` | `#23262b` | composer, menus, dialogs |
| `PANEL` / `PANEL_HI` | `#212429` / `#2c3036` | cards / hover |
| `USERBG` | `#262b33` | user message bubble |
| `BORDER` / `BORDER_HI` | `#2c3036` / `#3a414b` | hairlines / focus |
| `INK` / `MUTED` / `FAINT` | `#e8eaee` / `#9aa2af` / `#69707d` | text primary / secondary / tertiary |
| `ACCENT` / `ACCENT_HI` | `#34d399` / `#28bd86` | emerald — primary action + brand (savings signal) |
| `GREEN` / `AMBER` / `RED` | `#34d399` / `#e0a458` / `#ef6b7d` | success / warning / danger |
| `CLAUDE` / `CODEX` | `#d6896a` / `#58b0d6` | provider dots |

`SEVERITY_COLOR` maps semantic states (`success/warning/danger/accent/info/
neutral` and permission `allow/ask/block`, badge `safe/warn`) onto those colours
so dashboard cards, KPI chips, badges, and permission rows stay consistent.

## Typography

- **UI:** Inter (SIL OFL), bundled in `opai/assets/fonts/Inter-Variable.ttf`,
  loaded at startup. Fallback stack: Segoe UI Variable Text → Segoe UI →
  system-ui. Inter is neutral (not rounded) on purpose — developer-grade.
- **Mono:** Cascadia Code → JetBrains Mono → Consolas (diffs, raw tool output,
  commands).
- High-DPI: `HighDpiScaleFactorRoundingPolicy.PassThrough` + `PreferAntialias`
  so text is crisp on scaled displays.

## Components (object names)

| Object name | Component |
| --- | --- |
| `#Sidebar`, `#Brand`, `#SectionLabel`, `#NavItem` (`[active="true"]`), `#NewChat`, `#Recent` | left sidebar |
| `#HeaderBar`, `#WsSwitch`, `#HeaderStat` | header + workspace switcher |
| `#ControlPanel`, `#PanelTitle`, `#PanelLabel`, `#RowKey`, `#RowVal`, `#Meter` | right inspector |
| `#PageTitle`, `#PageSub`, `#SettingHead`, `#Card`, `#CardTitle/Body/Foot`, `#Kpi`, `#KpiLabel/Value`, `#HeroNum` | dashboard / settings pages |
| `#Composer`, `#Input`, `#Pick`, `#Ghost`, `#Send` | composer controls |
| `#UserBubble`, `#BotBubble`, `#ToolBubble`, `#Role`, `#Footer`, `#Mono` | conversation |

## Interaction rules

- One primary action per surface (emerald `#Send` / `#NewChat`). Everything else
  is `#Ghost` (outline) or a nav item.
- Active nav state = filled panel + 2px emerald left border.
- Hover always lightens by one step (`PANEL` → `PANEL_HI`, border → `BORDER_HI`).
- Destructive/mutating actions (panic, repair) always go through a confirm
  dialog; read actions never prompt.

## Accessibility rules

- Real text everywhere (selectable); no text baked into images.
- Tooltips carry the "why" (model capability badges, permission notes, workspace
  switcher hint).
- Keyboard-first: every primary action has a shortcut (see `SHORTCUTS`) and the
  command palette (Ctrl+K) reaches all of them; Esc closes dialogs / stops.
- Colour is never the only signal — permission state shows the word
  (`allow/ask/block`) alongside the colour.

## Do / don't

- **Do** keep logic in the Qt-free helpers so it's unit-tested; widgets only
  render payloads.
- **Do** add new dashboard data via `gui_view_model` sections (they render
  automatically through the dashboard page).
- **Don't** introduce a second source of truth for run-mode safety — the run
  mode picker is authoritative; task focus only prefaces.
- **Don't** add always-on panels that crowd the chat; power belongs in the
  toggleable inspector, the palette, and pages.
