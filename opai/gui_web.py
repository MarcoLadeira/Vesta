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
from opai.activity_batch import FLUSH_INTERVAL_MS, ActivityBatcher
from opai.gui_controls import header_status, model_badge, session_inspector
from opai.gui_lifecycle import drain_workers, signal_cancels
from opai.gui_modes import (
    DEFAULT_OUTPUT_FORMAT,
    DEFAULT_TASK_MODE,
    output_format,
    output_formats,
    task_modes,
    task_summary,
)
from opai.gui_nav import DEFAULT_VIEW, group_collapsed, nav_groups
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
def _models(root: Path, *, discover_local: bool = True) -> dict[str, Any]:
    data = A.available_models(root, discover_local=discover_local)
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
    from opaihub.repo_context import resolve_repo_context, save_active_repo

    context = resolve_repo_context(root)
    save_active_repo(root, context)
    try:
        ws = A.workspace_summary(context.path)
    except Exception:  # noqa: BLE001
        ws = {"name": root.name, "branch": "", "file_count": 0}
    return {
        "label": workspace_label(root),
        "name": ws["name"],
        "root": str(context.path),
        "branch": context.branch or ws.get("branch", ""),
        "remote": context.remote,
        "dirty": bool(context.dirty_paths),
        "dirty_paths": list(context.dirty_paths),
        "file_count": ws.get("file_count", 0),
        "recents": [
            {"path": p, "label": workspace_label(p)}
            for p in load_recent_workspaces()
            if p != str(root)
        ],
    }


def _inspector(root: Path, sel: dict[str, Any]) -> dict[str, Any]:
    from opaihub.workflow_state import load_workflow_state

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
    workflow = load_workflow_state(root)
    data.setdefault("rows", []).extend(
        [
            {"label": "Agent mode", "value": workflow.mode.title()},
            {"label": "Workflow", "value": workflow.phase.replace("_", " ").title()},
            {
                "label": "Tests",
                "value": workflow.tests_status.replace("_", " ").title(),
            },
            {
                "label": "PR / merge",
                "value": workflow.pr_url
                or workflow.merge_status.replace("_", " ").title(),
            },
        ]
    )
    if workflow.blocker:
        data["rows"].append({"label": "Blocker", "value": workflow.blocker})
    review_summary = workflow.diff_review.get("summary", {})
    if review_summary:
        data["rows"].append(
            {
                "label": "Diff review",
                "value": (
                    f"{review_summary.get('approved', 0)} approved · "
                    f"{review_summary.get('pending', 0)} pending · "
                    f"{review_summary.get('rejected', 0)} rejected"
                ),
            }
        )
    if workflow.next_actions:
        data["rows"].append({"label": "Next action", "value": workflow.next_actions[0]})
    return data


