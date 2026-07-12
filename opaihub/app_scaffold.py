"""Deterministic app scaffolding — the free-boilerplate foundation of OPai Build
(#276).

The cheapest tokens are the ones you never spend. Instead of paying a model to
emit every line of an app skeleton (what Lovable does), OPai writes the runnable
boilerplate deterministically here — HTML shell, CSS reset, JS structure, README
— for **zero tokens**. AI spend then goes only to the custom logic, so the same
budget buys many more iteration prompts.

Templates are dependency-free and instantly runnable (open the file or
``python -m http.server``), so the "see it work" preview loop needs no install
step. Kept Qt-free and network-free so it is unit-tested hermetically.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class ScaffoldResult:
    """What a scaffold produced — a manifest the CLI/GUI renders and the
    customization loop reads."""

    name: str
    kind: str
    root: str
    description: str
    files: list[str]
    entrypoint: str
    preview_cmd: str
    next_steps: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "kind": self.kind,
            "root": self.root,
            "description": self.description,
            "files": self.files,
            "entrypoint": self.entrypoint,
            "preview_cmd": self.preview_cmd,
            "boilerplate_tokens_avoided": estimate_boilerplate_tokens(
                self.files, self.root
            ),
            "next_steps": self.next_steps,
        }


def slugify(text: str, *, fallback: str = "app") -> str:
    """A safe directory/name slug from a free-text app idea."""
    slug = re.sub(r"[^a-z0-9]+", "-", str(text or "").lower()).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    return slug[:48] or fallback


def _titleize(text: str, *, fallback: str = "App") -> str:
    words = re.sub(r"[^A-Za-z0-9 ]+", " ", str(text or "")).split()
    return " ".join(w[:1].upper() + w[1:] for w in words) if words else fallback


# ---- Templates ------------------------------------------------------------- #
# Each template is {relative_path: content}. Placeholders (never str.format, so
# CSS/JS braces are safe): __APP_TITLE__, __APP_NAME__, __APP_DESC__.

_WEB_INDEX = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>__APP_TITLE__</title>
  <link rel="stylesheet" href="styles.css">
</head>
<body>
  <header class="topbar">
    <h1>__APP_TITLE__</h1>
    <button id="themeToggle" class="ghost" aria-label="Toggle dark mode">Dark</button>
  </header>
  <main id="app" class="container">
    <!-- OPai scaffolds the shell for free; ask OPai to build the features here. -->
  </main>
  <footer class="foot">Built with OPai · __APP_DESC__</footer>
  <script src="app.js"></script>
</body>
</html>
"""

_WEB_STYLES = """/* __APP_TITLE__ — zero-dependency starter styles. */
:root { --bg:#0d0f14; --fg:#e8ecf2; --muted:#9aa4b2; --accent:#34d399; --card:#161a22; --line:#242a35; }
:root[data-theme="light"] { --bg:#f7f8fa; --fg:#161a22; --muted:#5b6472; --accent:#0ea472; --card:#ffffff; --line:#e3e6ec; }
* { box-sizing: border-box; }
body { margin:0; font:16px/1.5 system-ui, -apple-system, Segoe UI, Roboto, sans-serif; background:var(--bg); color:var(--fg); }
.topbar { display:flex; align-items:center; justify-content:space-between; padding:16px 22px; border-bottom:1px solid var(--line); }
.topbar h1 { font-size:18px; margin:0; }
.container { max-width:720px; margin:32px auto; padding:0 22px; }
.card { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:20px; margin:14px 0; }
button { font:inherit; cursor:pointer; border-radius:8px; border:1px solid var(--accent); background:var(--accent); color:#06160f; padding:8px 14px; }
button.ghost { background:transparent; color:var(--fg); border-color:var(--line); }
.foot { color:var(--muted); text-align:center; padding:24px; font-size:13px; }
"""

_WEB_APP_JS = """/* __APP_TITLE__ — starter app. Zero dependencies; open index.html or run
   `python -m http.server` in this folder. Ask OPai to build features into
   render() and the state below. */
(function () {
  "use strict";

  // --- state (ask OPai to extend this) ---
  const state = { greeting: "Welcome to __APP_TITLE__" };

  // --- render (ask OPai to build the UI here) ---
  function render() {
    const app = document.getElementById("app");
    app.innerHTML =
      '<section class="card">' +
      "<h2>" + escapeHtml(state.greeting) + "</h2>" +
      "<p>__APP_DESC__</p>" +
      '<p style="color:var(--muted)">This runnable shell was scaffolded by OPai for zero tokens. ' +
      'Ask OPai to build the real features — every edit is a small, cheap diff.</p>' +
      "</section>";
  }

  // --- dark mode toggle (a working feature out of the box) ---
  function initTheme() {
    const btn = document.getElementById("themeToggle");
    const root = document.documentElement;
    btn.addEventListener("click", function () {
      const light = root.getAttribute("data-theme") === "light";
      root.setAttribute("data-theme", light ? "dark" : "light");
      btn.textContent = light ? "Dark" : "Light";
    });
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    initTheme();
    render();
  });
})();
"""

