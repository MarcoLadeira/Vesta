"""OPai desktop app — web-rendered UI (Chromium via QtWebEngine).

The window is a single ``QWebEngineView`` rendering a hand-built HTML/CSS/JS
front-end (``opai/assets/web/``). All of OPai's logic stays in Python and is
exposed to the page over a thin ``QWebChannel`` bridge — the front-end never
computes anything sensitive, it just renders JSON the bridge hands it.

Why web rendering: Qt's QSS/text engine can't match the polish of Cursor/Claude
(no real font smoothing, weak shadows/blur, no transitions). Chromium gives real
CSS, ``@font-face`` Inter with antialiasing, depth, and animation — at zero new
dependency (QtWebEngine ships with PySide6 here). ``gui_desktop.py`` (the Qt
version) remains as a fallback when QtWebEngine isn't available.

The bridge reuses the exact same Qt-free data modules the Qt UI used
(``gui_nav``, ``gui_modes``, ``gui_permissions``, ``gui_prompts``,
``gui_workspace``, ``gui_view_model``, ``gui_controls``), so there is one source
of truth for both surfaces.
"""

from __future__ import annotations

import importlib.util
import json
import threading
import uuid
from pathlib import Path
from typing import Any

from opai import app_state as A
from opai.gui_controls import header_status, model_badge, session_inspector
from opai.gui_modes import (
    DEFAULT_OUTPUT_FORMAT,
    DEFAULT_TASK_MODE,
    compose_prompt,
    output_formats,
    task_modes,
    task_summary,
)
from opai.gui_nav import DEFAULT_VIEW, nav_groups
from opai.gui_permissions import permission_summary, permissions_for
from opai.gui_prompts import categories_present, filter_prompts, find_prompt
from opai.gui_view_model import build_view_model
from opai.gui_workspace import (
    add_recent_workspace,
    is_valid_workspace,
    load_recent_workspaces,
    workspace_label,
)

WEB_DIR = Path(__file__).resolve().parent / "assets" / "web"


def web_available() -> bool:
    """True when QtWebEngine is importable (it ships with PySide6 here).

    ``find_spec`` raises ``ModuleNotFoundError`` for a submodule when the parent
    package (PySide6) is absent — e.g. on CI without the desktop extra — so we
    treat any import failure as "not available".
    """
    try:
        return (
            importlib.util.find_spec("PySide6.QtWebEngineWidgets") is not None
            and importlib.util.find_spec("PySide6.QtWebChannel") is not None
        )
    except (ModuleNotFoundError, ValueError):
        return False


def resolve_openable(root: Path, target: str) -> Path | None:
    """Resolve ``target`` to an absolute path that is safe to open in the OS.

    Allows the workspace root itself or any existing path under it (a changed
    file, a subfolder). Anything outside the project, or that doesn't exist,
    returns ``None`` — so the front-end can never ask the OS to open an
    arbitrary path.
    """
    try:
        base = root.expanduser().resolve()
        raw = Path(target or "")
        candidate = (raw if raw.is_absolute() else base / raw).resolve()
    except (OSError, ValueError, RuntimeError):
        return None
    if not candidate.exists():
        return None
    try:
        candidate.relative_to(base)
    except ValueError:
        return None
    return candidate


# --------------------------------------------------------------------------- #
# Bridge payload builders (pure-ish; reuse the Qt-free data layer)
# --------------------------------------------------------------------------- #
def _models(root: Path) -> dict[str, Any]:
    data = A.available_models(root)
    models = []
    for opt in data["models"]:
        models.append({**opt, "badge": model_badge(opt)})
    return {
        "models": models,
        "accounts": data.get("accounts", []),
        "connections": data.get("connections", []),
    }


def _status(root: Path, model_label: str, mode_label: str) -> dict[str, Any]:
    try:
        o = A.overview(root)
        ins = A.inspector_state(root, mode="safe-auto")
        spent = ins["budget"]["spent_today"]
        saved = o["savings"]["estimated_savings_usd"]
        on = bool(o.get("on"))
    except Exception:  # noqa: BLE001
        spent, saved, on = 0.0, 0.0, False
    return {
        "on": on,
        "line": header_status(model_label, mode_label, spent, saved=saved),
        "spent": spent,
        "saved": saved,
    }