def boot_payload(root: Path, *, initial_task: str | None = None) -> dict[str, Any]:
    """Everything the front-end needs to render the whole shell in one call."""
    from opaihub.gui_preferences import MODES, load_gui_preferences
    from opaihub.workflow_state import load_workflow_state

    from opaihub.autonomy import resolve_startup_mode

    root = root.expanduser().resolve()
    prefs = load_gui_preferences(root)
    # Central autonomy decision (#137): boot into the effective mode, which is
    # Full Auto only when it is explicitly pinned.
    autonomy = resolve_startup_mode(prefs)
    mode = autonomy.effective_mode
    focus = str(prefs.get("default_task_mode") or DEFAULT_TASK_MODE)
    fmt = str(prefs.get("default_output_format") or DEFAULT_OUTPUT_FORMAT)
    models = _models(root, discover_local=False)
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
    workflow = load_workflow_state(root)
    return {
        "workspace": _workspace(root),
        "workflow": workflow.to_dict(),
        "models": models["models"],
        "selectedModel": sel_model.get("id", "auto"),
        "modes": [{"id": item, "label": mode_labels.get(item, item)} for item in MODES],
        "navGroups": [
            {"group": group, "items": items, "collapsed": group_collapsed(group)}
            for group, items in nav_groups()
        ],
        "taskModes": task_modes(),
        "outputFormats": output_formats(),
        "prefs": {
            "model": prefs.get("default_model", "auto"),
            "mode": mode,
            "focus": focus,
            "format": fmt,
            "showPanel": bool(prefs.get("show_control_panel", True)),
            # Full Auto pin state (#137) so the UI can show danger styling and
            # an unpin action, and never silently present unpinned Full Auto.
            "fullAutoPinned": autonomy.full_auto_pinned,
            # Free-model ids the user already consented to (asked once, never
            # again). Front-end skips the consent card for anything in this list.
            "freeConsent": list(prefs.get("free_consent") or []),
        },
        "autonomy": autonomy.to_dict(),
        "accounts": models["accounts"],
        "connections": models["connections"],
        "status": _status(root, sel["model_label"], sel["mode_label"]),
        "inspector": _inspector(root, sel),
        "defaultView": DEFAULT_VIEW,
        "initialTask": initial_task or "",
        "recents": _recents(root),
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
    models = _models(root, discover_local=False)
    from opaihub.accounts import codex_config_issue, provider_connection_doctor
    from opaihub.credentials import credential_statuses
    from opaihub.usage import build_usage_snapshots

    credentials = credential_statuses()
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
        "models": models["models"],
        "usage": build_usage_snapshots(
            root, models["models"], limits=prefs.get("usage_limits") or {}
        ),
        "credentials": credentials,
        "connectionDoctor": provider_connection_doctor(
            accounts=models["accounts"],
            connections=models["connections"],
            credentials=credentials,
            include_cli_versions=False,
            include_history=True,
        ),
        "codexConfig": codex_config_issue(),
        "about": {
            "version": overview.get("version"),
            "release_stage": overview.get("release_stage"),
        },
    }


