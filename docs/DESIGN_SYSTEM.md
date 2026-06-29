# OPai Desktop — Design System

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
