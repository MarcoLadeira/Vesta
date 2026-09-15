# Vesta Desktop — Premium Workspace Redesign

> **Follow-up: the surface now renders in Chromium, not Qt.** After this Qt/QSS
> redesign, the rendering quality still read "crusty/outdated" next to
> Cursor/Claude (Qt's text engine + weak CSS were the ceiling). `vesta gui` now
> renders the same information architecture as a hand-built HTML/CSS/JS UI inside
> a `QWebEngineView` (real Inter font smoothing, depth, transitions) at zero new
> dependency. See **`WEB_UI.md`**. The IA, controls, and data layer below are
> unchanged — only the rendering moved from QSS to Chromium. The Qt window
> remains as `vesta gui --classic`.

_Stack reality: `vesta gui` is a **PySide6 desktop app** (`opai/gui_desktop.py`),
not a web app. There is no React/Tailwind/Next/Playwright layer. This report
maps the product brief's intent onto the real Qt stack — "pages" are Qt views in
a `QStackedWidget`, "E2E" is a headless offscreen render of each view, "design
tokens" are Python colour constants + QSS._

## Before → after

| | Before | After |
| --- | --- | --- |
| Sidebar | 2 hardcoded buttons (Connect, Settings) + recents | Grouped nav: **Workspace** (Chat, Prompt Library), **Dashboard** (Money Saved, Cost Firewall, Context Waste, Benchmark, Agents, Proof Bundle, Workflows), **System** (Settings) + recents, active-item highlight |
| Project folder | static label showing a path you couldn't change | **Workspace switcher**: header button → folder picker + recent workspaces, persisted; switching re-points all state |
| Main area | chat only | `QStackedWidget`: chat **+ 7 data-backed dashboard pages + prompt library + real settings page** |
| Right panel | none | **Session inspector / control panel** (toggleable, Ctrl+I): model, run mode, task focus, output format, workspace, permissions, **budget meter**, **colour-coded tool permissions**, **privacy badges** |
| AI control | model + run mode | + **task focus** (Build/Debug/Explain/Refactor/Test/Plan/Review) and **output format** (steps/table/code/bug/JSON…), both shaping the real prompt |
| Settings | a text dump from the `budget` tool | a real page: defaults, cost firewall (+ panic toggle), tool permissions, accounts (+ connect), privacy, about |
| Typeface | Nunito (rounded, reads childish) | **Inter** (crisp neutral SaaS standard) + high-DPI pass-through + antialiasing |

## Why it isn't a fake skin

The biggest unlock was that Vesta already computes far more than the chat ever
showed. `gui_view_model.build_view_model()` produces 8 full dashboard sections;
`app_state.inspector_state()` exposes real budget/workspace telemetry;
`cost_firewall()` knows the gates. The redesign **surfaces existing real data**
into pages and an inspector — it does not invent numbers. The tool-permission
panel is derived from the same run mode the engine enforces (`gui_permissions`),
so it cannot drift into a comforting lie.

## New UX principles

1. **Calm by default, power on demand.** Chat is still the default surface; the
   control panel and dashboards are there when wanted, not in your face.
2. **Always know the AI's state.** Header strip + inspector show model, mode,
   spend, and exactly what the AI may touch — at all times.
3. **Honest controls only.** Every control maps to real behaviour (prompt
   shaping, run mode, panic) — nothing is a decorative placeholder.
4. **Testable surface.** All display logic lives in Qt-free modules so CI
   verifies it headlessly; a render smoke proves every page builds.

## New layout (Qt)

```
┌ Sidebar ─┐┌ Header: workspace switcher · status strip · Inspector toggle ┐┌ Control ┐
│ New chat ││┌ QStackedWidget ───────────────────────────────────────────┐││ Session │
│ Workspace│││  chat │ prompts │ dashboard(×7 sections) │ settings        │││ focus   │
│ Dashboard│││                                                            │││ format  │
│ System   │││  (composer lives on the chat page)                        │││ rows    │
│ Recents  ││└────────────────────────────────────────────────────────────┘││ budget  │
│ Account  ││                                                              ││ perms   │
└──────────┘└──────────────────────────────────────────────────────────────┘│ privacy │
                                                                             └─────────┘
```

## New features added

| Feature | Status |
| --- | --- |
| Workspace switcher (folder picker + recents) | Implemented |
| Grouped sidebar navigation | Implemented |
| 7 data-backed dashboard pages | Implemented |
| Right control panel / session inspector | Implemented |
| Task focus modes (prompt prefacing) | Implemented |
| Output format selector (prompt shaping) | Implemented |
| Prompt library (search + categories) | Implemented |
| Real settings page | Implemented |
| Command palette + shortcuts (Ctrl+P/I/O/B added) | Implemented |
| Inter font + high-DPI/antialiasing | Implemented |
| Tool-permission view (mode-derived) | Implemented |
| Privacy badges | Implemented |
| Dashboard actions (panic/repair real; others copy command) | Partially implemented |
| Workflows page (surfaces guarded workflows) | Partially implemented (read + copy) |
| Multi-model compare, workflow queue, team/billing, light theme, icon set | Documented for roadmap (see FEATURE_ROADMAP.md) |

## Screens / components changed

- `opai/gui_desktop.py` — full window rewrite (stacked views, sidebar, header
  switcher, control panel, dashboard/settings/prompts renderers, Inter + DPI).
- New Qt-free modules: `gui_nav`, `gui_modes`, `gui_permissions`, `gui_prompts`,
  `gui_workspace`; extended `gui_controls` (inspector + privacy badges, new
  commands/shortcuts).
- `opaihub/gui_preferences.py` — persist task focus, output format, panel state.

## Remaining UX risks

| Risk | Severity | Note |
| --- | --- | --- |
| No glyph icons in nav | Low | Deliberate (text-only renders consistently); icon font/SVG is a roadmap item |
| Some dashboard actions copy a command rather than executing | Low | Safe by design; only panic/repair mutate, and only via confirm |
| Light theme not implemented | Low | Dark-only today; tokens are centralised so a light map is feasible |
| Context "files included" chips are not editable yet | Medium | Inspector shows indexed-file count; add/remove file chips is roadmap |

## Release verdict

**PREMIUM UX READY FOR LOCAL DEMO.** The chat is now a real, controllable AI
workspace — switchable projects, data-backed dashboards, a full session
inspector, honest tool-permission visibility, and a crisp premium typeface —
without sacrificing the calm-chat default. Heavier items (multi-model compare,
live workflow queue, billing, light theme) are documented as roadmap, not
half-built.