def _workspace(root: Path) -> dict[str, Any]:
    try:
        ws = A.workspace_summary(root)
    except Exception:  # noqa: BLE001
        ws = {"name": root.name, "branch": "", "file_count": 0}
    return {
        "label": workspace_label(root),
        "name": ws["name"],
        "root": str(root),
        "branch": ws.get("branch", ""),
        "file_count": ws.get("file_count", 0),
        "recents": [
            {"path": p, "label": workspace_label(p)}
            for p in load_recent_workspaces()
            if p != str(root)
        ],
    }


def _inspector(root: Path, sel: dict[str, Any]) -> dict[str, Any]:
    model_label = (
        sel.get("model_advanced_label") or sel.get("model_label") or "Automatic routing"
    )
    model_kind = sel.get("model_kind") or "auto"
    run_mode = sel.get("mode") or "safe-auto"
    run_mode_label = sel.get("mode_label") or "Safe Auto"
    focus = sel.get("focus") or DEFAULT_TASK_MODE
    fmt = sel.get("format") or DEFAULT_OUTPUT_FORMAT
    try:
        ins = A.inspector_state(root, mode=run_mode)
    except Exception:  # noqa: BLE001
        ins = {}
    accounts = sel.get("accounts") or []
    connected = any(a.get("connected") for a in accounts)
    data = session_inspector(
        model_label=model_label,
        model_kind=model_kind,
        run_mode_label=run_mode_label,
        task_summary=task_summary(focus, fmt),
        inspector=ins,
        permission_summary=permission_summary(run_mode),
        connected=connected,
    )
    prefs = None
    try:
        from opaihub.gui_preferences import load_gui_preferences

        prefs = load_gui_preferences(root)
    except Exception:  # noqa: BLE001
        prefs = {}
    data["permissions"] = permissions_for(
        run_mode, safe_auto=(prefs or {}).get("safe_auto")
    )
    return data


def boot_payload(root: Path, *, initial_task: str | None = None) -> dict[str, Any]:
    """Everything the front-end needs to render the whole shell in one call."""
    from opaihub.gui_preferences import DEFAULT_MODE, MODES, load_gui_preferences

    root = root.expanduser().resolve()
    prefs = load_gui_preferences(root)
    mode = str(prefs.get("default_mode") or DEFAULT_MODE)
    focus = str(prefs.get("default_task_mode") or DEFAULT_TASK_MODE)
    fmt = str(prefs.get("default_output_format") or DEFAULT_OUTPUT_FORMAT)
    models = _models(root)
    mode_labels = {
        "ask": "Ask",
        "plan": "Plan",
        "safe-auto": "Safe Auto",
        "approve-edits": "Approve Edits",
        "full-auto": "Full Auto",
    }
    sel_model = next(
        (m for m in models["models"] if m["id"] == prefs.get("default_model")),
        models["models"][0]
        if models["models"]
        else {"id": "auto", "label": "OPai · Auto mode", "kind": "auto"},
    )
    sel = {
        "model_label": sel_model.get("label", "OPai · Auto mode"),
        "model_advanced_label": sel_model.get(
            "advanced_label", sel_model.get("label", "Automatic routing")
        ),
        "model_kind": sel_model.get("kind", "auto"),
        "mode": mode,
        "mode_label": mode_labels.get(mode, mode),
        "focus": focus,
        "format": fmt,
        "accounts": models["accounts"],
    }
    return {
        "workspace": _workspace(root),
        "models": models["models"],
        "selectedModel": sel_model.get("id", "auto"),
        "modes": [{"id": item, "label": mode_labels.get(item, item)} for item in MODES],
        "navGroups": [
            {"group": group, "items": items} for group, items in nav_groups()
        ],
        "taskModes": task_modes(),
        "outputFormats": output_formats(),
        "prefs": {
            "model": prefs.get("default_model", "auto"),
            "mode": mode,
            "focus": focus,
            "format": fmt,
            "showPanel": bool(prefs.get("show_control_panel", True)),
        },
        "accounts": models["accounts"],
        "connections": models["connections"],
        "status": _status(root, sel["model_label"], sel["mode_label"]),
        "inspector": _inspector(root, sel),
        "defaultView": DEFAULT_VIEW,
        "initialTask": initial_task or "",
        "recents": _recents(),
        "brand": _brand(),
        "tools": [
            {"id": tool["id"], "label": tool["label"], "desc": tool["desc"]}
            for tool in A.TOOLS
        ],
    }


