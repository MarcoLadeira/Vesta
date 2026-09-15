# Vesta Desktop — Web-Rendered UI

`vesta gui` now renders its surface with **Chromium (QtWebEngine)** instead of Qt
widgets/QSS. Qt's styling and text engine couldn't match the polish of
Cursor/Claude (no real font smoothing, weak shadows/blur, no transitions);
Chromium gives real CSS, `@font-face` Inter with antialiasing, depth, and
animation — at **zero new dependency** (QtWebEngine ships with PySide6 here).

## Architecture

```
vesta gui  ─▶  cmd_gui (cli.py)
                 │  web_available()?  ── yes ─▶ vesta/gui_web.py  (QWebEngineView)
                 │                                   │
                 │                                   ├─ QWebChannel ──▶ Bridge (QObject)
                 │                                   │      slots return JSON / emit signals
                 │                                   └─ loads vesta/assets/web/index.html
                 └─  no / --classic  ─▶ vesta/gui_desktop.py (classic Qt window, fallback)
```

- **`vesta/gui_web.py`** — the QWebEngine host + the `Bridge`. The bridge reuses
  the *exact same Qt-free data modules* the Qt UI used (`gui_nav`, `gui_modes`,
  `gui_permissions`, `gui_prompts`, `gui_workspace`, `gui_view_model`,
  `gui_controls`). One source of truth for both surfaces. The front-end never
  computes anything sensitive — it renders JSON the bridge hands it.
- **`vesta/assets/web/`** — hand-built front-end: `index.html`, `styles.css`,
  `app.js`, plus focused modules loaded as plain `<script>`s before `app.js`
  (`activity.js`, `message-state.js`, `settings.js`). Inter is loaded via
  `@font-face` from `../fonts/Inter-Variable.ttf` with
  `-webkit-font-smoothing: antialiased`. New JS modules must be added to
  `REQUIRED_WEB_ASSETS` in `vestahub/desktop_artifacts.py` (and the smoke check)
  so they ship in the packaged desktop app.
- **Fallback** — `vesta gui --classic` (or any machine without QtWebEngine) uses
  the classic Qt window in `gui_desktop.py`, which stays fully tested.

### Settings surface (`settings.js`, #217)

The settings page is a **section registry**, not one long function. Each entry
in `window.VestaSettings.sections` is
`{ id, title, icon, keywords, render(d, ctx) }`; `VestaSettings.render(page, ctx)`
lays out **Claude-style paned pages**: the left rail is real page navigation —
one cleanly labelled page visible at a time (`.settings-pane.active`), with
`aria-current="page"` on the active rail item and `#settings/<id>` deep links
that open a page directly. Hidden pages stay in the DOM (wiring stays simple),
so on-screen test assertions must use innerText semantics
(`toContainText(..., { useInnerText: true })`), not textContent.

Search (#240) stays global: typing flips the layout into a cross-page results
mode (`.settings-layout.searching`) where every page shows only matching
blocks, each labelled with its page title; clearing the query returns to the
page the user was on. Adding a settings page is a registry entry here — no
`app.js` edit. `app.js` keeps only the async data fetch and hands the registry
a `ctx` bundle of shared dependencies (`bridge`, `esc`, `toast`, `switchView`,
`updateDoctorCard`, …). The `el(tag, attrs, children)` helper builds DOM without
routing dynamic values through `innerHTML` (text → `textContent`), and is
unit-tested in `__tests__/settings.test.js`.

## Bridge API (Python → JS)

| Slot | Returns | Purpose |
| --- | --- | --- |
| `boot()` | JSON | the whole shell payload in one call |
| `inspector(selJson)` | JSON | recompute the session inspector on selection change |
| `statusLine(selJson)` | JSON | the header status strip |
| `dashboard(sectionId)` | JSON | a `gui_view_model` section |
| `prompts(query, cat)` | JSON | filtered prompt library |
| `settingsData()` | JSON | the settings page payload |
| `savePref(key, value)` | — | persist a GUI preference |
| `send(payloadJson)` | signal `replyReady` | run a chat turn off-thread, fail-open |
| `runTool(name)` / `applyTool(name)` | signal `toolReady` / JSON | tools + confirm |
| `openWorkspace()` / `switchWorkspace(path)` | signal `workspaceChanged` | switch project |
| `openExternal(url)` | — | open http(s) links in the system browser |

## Startup budget & instrumentation (#246)

Cold start (process start → the chat view interactive) has a **budget of
≤ 1.5 s** on a warm profile. The dominant cost is the single `boot_payload`
call, so that is where the budget is spent and measured.

**Instrumentation** (`vestahub/startup_trace.py`) is **off by default** and
**never leaves the machine**. Set `VESTA_STARTUP_TRACE=1` to record the major
init stages (`boot:start → boot:prefs → boot:models → boot:workflow →
boot:done → interactive`); the trace is appended as JSONL to
`<workspace>/.vestahub/gui/startup-trace.jsonl`. Disabled, every mark is a no-op
and no file is written.

**Deferral (measured win):** the session **inspector payload is deferred** —
`boot_payload` returns `inspector: null` instead of computing budget/
permissions/workflow eagerly, because the inspector panel is hidden by default
(`show_control_panel`). The front-end fetches it via the `inspector()` slot only
when the panel is shown (at boot if visible, else on first open), so cold boot
skips it. Measured on a fixture repo: `boot_payload` **~688 ms → ~556 ms
(−132 ms, −19%)** with the inspector deferred; no behaviour change (the same
data renders whenever the panel is opened).

## Safety properties kept

- **No secrets in the page.** The bridge only sends already-redacted view-model
  data; `tests/test_gui_web.py` asserts a recorded secret never appears in the
  boot JSON.
- **Escape-first rendering.** Model answers are rendered by a small markdown
  function in `app.js` that escapes HTML first; only `http(s)` links become
  anchors, and they open via the bridge, not inline navigation.
- **Honest controls.** Permissions shown in the page come from
  `gui_permissions.permissions_for(run_mode)` — the same mapping the engine
  enforces.

## How to change the UI

- Visuals: edit `vesta/assets/web/styles.css` (design tokens are CSS variables at
  the top) and `index.html`.
- Behaviour/new data: add a `Bridge` slot in `gui_web.py` returning JSON from the
  existing data modules, then render it in `app.js`. Prefer adding data to a
  `gui_view_model` section — the dashboard page renders sections automatically.
- Tests: `tests/test_gui_web.py` covers the bridge payloads + asset wiring
  headlessly (Chromium rendering is verified by launching the app, not in CI).