def _recents(root: Path) -> list[str]:
    from opai.gui_recents import load_recents

    return load_recents(root)


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
    from opaihub.repo_context import active_repo_context

    root = active_repo_context(project_root).path
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
        activityBatch = QtCore.Signal(str)
        token = QtCore.Signal(str)
        toolReady = QtCore.Signal(str)
        workspaceChanged = QtCore.Signal(str)
        modelsChanged = QtCore.Signal(str)
        providerLoginReady = QtCore.Signal(str)
        connectionDoctorReady = QtCore.Signal(str)

        def __init__(self, window) -> None:
            super().__init__()
            self.window = window
            self.root = root
            self._workers: list[Any] = []
            self._cancels: dict[str, threading.Event] = {}

        def shutdown(self) -> dict[str, int]:
            """Stop everything before the window dies (#140).

            Signals every pending cancel (the runners kill their CLI children),
            then waits — bounded — for worker threads so Qt never destroys a
            live QThread. Stragglers stay referenced rather than destroyed.
            """
            signalled = signal_cancels(self._cancels)
            self._cancels.clear()
            stragglers = drain_workers(self._workers)
            self._workers = list(stragglers)
            return {"cancelled": signalled, "still_running": len(stragglers)}

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

        @QtCore.Slot(str, str, result=str)
        def reviewDiff(self, path: str, decision: str) -> str:
            from opaihub.diff_review import record_diff_decision

            return json.dumps(record_diff_decision(self.root, path, decision))

        @QtCore.Slot(result=str)
        def settingsData(self) -> str:
            return json.dumps(settings_payload(self.root))

        @QtCore.Slot(str, str, result=str)
        def saveProviderKey(self, provider: str, secret: str) -> str:
            from opaihub.credentials import CredentialStore

            try:
                return json.dumps(CredentialStore().set(provider, secret))
            except (ValueError, RuntimeError) as exc:
                return json.dumps(
                    {"provider": provider, "configured": False, "error": str(exc)}
                )

        @QtCore.Slot(str, result=str)
        def deleteProviderKey(self, provider: str) -> str:
            from opaihub.credentials import CredentialStore

            return json.dumps(CredentialStore().delete(provider))

        @QtCore.Slot(str, result=str)
        def testProvider(self, provider: str) -> str:
            from opaihub.provider_adapters import adapter_for

            try:
                return json.dumps(adapter_for(provider).probe(force=True))
            except (OSError, RuntimeError, ValueError) as exc:
                return json.dumps(
                    {"provider": provider, "connected": False, "error": str(exc)}
                )

        @QtCore.Slot(result=str)
        def refreshModels(self) -> str:
            return json.dumps(A.available_models(self.root, discover_local=True))

        @QtCore.Slot(str, str, str, str, result=str)
        def saveUsageLimit(
            self, model_id: str, metric: str, limit: str, window: str
        ) -> str:
            from opaihub.gui_preferences import save_usage_limit

            try:
                save_usage_limit(
                    self.root,
                    model_id,
                    metric=metric,
                    limit=int(limit),
                    window=window,
                )
                return json.dumps({"ok": True})
            except (TypeError, ValueError) as exc:
                return json.dumps({"ok": False, "error": str(exc)})

        @QtCore.Slot(result=str)
        def repairCodexConfig(self) -> str:
            from opaihub.accounts import repair_codex_config

            try:
                return json.dumps(repair_codex_config())
            except (OSError, ValueError) as exc:
                return json.dumps({"repaired": False, "error": str(exc)})

        @QtCore.Slot(str, result=str)
        def disconnectAccount(self, provider: str) -> str:
            """Sign out via the provider's own CLI (never touches credential files)."""
            from opaihub.accounts import disconnect_account

            try:
                return json.dumps(disconnect_account(provider))
            except Exception as exc:  # noqa: BLE001 - always report cleanly
                return json.dumps(
                    {"provider": provider, "disconnected": False, "message": str(exc)}
                )

        @QtCore.Slot(str, str)
        def startProviderLogin(self, provider: str, request_id: str) -> None:
            """Launch the explicit visible-terminal login flow off the UI thread."""
            from opaihub.accounts import interactive_provider_login

            cancel = threading.Event()
            self._cancels[request_id] = cancel
            worker = Worker(lambda: interactive_provider_login(provider, cancel=cancel))

            def _done(result_json: str) -> None:
                self._cancels.pop(request_id, None)
                self.providerLoginReady.emit(
                    json.dumps(
                        {
                            "requestId": request_id,
                            "provider": provider,
                            "result": json.loads(result_json),
                        }
                    )
                )

            worker.done.connect(_done)
            worker.finished.connect(
                lambda w=worker: self._workers.remove(w) if w in self._workers else None
            )
            self._workers.append(worker)
            worker.start()

        @QtCore.Slot(str)
        def refreshConnectionDoctor(self, request_id: str) -> None:
            """Collect local CLI versions without blocking Qt's UI thread."""
            from opaihub.accounts import provider_connection_doctor

            worker = Worker(provider_connection_doctor)

            def _done(result_json: str) -> None:
                result = json.loads(result_json)
                self.connectionDoctorReady.emit(
                    json.dumps(
                        {
                            "requestId": request_id,
                            "entries": result if isinstance(result, list) else [],
                        }
                    )
                )

            worker.done.connect(_done)
            worker.finished.connect(
                lambda w=worker: self._workers.remove(w) if w in self._workers else None
            )
            self._workers.append(worker)
            worker.start()

        @QtCore.Slot(str, result=str)
        def grantFreeConsent(self, model_id: str) -> str:
            """Remember consent for a free-tier model id (one-time confirmation)."""
            from opaihub.gui_preferences import grant_free_consent

            try:
                updated = grant_free_consent(self.root, model_id)
                return json.dumps({"ok": True, "freeConsent": updated["free_consent"]})
            except ValueError as exc:
                return json.dumps({"ok": False, "error": str(exc)})

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
            # Full Auto pin contract (#137): selecting Full Auto never persists a
            # bare full-auto default. It must go through the explicit pin slot,
            # so a plain savePref for it downgrades to Safe Auto.
            if key == "default_mode" and value == "full-auto":
                value = "safe-auto"
            val: Any = value
            if value in ("true", "false"):
                val = value == "true"
            save_gui_preferences(self.root, {key: val})

        @QtCore.Slot(result=str)
        def pinFullAuto(self) -> str:
            """Explicitly pin Full Auto with a recorded acknowledgement (#137)."""
            from opaihub.autonomy import resolve_startup_mode
            from opaihub.gui_preferences import pin_full_auto

            prefs = pin_full_auto(self.root)
            return json.dumps(resolve_startup_mode(prefs).to_dict())

        @QtCore.Slot(result=str)
        def unpinFullAuto(self) -> str:
            """Clear the Full Auto pin and fall back to Safe Auto (#137)."""
            from opaihub.autonomy import resolve_startup_mode
            from opaihub.gui_preferences import unpin_full_auto

            prefs = unpin_full_auto(self.root)
            return json.dumps(resolve_startup_mode(prefs).to_dict())

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
            cancel = threading.Event()
            self._cancels[request_id] = cancel

            # Activity batching (#226): worker threads append events to a
            # lock-guarded buffer; a GUI-thread QTimer drains it into ONE
            # `activityBatch` payload every ~33ms, so a burst of activity costs
            # one cross-thread signal instead of one per event. The legacy
            # per-event `activity` signal stays defined for the classic GUI
            # (#138) but the web path no longer floods it.
            batcher = ActivityBatcher(request_id)

            def flush_batch() -> None:
                payload = batcher.flush()
                if payload is not None:
                    self.activityBatch.emit(payload)

            timer = QtCore.QTimer(self)
            timer.setInterval(FLUSH_INTERVAL_MS)
            timer.timeout.connect(flush_batch)
            timer.start()

            # Callbacks run on the worker thread; appending is thread-safe and
            # the GUI-thread timer does the emitting. Every payload carries the
            # request_id so the front-end drops anything stale/cancelled.
            def emit_event(event: dict[str, Any]) -> None:
                batcher.append(event)

            def emit_text(chunk: str) -> None:
                self.token.emit(json.dumps({"requestId": request_id, "text": chunk}))

            def job() -> dict[str, Any]:
                return handle_gui_message(
                    self.root,
                    text,
                    model_id=model_id,
                    mode=mode,
                    focus_hint=payload.get("focus"),
                    output_instruction=output_format(payload.get("format")).get(
                        "instruction", ""
                    ),
                    on_event=emit_event,
                    on_text=emit_text,
                    cancel=cancel,
                    allow_cloud=bool(payload.get("allowCloud", False)),
                    allow_limit=bool(payload.get("allowLimit", False)),
                )

            worker = Worker(job)

            def _done(result_json: str) -> None:
                self._cancels.pop(request_id, None)
                # Deliver the tail before the reply so no event is lost or
                # arrives after the answer (honesty invariant).
                timer.stop()
                flush_batch()
                timer.deleteLater()
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

        @QtCore.Slot()
        def discoverModels(self) -> None:
            """Discover loopback models off the GUI thread and publish the catalog."""

            worker = Worker(lambda: A.available_models(self.root, discover_local=True))

            def _done(result_json: str) -> None:
                self.modelsChanged.emit(result_json)

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
            worker.finished.connect(
                lambda w=worker: self._workers.remove(w) if w in self._workers else None
            )
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
            return json.dumps(_recents(self.root))

        @QtCore.Slot(str)
        def saveRecent(self, text: str) -> None:
            from opai.gui_recents import add_recent

            add_recent(self.root, text)

        @QtCore.Slot(result=str)
        def clearRecents(self) -> str:
            from opai.gui_recents import clear_recents

            return json.dumps(clear_recents(self.root))

        def _switch(self, path: str) -> None:
            from opaihub.repo_context import active_repo_context

            self.root = active_repo_context(Path(path)).path
            add_recent_workspace(self.root)
            self.window.setWindowTitle(f"OPai · {self.root.name}")
            self.workspaceChanged.emit(json.dumps(boot_payload(self.root)))

        @QtCore.Slot(str)
        def openExternal(self, url: str) -> None:
            if str(url).startswith(("http://", "https://")):
                QtGui.QDesktopServices.openUrl(QtCore.QUrl(url))

        @QtCore.Slot(str)
        def copyText(self, text: str) -> None:
            """Write-only clipboard for copy buttons; the page never reads it."""
            QtGui.QGuiApplication.clipboard().setText(str(text or "")[:20000])

        # ---- native window chrome ----------------------------------- #
        @QtCore.Slot()
        def startWindowMove(self) -> None:
            """Hand title-bar dragging to the operating system."""
            handle = self.window.windowHandle()
            if handle is not None:
                handle.startSystemMove()

        @QtCore.Slot(str)
        def startWindowResize(self, edge_name: str) -> None:
            """Preserve native edge resizing for the frameless app window."""
            edge = {
                "top": QtCore.Qt.Edge.TopEdge,
                "right": QtCore.Qt.Edge.RightEdge,
                "bottom": QtCore.Qt.Edge.BottomEdge,
                "left": QtCore.Qt.Edge.LeftEdge,
                "top-right": QtCore.Qt.Edge.TopEdge | QtCore.Qt.Edge.RightEdge,
                "bottom-right": QtCore.Qt.Edge.BottomEdge | QtCore.Qt.Edge.RightEdge,
                "bottom-left": QtCore.Qt.Edge.BottomEdge | QtCore.Qt.Edge.LeftEdge,
                "top-left": QtCore.Qt.Edge.TopEdge | QtCore.Qt.Edge.LeftEdge,
            }.get(edge_name)
            handle = self.window.windowHandle()
            if edge is not None and handle is not None:
                handle.startSystemResize(edge)

        @QtCore.Slot()
        def minimizeWindow(self) -> None:
            self.window.showMinimized()

        @QtCore.Slot()
        def toggleMaximizeWindow(self) -> None:
            if self.window.isMaximized():
                self.window.showNormal()
            else:
                self.window.showMaximized()

        @QtCore.Slot()
        def closeWindow(self) -> None:
            self.window.close()

    class Window(QtWidgets.QMainWindow):
        def __init__(self) -> None:
            super().__init__()
            self.setWindowTitle(f"OPai · {root.name}")
            self.setWindowFlag(QtCore.Qt.WindowType.FramelessWindowHint, True)
            self.setMinimumSize(1040, 700)
            self.resize(1340, 880)
            self.view = QWebEngineView(self)
            s = self.view.settings()
            s.setAttribute(
                QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True
            )
            # Local-only render surface (#149): every byte of data arrives over
            # the QWebChannel bridge, so the page gets no network reach and no
            # clipboard permission. Copy buttons go through the write-only
            # bridge slot; external links go through openExternal.
            s.setAttribute(
                QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False
            )
            s.setAttribute(
                QWebEngineSettings.WebAttribute.JavascriptCanAccessClipboard, False
            )
            s.setAttribute(QWebEngineSettings.WebAttribute.ShowScrollBars, False)
            self.setCentralWidget(self.view)
            self.bridge = Bridge(self)
            self.channel = QWebChannel()
            self.channel.registerObject("bridge", self.bridge)
            self.view.page().setWebChannel(self.channel)
            self.view.setHtml("")  # avoid white flash before load
            self.view.load(QtCore.QUrl.fromLocalFile(str(WEB_DIR / "index.html")))

        def closeEvent(self, event) -> None:  # noqa: N802 - Qt override
            # Closing the window is how most users "stop" an AI app: cancel any
            # in-flight run (killing its CLI child) and drain workers before Qt
            # teardown, so nothing keeps spending after quit (#140).
            self.bridge.shutdown()
            super().closeEvent(event)

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
    # Window/taskbar/Alt-Tab icon + Windows taskbar grouping, set before show so
    # the app never presents as a generic Python window (#148).
    from opai.gui_identity import apply_window_identity

    apply_window_identity(app, window)
    window.show()
    app.exec()
    return 0


def launch(project_root: Path, task: str | None = None) -> int:
    """Open the web-rendered desktop window (blocks until closed)."""
    return int(_run_gui(project_root, initial_task=task) or 0)