def settings_payload(root: Path) -> dict[str, Any]:
    """Return the complete, secret-free Settings/Connections payload."""

    from opaihub.gui_preferences import load_gui_preferences

    prefs = load_gui_preferences(root)
    try:
        firewall = A.cost_firewall(root)
    except Exception:  # noqa: BLE001
        firewall = {}
    try:
        overview = A.overview(root)
    except Exception:  # noqa: BLE001
        overview = {}
    models = _models(root)
    return {
        "prefs": prefs,
        "firewall": {
            "profile": firewall.get("profile"),
            "panic": firewall.get("panic"),
            "spent_today": (firewall.get("spent") or {}).get("today_usd", 0),
            "cloud_gate": bool(firewall.get("require_confirmation_for_cloud")),
        },
        "permissions": permissions_for(
            str(prefs.get("default_mode") or "safe-auto"),
            safe_auto=prefs.get("safe_auto"),
        ),
        "accounts": models["accounts"],
        "connections": models["connections"],
        "about": {
            "version": overview.get("version"),
            "release_stage": overview.get("release_stage"),
        },
    }


def _recents() -> list[str]:
    from opai.gui_recents import load_recents

    return load_recents()


def _brand() -> dict[str, str]:
    from opai.brand import boot_brand

    return boot_brand()