_WEB_README = """# __APP_TITLE__

__APP_DESC__

Scaffolded by **OPai Build** — the runnable skeleton was generated for free
(zero tokens). Now build the real features with cheap, targeted prompts.

## Preview it (no install needed)

```
python -m http.server 8000
```

Then open http://localhost:8000 — or just open `index.html` in a browser.

## Build features with OPai

```
opai build "implement <the feature you want>"
```

OPai sends only the relevant files, gets back complete updated files, and
applies them itself with a backup of anything it overwrites. Each prompt is a
small diff routed to the cheapest capable model, so your budget buys many
iterations — and you get a savings receipt.

## Files
- `index.html` — page shell
- `styles.css` — starter styles (light/dark tokens)
- `app.js` — state + render (build features here)
"""

_STATIC_INDEX = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>__APP_TITLE__</title>
  <link rel="stylesheet" href="styles.css">
</head>
<body>
  <main class="hero">
    <h1>__APP_TITLE__</h1>
    <p>__APP_DESC__</p>
    <a class="cta" href="#">Get started</a>
  </main>
  <footer>Built with OPai</footer>
</body>
</html>
"""

_STATIC_STYLES = """/* __APP_TITLE__ — static landing page. */
:root { --bg:#0d0f14; --fg:#e8ecf2; --muted:#9aa4b2; --accent:#34d399; }
* { box-sizing:border-box; } body { margin:0; font:16px/1.6 system-ui, sans-serif; background:var(--bg); color:var(--fg); }
.hero { min-height:80vh; display:flex; flex-direction:column; align-items:center; justify-content:center; text-align:center; padding:24px; }
.hero h1 { font-size:44px; margin:0 0 12px; }
.hero p { color:var(--muted); max-width:520px; }
.cta { margin-top:20px; background:var(--accent); color:#06160f; padding:12px 22px; border-radius:10px; text-decoration:none; font-weight:600; }
footer { text-align:center; color:var(--muted); padding:24px; font-size:13px; }
"""

# ---- data (list/CRUD) template ---------------------------------------------- #
# Most "build me an app" requests (todo, notes, tracker, expenses, groceries)
# are a list you add to, persist, and remove from. This ships that WORKING out
# of the box — add/render/delete + localStorage — so the first prompt only has
# to customise, not build from scratch.

_DATA_INDEX = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>__APP_TITLE__</title>
  <link rel="stylesheet" href="styles.css">
</head>
<body>
  <header class="topbar">
    <h1>__APP_TITLE__</h1>
    <button id="themeToggle" class="ghost" aria-label="Toggle dark mode">Dark</button>
  </header>
  <main class="container">
    <form id="addForm" class="add-row" autocomplete="off">
      <input id="addInput" type="text" placeholder="Add an item…" aria-label="New item">
      <button type="submit">Add</button>
    </form>
    <ul id="list" class="list" aria-live="polite"></ul>
    <p id="empty" class="empty" hidden>Nothing yet — add your first item above.</p>
  </main>
  <footer class="foot">Built with OPai · __APP_DESC__</footer>
  <script src="app.js"></script>
</body>
</html>
"""

_DATA_STYLES = """/* __APP_TITLE__ — list app styles. */
:root { --bg:#0d0f14; --fg:#e8ecf2; --muted:#9aa4b2; --accent:#34d399; --card:#161a22; --line:#242a35; --danger:#f2768a; }
:root[data-theme="light"] { --bg:#f7f8fa; --fg:#161a22; --muted:#5b6472; --accent:#0ea472; --card:#ffffff; --line:#e3e6ec; --danger:#d64a63; }
* { box-sizing: border-box; }
body { margin:0; font:16px/1.5 system-ui, -apple-system, Segoe UI, Roboto, sans-serif; background:var(--bg); color:var(--fg); }
.topbar { display:flex; align-items:center; justify-content:space-between; padding:16px 22px; border-bottom:1px solid var(--line); }
.topbar h1 { font-size:18px; margin:0; }
.container { max-width:620px; margin:28px auto; padding:0 22px; }
.add-row { display:flex; gap:8px; }
.add-row input { flex:1; padding:10px 12px; font:inherit; background:var(--card); color:var(--fg); border:1px solid var(--line); border-radius:8px; outline:none; }
.add-row input:focus { border-color:var(--accent); }
button { font:inherit; cursor:pointer; border-radius:8px; border:1px solid var(--accent); background:var(--accent); color:#06160f; padding:10px 16px; }
button.ghost { background:transparent; color:var(--fg); border-color:var(--line); padding:8px 14px; }
.list { list-style:none; padding:0; margin:18px 0 0; display:flex; flex-direction:column; gap:8px; }
.item { display:flex; align-items:center; justify-content:space-between; gap:12px; background:var(--card); border:1px solid var(--line); border-radius:10px; padding:12px 14px; }
.item.done .item-text { text-decoration:line-through; color:var(--muted); }
.item-text { flex:1; cursor:pointer; }
.item-del { background:none; border:0; color:var(--danger); cursor:pointer; font-size:18px; padding:0 4px; }
.empty { color:var(--muted); text-align:center; margin-top:24px; }
.foot { color:var(--muted); text-align:center; padding:24px; font-size:13px; }
"""

_DATA_APP_JS = """/* __APP_TITLE__ — a working list app. Zero dependencies; open index.html or
   run `python -m http.server`. Items persist in localStorage. Ask OPai to add
   features (due dates, filters, categories) — each edit is a cheap diff. */
(function () {
  "use strict";

  var KEY = "__APP_NAME__.items";
  var items = load();

  function load() {
    try { return JSON.parse(localStorage.getItem(KEY)) || []; }
    catch (e) { return []; }
  }
  function save() {
    try { localStorage.setItem(KEY, JSON.stringify(items)); } catch (e) { /* ignore */ }
  }

  function addItem(text) {
    text = String(text || "").trim();
    if (!text) return;
    items.push({ id: Date.now() + "-" + Math.random().toString(16).slice(2), text: text, done: false });
    save();
    render();
  }
  function toggleItem(id) {
    items.forEach(function (it) { if (it.id === id) it.done = !it.done; });
    save();
    render();
  }
  function deleteItem(id) {
    items = items.filter(function (it) { return it.id !== id; });
    save();
    render();
  }

  function render() {
    var list = document.getElementById("list");
    var empty = document.getElementById("empty");
    list.innerHTML = "";
    items.forEach(function (it) {
      var li = document.createElement("li");
      li.className = "item" + (it.done ? " done" : "");
      var span = document.createElement("span");
      span.className = "item-text";
      span.textContent = it.text;
      span.addEventListener("click", function () { toggleItem(it.id); });
      var del = document.createElement("button");
      del.className = "item-del";
      del.setAttribute("aria-label", "Delete");
      del.textContent = "\\u00d7";
      del.addEventListener("click", function () { deleteItem(it.id); });
      li.appendChild(span);
      li.appendChild(del);
      list.appendChild(li);
    });
    empty.hidden = items.length !== 0;
  }

  function initTheme() {
    var btn = document.getElementById("themeToggle");
    var root = document.documentElement;
    btn.addEventListener("click", function () {
      var light = root.getAttribute("data-theme") === "light";
      root.setAttribute("data-theme", light ? "dark" : "light");
      btn.textContent = light ? "Dark" : "Light";
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    document.getElementById("addForm").addEventListener("submit", function (e) {
      e.preventDefault();
      var input = document.getElementById("addInput");
      addItem(input.value);
      input.value = "";
      input.focus();
    });
    initTheme();
    render();
  });
})();
"""

_DATA_README = """# __APP_TITLE__

__APP_DESC__

A **working** list app scaffolded by OPai Build for free — add items, click to
mark done, delete, and everything persists in your browser (localStorage). No
build step, no dependencies.

## Preview it

```
python -m http.server 8000
```

Then open http://localhost:8000 — or open `index.html` directly.

## Build features with OPai

```
opai build "add due dates to each item"
opai build "add a filter for done vs active"
```

Each prompt sends only the relevant files and applies a small, verified diff —
cheap, and you get a savings receipt.

## Files
- `index.html` — page + add form + list
- `styles.css` — list styles (light/dark tokens)
- `app.js` — load / add / toggle / delete / persist (build features here)
"""


_TEMPLATES: dict[str, dict[str, object]] = {
    "web": {
        "summary": "Zero-build interactive web app (HTML + CSS + vanilla JS)",
        "entrypoint": "index.html",
        "preview_cmd": "python -m http.server 8000",
        "files": {
            "index.html": _WEB_INDEX,
            "styles.css": _WEB_STYLES,
            "app.js": _WEB_APP_JS,
            "README.md": _WEB_README,
        },
    },
    "static": {
        "summary": "Static landing page (HTML + CSS)",
        "entrypoint": "index.html",
        "preview_cmd": "python -m http.server 8000",
        "files": {
            "index.html": _STATIC_INDEX,
            "styles.css": _STATIC_STYLES,
        },
    },
    "data": {
        "summary": "Working list/CRUD app with localStorage (todo, notes, tracker)",
        "entrypoint": "index.html",
        "preview_cmd": "python -m http.server 8000",
        "files": {
            "index.html": _DATA_INDEX,
            "styles.css": _DATA_STYLES,
            "app.js": _DATA_APP_JS,
            "README.md": _DATA_README,
        },
    },
}

# Keyword → kind for `--type auto`. First match wins; unmatched → "web".
_KIND_KEYWORDS: dict[str, tuple[str, ...]] = {
    "data": (
        "todo",
        "to-do",
        "task",
        "list",
        "note",
        "track",
        "tracker",
        "crud",
        "manage",
        "inventory",
        "expense",
        "budget",
        "habit",
        "grocery",
        "groceries",
        "shopping",
        "bookmark",
        "contact",
        "reminder",
        "journal",
        "checklist",
        "wishlist",
    ),
    "static": (
        "landing",
        "portfolio",
        "resume",
        " cv ",
        "brochure",
        "coming soon",
        "splash",
        "waitlist",
    ),
}


def infer_kind(description: str) -> str:
    """Pick the best template for a free-text app idea (for ``--type auto``).

    Most app requests are a persisted list (todo/notes/tracker) → the working
    ``data`` template; landing/portfolio phrasing → ``static``; else ``web``.
    """
    text = f" {str(description or '').lower()} "
    for kind, words in _KIND_KEYWORDS.items():
        if any(word in text for word in words):
            return kind
    return "web"


def kinds() -> list[str]:
    """Available scaffold kinds."""
    return list(_TEMPLATES)


def template_summary() -> dict[str, str]:
    return {kind: str(spec["summary"]) for kind, spec in _TEMPLATES.items()}


# Rough token estimate for the boilerplate we wrote for free (~4 chars/token) —
# powers the "tokens you didn't spend" line in the savings story.
def estimate_boilerplate_tokens(files: list[str], root: str) -> int:
    total_chars = 0
    for rel in files:
        try:
            total_chars += len((Path(root) / rel).read_text(encoding="utf-8"))
        except OSError:
            continue
    return round(total_chars / 4)


def scaffold_app(
    dest: Path,
    description: str,
    *,
    name: str | None = None,
    kind: str = "web",
    force: bool = False,
) -> ScaffoldResult:
    """Write a runnable app skeleton to ``dest/<name>`` (deterministic, no AI).

    Refuses to overwrite an existing non-empty directory unless ``force`` — a
    scaffold must never clobber a user's work. ``kind="auto"`` infers the best
    template from the description. Returns a manifest.
    """
    if kind == "auto":
        kind = infer_kind(description)
    if kind not in _TEMPLATES:
        raise ValueError(f"Unknown app kind '{kind}'. Available: {', '.join(kinds())}.")
    app_name = slugify(name or description)
    title = _titleize(name or description)
    desc = str(description or "").strip() or f"A new {kind} app."
    root = dest.expanduser().resolve() / app_name

    if root.exists() and any(root.iterdir()) and not force:
        raise FileExistsError(
            f"{root} already exists and is not empty. Use force to overwrite."
        )
    root.mkdir(parents=True, exist_ok=True)

    spec = _TEMPLATES[kind]
    written: list[str] = []
    for rel, content in spec["files"].items():  # type: ignore[assignment]
        text = (
            str(content)
            .replace("__APP_TITLE__", title)
            .replace("__APP_NAME__", app_name)
            .replace("__APP_DESC__", desc)
        )
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        written.append(rel)

    result = ScaffoldResult(
        name=app_name,
        kind=kind,
        root=str(root),
        description=desc,
        files=sorted(written),
        entrypoint=str(spec["entrypoint"]),
        preview_cmd=str(spec["preview_cmd"]),
        next_steps=[
            f"cd {root}",
            str(spec["preview_cmd"]) + "   # preview it (no install needed)",
            'opai build "implement <the first feature>"   # cheap targeted diff',
        ],
    )
    # The build manifest marks this directory as an OPai Build app — the
    # customization loop (`opai build`, opaihub/build_loop.py) reads it to
    # select context and apply edits safely.
    from opaihub.build_loop import save_app_manifest

    save_app_manifest(
        root,
        {
            "schema": 1,
            "name": app_name,
            "kind": kind,
            "description": desc,
            "entrypoint": result.entrypoint,
            "preview_cmd": result.preview_cmd,
            "files": result.files,
            "created_by": "opai new",
            # Persisted so the per-app receipt can always report what the
            # free scaffold saved, even long after creation (#276).
            "boilerplate_tokens_avoided": estimate_boilerplate_tokens(
                result.files, result.root
            ),
            "created_at": int(__import__("time").time()),
        },
    )
    return result