def _run_gui(
    project_root: Path,
    *,
    initial_task: str | None = None,
):
    if not web_available():
        raise RuntimeError("QtWebEngine is not available")
    from PySide6 import QtCore, QtGui, QtWidgets
    from PySide6.QtWebChannel import QWebChannel
    from PySide6.QtWebEngineCore import QWebEngineSettings
    from PySide6.QtWebEngineWidgets import QWebEngineView

    QtCore.qInstallMessageHandler(lambda *_a: None)
    root = project_root.expanduser().resolve()
    from opaihub.gui_pipeline import handle_gui_message
    from opaihub.gui_preferences import save_gui_preferences

    class Worker(QtCore.QThread):
        done = QtCore.Signal(str)

        def __init__(self, fn) -> None:
            super().__init__()
            self._fn = fn

        def run(self) -> None:
            try:
                result = self._fn()
            except Exception as exc:  # noqa: BLE001
                result = {"status": "error", "answer": str(exc)}
            self.done.emit(json.dumps(result, default=str))

    class Bridge(QtCore.QObject):
        replyReady = QtCore.Signal(str)
        activity = QtCore.Signal(str)
        token = QtCore.Signal(str)
        toolReady = QtCore.Signal(str)
        workspaceChanged = QtCore.Signal(str)

        def __init__(self, window) -> None:
            super().__init__()
            self.window = window
            self.root = root
            self._workers: list[Any] = []
            self._cancels: dict[str, threading.Event] = {}

        # ---- synchronous data slots ---------------------------------- #
        @QtCore.Slot(result=str)
        def boot(self) -> str:
            return json.dumps(boot_payload(self.root, initial_task=initial_task))

        @QtCore.Slot(str, result=str)
        def inspector(self, sel_json: str) -> str:
            try:
                sel = json.loads(sel_json)
            except ValueError:
                sel = {}
            return json.dumps(_inspector(self.root, sel))

        @QtCore.Slot(str, result=str)
        def statusLine(self, sel_json: str) -> str:
            try:
                sel = json.loads(sel_json)
            except ValueError:
                sel = {}
            return json.dumps(
                _status(
                    self.root,
                    sel.get("model_label", "Auto"),
                    sel.get("mode_label", "Safe Auto"),
                )
            )

        @QtCore.Slot(str, result=str)
        def dashboard(self, section_id: str) -> str:
            try:
                vm = build_view_model(self.root)
                section = next(
                    (s for s in vm["sections"] if s.get("id") == section_id), None
                )
            except Exception as exc:  # noqa: BLE001
                return json.dumps({"error": str(exc)})
            return json.dumps(section or {"error": "not found"})

        @QtCore.Slot(str, str, result=str)
        def prompts(self, query: str, category: str) -> str:
            return json.dumps(
                {
                    "categories": categories_present(),
                    "prompts": filter_prompts(query, category or None),
                }
            )

        @QtCore.Slot(str, result=str)
        def usePrompt(self, prompt_id: str) -> str:
            return json.dumps(find_prompt(prompt_id) or {})

        @QtCore.Slot(result=str)
        def settingsData(self) -> str:
            return json.dumps(settings_payload(self.root))

        @QtCore.Slot(str, str)
        def savePref(self, key: str, value: str) -> None:
            allowed = {
                "default_model",
                "default_mode",
                "default_task_mode",
                "default_output_format",
                "show_control_panel",
            }
            if key not in allowed:
                return
            val: Any = value
            if value in ("true", "false"):
                val = value == "true"
            save_gui_preferences(self.root, {key: val})

        # ---- async slots --------------------------------------------- #
        @QtCore.Slot(str)
        def send(self, payload_json: str) -> None:
            try:
                payload = json.loads(payload_json)
            except ValueError:
                payload = {}
            text = str(payload.get("text", "")).strip()
            if not text:
                return
            request_id = str(payload.get("requestId") or uuid.uuid4().hex[:12])
            model_id = payload.get("model", "auto")
            mode = payload.get("mode", "safe-auto")
            composed = compose_prompt(
                text,
                task_mode_id=payload.get("focus"),
                output_format_id=payload.get("format"),
            )
            cancel = threading.Event()
            self._cancels[request_id] = cancel

            # Callbacks run on the worker thread; emitting a Bridge signal is a
            # safe cross-thread queued call. Every payload carries the request_id
            # so the front-end can drop anything from a stale/cancelled request.
            def emit_event(event: dict[str, Any]) -> None:
                self.activity.emit(
                    json.dumps({"requestId": request_id, "event": event})
                )

            def emit_text(chunk: str) -> None:
                self.token.emit(json.dumps({"requestId": request_id, "text": chunk}))

            def job() -> dict[str, Any]:
                return handle_gui_message(
                    self.root,
                    composed,
                    model_id=model_id,
                    mode=mode,
                    on_event=emit_event,
                    on_text=emit_text,
                    cancel=cancel,
                )

            worker = Worker(job)

            def _done(result_json: str) -> None:
                self._cancels.pop(request_id, None)
                self.replyReady.emit(
                    json.dumps(
                        {"requestId": request_id, "result": json.loads(result_json)}
                    )
                )

            worker.done.connect(_done)
            worker.finished.connect(
                lambda w=worker: self._workers.remove(w) if w in self._workers else None
            )
            self._workers.append(worker)
            worker.start()

        @QtCore.Slot(str)
        def cancel(self, request_id: str) -> None:
            """Stop the request: set its cancel flag so the runner kills the CLI.

            The front-end also drops the request_id immediately, so even if a
            late partial arrives it is ignored — no stale overwrite.
            """
            event = self._cancels.get(str(request_id))
            if event is not None:
                event.set()

        @QtCore.Slot(str)
        def runTool(self, name: str) -> None:
            def job():
                result = A.run_tool(self.root, name)
                if result.get("mutates") and result.get("apply"):
                    # Confirm on the GUI thread is awkward from a worker; for the
                    # web UI we surface the confirm text and apply on accept via a
                    # second call. Here we just return the prompt.
                    return {
                        "title": result.get("title"),
                        "text": result.get("confirm", result.get("text", "")),
                        "needs_confirm": True,
                        "apply": name,
                    }
                return {
                    "title": result.get("title", "Tool"),
                    "text": result.get("text", ""),
                    "needs_confirm": False,
                }

            worker = Worker(job)
            worker.done.connect(self.toolReady.emit)
            self._workers.append(worker)
            worker.start()

        @QtCore.Slot(str, result=str)
        def applyTool(self, name: str) -> str:
            result = A.run_tool(self.root, name)
            apply = result.get("apply")
            if apply:
                applied = A.apply_tool(self.root, apply)
                return json.dumps({"text": applied.get("text", "done")})
            return json.dumps({"text": "Nothing to apply."})

        @QtCore.Slot()
        def openWorkspace(self) -> None:
            # Bring the window forward first so the native picker is never hidden
            # behind it, and start one level up so sibling projects are one click
            # away. Runs on the GUI thread (it's a slot), so the modal dialog is
            # safe.
            self.window.raise_()
            self.window.activateWindow()
            parent = self.root.parent
            start = str(parent if parent.exists() else self.root)
            chosen = QtWidgets.QFileDialog.getExistingDirectory(
                self.window,
                "Open project folder",
                start,
                QtWidgets.QFileDialog.Option.ShowDirsOnly,
            )
            if chosen and is_valid_workspace(chosen):
                self._switch(chosen)

        @QtCore.Slot(str)
        def switchWorkspace(self, path: str) -> None:
            if is_valid_workspace(path):
                self._switch(path)

        @QtCore.Slot(str)
        def openPath(self, target: str) -> None:
            """Open a file or folder from this project in the OS file manager."""
            resolved = resolve_openable(self.root, target)
            if resolved is not None:
                QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(resolved)))

        @QtCore.Slot(result=str)
        def recents(self) -> str:
            return json.dumps(_recents())

        @QtCore.Slot(str)
        def saveRecent(self, text: str) -> None:
            from opai.gui_recents import add_recent

            add_recent(text)

        def _switch(self, path: str) -> None:
            self.root = Path(path).expanduser().resolve()
            add_recent_workspace(self.root)
            self.window.setWindowTitle(f"OPai · {self.root.name}")
            self.workspaceChanged.emit(json.dumps(boot_payload(self.root)))

        @QtCore.Slot(str)
        def openExternal(self, url: str) -> None:
            if str(url).startswith(("http://", "https://")):
                QtGui.QDesktopServices.openUrl(QtCore.QUrl(url))

    class Window(QtWidgets.QMainWindow):
        def __init__(self) -> None:
            super().__init__()
            self.setWindowTitle(f"OPai · {root.name}")
            self.setMinimumSize(1040, 700)
            self.resize(1340, 880)
            self.view = QWebEngineView(self)
            s = self.view.settings()
            s.setAttribute(
                QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True
            )
            s.setAttribute(
                QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True
            )
            s.setAttribute(
                QWebEngineSettings.WebAttribute.JavascriptCanAccessClipboard, True
            )
            s.setAttribute(QWebEngineSettings.WebAttribute.ShowScrollBars, False)
            self.setCentralWidget(self.view)
            self.bridge = Bridge(self)
            self.channel = QWebChannel()
            self.channel.registerObject("bridge", self.bridge)
            self.view.page().setWebChannel(self.channel)
            self.view.setHtml("")  # avoid white flash before load
            self.view.load(QtCore.QUrl.fromLocalFile(str(WEB_DIR / "index.html")))

    if QtWidgets.QApplication.instance() is None:
        # QtWebEngine needs a shared GL context set before the app is created,
        # and a crisp scale factor passed through on high-DPI displays.
        QtCore.QCoreApplication.setAttribute(
            QtCore.Qt.ApplicationAttribute.AA_ShareOpenGLContexts
        )
        QtGui.QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
            QtCore.Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Window()
    window.show()
    app.exec()
    return 0


def launch(project_root: Path, task: str | None = None) -> int:
    """Open the web-rendered desktop window (blocks until closed)."""
    return int(_run_gui(project_root, initial_task=task) or 0)
