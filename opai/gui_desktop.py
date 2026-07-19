"""OPai desktop app — a premium, user-controlled AI workspace.

`opai gui` opens one window with four zones: a grouped left sidebar (new chat,
workspace views, dashboards, prompt library, settings), a header with a real
workspace switcher and live status, a stacked main area (chat + data-backed
dashboard pages + prompt library + settings), and a toggleable right control
panel (session inspector: model, run mode, task focus, output format, budget
meter, tool permissions, privacy). The chat stays the default surface; the
power lives in the palette, shortcuts, and the inspector — not in clutter.

All display/formatting logic lives in Qt-free helpers (``gui_controls``,
``gui_nav``, ``gui_modes``, ``gui_permissions``, ``gui_prompts``,
``gui_workspace``, ``gui_view_model``) so it is unit-tested without a display;
the widgets here just render those payloads. PySide6 is imported lazily so
``run_once`` and ``dependency_status`` stay dependency-free for tests and the
install smoke.
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
import os
import threading
from pathlib import Path
from typing import Any

from opai import app_state as A
from opai.gui_lifecycle import drain_workers
from opai.gui_controls import (
    SHORTCUTS,
    empty_state,
    filter_commands,
    header_status,
    live_agent_mode_row,
    model_badge,
    session_inspector,
    thinking_text,
    workflow_summary,
)
from opai.gui_modes import (
    DEFAULT_OUTPUT_FORMAT,
    DEFAULT_TASK_MODE,
    output_format,
    output_formats,
    plan_mode_selection,
    task_modes,
    task_summary,
)
from opai.gui_nav import DEFAULT_VIEW, find_nav, nav_groups
from opai.gui_permissions import permission_summary, permissions_for
from opai.gui_prompts import categories_present, filter_prompts, find_prompt
from opai.gui_view_model import SECTIONS, build_view_model
from opai.gui_workspace import (
    add_recent_workspace,
    is_valid_workspace,
    load_recent_workspaces,
    workspace_label,
)
from opai.message_render import render_message_html

INSTALL_HINT = (
    'Install desktop GUI support with: python -m pip install "opai[desktop-gui]" '
    '(from a source checkout: python -m pip install -e ".[desktop-gui]")'
)


def dependency_status() -> dict[str, Any]:
    available = importlib.util.find_spec("PySide6") is not None
    version = None
    if available:
        try:
            version = importlib.metadata.version("PySide6")
        except importlib.metadata.PackageNotFoundError:
            version = "unknown"
    return {
        "available": available,
        "package": "PySide6",
        "version": version,
        "install_hint": INSTALL_HINT,
    }


def run_once(project_root: Path) -> dict[str, Any]:
    """Headless smoke: build state + model list and return a summary. No Qt."""
    from opaihub.gui_pipeline import last_savings_receipt
    from opaihub.gui_preferences import MODES, load_gui_preferences

    root = project_root.expanduser().resolve()
    from opaihub.autonomy import resolve_startup_mode

    state = A.full_state(root)
    o = state["overview"]
    models = A.available_models(root)
    setup = models["setup"]
    # Central autonomy decision (#137): the classic GUI boots into the same
    # effective mode as every other surface - Full Auto only when pinned.
    autonomy = resolve_startup_mode(load_gui_preferences(root))
    mode = autonomy.effective_mode
    return {
        "ok": True,
        "on": o["on"],
        "status_label": o["status_label"],
        "version": o["version"],
        "project_root": o["project_root"],
        "clients": o["clients"]["summary"],
        "savings_usd": o["savings"]["estimated_savings_usd"],
        "routed_tasks": o["savings"]["routed_tasks"],
        "paid_calls_avoided": o["savings"]["cloud_calls_avoided"],
        "panic": o["budget"]["panic"],
        "models": [m["id"] for m in models["models"]],
        "accounts": [
            {"id": a["id"], "connected": a["connected"]} for a in models["accounts"]
        ],
        "account_count": models["account_count"],
        "model_status": {
            "available": [m["id"] for m in models["models"]],
            "local_count": models["local_count"],
        },
        "model_setup": {
            "status": setup["status"],
            "install_command": setup["install"]["command"],
            "recommended_model": setup["recommended"][0]["model"],
            "verify_command": setup["verify_command"],
        },
        "tools": [t["id"] for t in A.TOOLS],
        "zero_state": o["savings"]["zero_state"],
        "sections": [key for key, _ in SECTIONS],
        "mode": mode,
        "available_models": [m["id"] for m in models["models"]],
        "auto_policy": {
            "default_mode": mode,
            "modes": list(MODES),
            "full_auto_pinned": autonomy.full_auto_pinned,
        },
        "last_savings_receipt": last_savings_receipt(root),
        "capture": o["capture"],
        "capture_rate_percent": o["capture"]["rate_percent"],
    }


def _qt():
    if not dependency_status()["available"]:
        raise RuntimeError(INSTALL_HINT)
    from PySide6 import QtCore, QtGui, QtWidgets  # type: ignore[import-not-found]

    return QtCore, QtGui, QtWidgets


def build_chat_job(
    root: Path,
    message: str,
    *,
    model_id: str,
    mode: str,
    focus_hint: str | None = None,
    output_instruction: str | None = None,
):
    """One chat send as ``(job, cancel_event)`` — Qt-free so it is unit-tested.

    Stop must actually kill the provider CLI, not just hide its result (#141):
    the returned Event is threaded into ``handle_gui_message`` so the runner
    terminates the subprocess / closes the local HTTP connection when it fires.
    """
    cancel = threading.Event()

    def job() -> dict[str, Any]:
        from opaihub.gui_pipeline import handle_gui_message

        kwargs: dict[str, Any] = {
            "model_id": model_id,
            "mode": mode,
            "cancel": cancel,
        }
        if focus_hint is not None:
            kwargs["focus_hint"] = focus_hint
        if output_instruction is not None:
            kwargs["output_instruction"] = output_instruction
        return handle_gui_message(root, message, **kwargs)

    return job, cancel


# OPai's own dark identity: a cool charcoal with an emerald accent (the savings /
# cost-firewall signal) - deliberately not Claude's warm coral. One typeface only.
BG = "#1b1d21"  # main conversation surface (cool charcoal)
SIDEBAR = "#16181b"  # left sidebar + right control panel (a touch darker)
COMPOSER = "#23262b"  # composer + cards
PANEL = "#212429"  # message cards
PANEL_HI = "#2c3036"  # hover
USERBG = "#262b33"  # your messages (cool slate)
BORDER = "#2c3036"
BORDER_HI = "#3a414b"
INK = "#e8eaee"  # cool off-white
MUTED = "#9aa2af"
FAINT = "#69707d"
ACCENT = "#34d399"  # emerald - OPai primary action / brand
ACCENT_HI = "#28bd86"
GREEN = "#34d399"
AMBER = "#e0a458"
RED = "#ef6b7d"
CLAUDE = "#d6896a"  # provider dot - terracotta (recognisable, not the accent)
CODEX = "#58b0d6"  # provider dot - cool blue
COPILOT = "#a371f7"  # provider dot - GitHub Copilot violet

PROVIDER_COLOR = {
    "claude": CLAUDE,
    "codex": CODEX,
    "copilot": COPILOT,
    "auto": MUTED,
}
# Severity → colour for dashboard cards, KPI chips, badges, permission states.
SEVERITY_COLOR = {
    "success": GREEN,
    "warning": AMBER,
    "danger": RED,
    "accent": ACCENT,
    "info": "#7ab7ff",
    "neutral": MUTED,
    "safe": GREEN,
    "warn": AMBER,
    "allow": GREEN,
    "ask": AMBER,
    "block": RED,
}


def card_metric_rows(card: dict[str, Any]) -> list[tuple[str, str, str]]:
    """Normalize dashboard metrics for both Qt and Chromium renderers."""
    rows: list[tuple[str, str, str]] = []
    for metric in card.get("metrics") or []:
        if not isinstance(metric, dict):
            continue
        rows.append(
            (
                str(metric.get("label") or ""),
                str(metric.get("value") or ""),
                str(metric.get("severity") or "neutral"),
            )
        )
    return rows


# One crisp, premium typeface everywhere. Inter (the SaaS-standard neutral UI
# sans, SIL OFL) ships in opai/assets/fonts and is loaded at startup, so the app
# looks the same on every machine; the system fonts are only a fallback if
# loading fails. Inter is deliberately not a rounded face — it reads as a
# developer-grade workspace, not a toy.
FONT = '"Inter","Segoe UI Variable Text","Segoe UI",system-ui,sans-serif'
# Code and diffs still need a monospace face for alignment.
MONO_FONT = '"Cascadia Code","JetBrains Mono",Consolas,monospace'

# Palette handed to the Markdown renderer so answers match the dark theme.
MSG_COLORS = {
    "ink": INK,
    "muted": MUTED,
    "accent": ACCENT,
    "link": "#7cc0ff",
    "code_bg": "#15171b",
    "code_ink": INK,
    "border": BORDER,
}

# Run-mode labels come from the single autonomy source (#400); re-exported here
# so existing ``gui_desktop.MODE_LABELS`` references keep working.
from opaihub.autonomy import MODE_LABELS  # noqa: E402


def _stylesheet() -> str:
    return f"""
    QWidget {{ background:{BG}; color:{INK}; font-family:{FONT}; font-size:14px; }}
    QLabel {{ background:transparent; }}
    QToolTip {{ background:{PANEL_HI}; color:{INK}; border:1px solid {BORDER_HI};
        padding:6px 9px; border-radius:6px; }}

    #Sidebar {{ background:{SIDEBAR}; border-right:1px solid {BORDER}; }}
    #Brand {{ font-size:16px; font-weight:800; letter-spacing:0.2px; color:{INK}; }}
    #SectionLabel {{ color:{FAINT}; font-size:10.5px; font-weight:800; letter-spacing:0.9px; }}
    QPushButton#NewChat {{ background:{ACCENT}; color:#06281d; border:0;
        border-radius:11px; padding:11px 12px; text-align:left; font-weight:800; }}
    QPushButton#NewChat:hover {{ background:{ACCENT_HI}; }}
    QPushButton#NavItem {{ background:transparent; color:{MUTED}; border:0;
        border-radius:9px; padding:9px 12px; text-align:left; font-weight:600; }}
    QPushButton#NavItem:hover {{ background:{PANEL}; color:{INK}; }}
    QPushButton#NavItem[active="true"] {{ background:{PANEL_HI}; color:{INK};
        border-left:2px solid {ACCENT}; padding-left:10px; }}
    QPushButton#Recent {{ background:transparent; color:{MUTED}; border:0;
        border-radius:8px; padding:7px 12px; text-align:left; font-size:13px; }}
    QPushButton#Recent:hover {{ background:{PANEL}; color:{INK}; }}
    #Account {{ color:{MUTED}; font-size:12px; font-weight:600; }}

    #HeaderBar {{ background:{BG}; border-bottom:1px solid {BORDER}; }}
    #HeaderStat {{ color:{FAINT}; font-size:12px; }}
    QPushButton#WsSwitch {{ background:{PANEL}; color:{INK}; border:1px solid {BORDER};
        border-radius:9px; padding:6px 12px; font-weight:700; font-size:13px; text-align:left; }}
    QPushButton#WsSwitch:hover {{ background:{PANEL_HI}; border-color:{BORDER_HI}; }}
    QPushButton#WsSwitch::menu-indicator {{ image:none; width:0; }}

    QScrollArea {{ border:0; background:{BG}; }}
    QScrollBar:vertical {{ background:transparent; width:11px; margin:4px 2px; }}
    QScrollBar::handle:vertical {{ background:#46433f; border-radius:5px; min-height:48px; }}
    QScrollBar::handle:vertical:hover {{ background:#56524d; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height:0; }}

    #Hero {{ font-size:27px; font-weight:600; letter-spacing:-0.2px; color:{INK}; }}
    #HeroSub {{ color:{MUTED}; font-size:14px; }}
    #SuggestChip {{ background:{PANEL}; color:{INK}; border:1px solid {BORDER};
        border-radius:20px; padding:10px 16px; font-size:13px; }}
    #SuggestChip:hover {{ background:{PANEL_HI}; border-color:{BORDER_HI}; }}

    #UserBubble {{ background:{USERBG}; border:0; border-radius:14px; }}
    #BotBubble {{ background:transparent; border:0; }}
    #ToolBubble {{ background:{PANEL}; border:1px solid {BORDER}; border-radius:12px; }}
    #Role {{ font-weight:700; font-size:13px; }}
    #BubbleMeta {{ color:{FAINT}; font-size:11.5px; }}
    #Footer {{ color:{FAINT}; font-size:11px; }}
    #Mono {{ color:{MUTED}; font-size:12.5px; font-family:{MONO_FONT}; }}

    #Composer {{ background:{COMPOSER}; border:1px solid {BORDER}; border-radius:18px; }}
    #Composer:focus-within {{ border:1px solid {BORDER_HI}; }}
    QPlainTextEdit#Input {{ background:transparent; border:0; color:{INK};
        font-size:15px; padding:6px 4px; }}
    QLineEdit#Input {{ background:{COMPOSER}; border:1px solid {BORDER}; border-radius:10px;
        color:{INK}; font-size:14px; padding:9px 12px; }}
    QLineEdit#Input:focus {{ border-color:{BORDER_HI}; }}

    QComboBox#Pick {{ background:transparent; border:1px solid {BORDER}; border-radius:9px;
        padding:6px 12px; color:{MUTED}; font-weight:600; font-size:13px; }}
    QComboBox#Pick:hover {{ border-color:{BORDER_HI}; color:{INK}; }}
    QComboBox#Pick::drop-down {{ border:0; width:18px; }}
    QComboBox#Pick QAbstractItemView {{ background:{COMPOSER}; color:{INK};
        border:1px solid {BORDER_HI}; border-radius:10px; padding:6px;
        selection-background-color:{PANEL_HI}; outline:0; }}
    QPushButton#Ghost {{ background:transparent; color:{MUTED}; border:1px solid {BORDER};
        border-radius:9px; padding:6px 12px; font-weight:600; font-size:13px; }}
    QPushButton#Ghost:hover {{ color:{INK}; border-color:{BORDER_HI}; }}
    QPushButton#Send {{ background:{ACCENT}; color:#06281d; border:0; border-radius:10px;
        padding:9px 18px; font-weight:700; font-size:14px; }}
    QPushButton#Send:hover {{ background:{ACCENT_HI}; }}
    QPushButton#Send:disabled {{ background:{PANEL_HI}; color:{FAINT}; }}

    #ControlPanel {{ background:{SIDEBAR}; border-left:1px solid {BORDER}; }}
    #PanelTitle {{ font-size:13px; font-weight:800; letter-spacing:0.5px; color:{INK}; }}
    #PanelLabel {{ color:{FAINT}; font-size:10.5px; font-weight:800; letter-spacing:0.8px; }}
    #RowKey {{ color:{MUTED}; font-size:12px; }}
    #RowVal {{ color:{INK}; font-size:12px; font-weight:700; }}
    QProgressBar#Meter {{ background:{PANEL}; border:0; border-radius:5px; height:8px; text-align:center; }}
    QProgressBar#Meter::chunk {{ background:{ACCENT}; border-radius:5px; }}

    #PageTitle {{ font-size:23px; font-weight:700; letter-spacing:-0.2px; color:{INK}; }}
    #PageSub {{ color:{MUTED}; font-size:13.5px; }}
    #SettingHead {{ font-size:14px; font-weight:800; color:{INK}; }}
    #Card {{ background:{PANEL}; border:1px solid {BORDER}; border-radius:14px; }}
    #CardTitle {{ font-size:13px; font-weight:700; color:{INK}; }}
    #CardBody {{ color:{MUTED}; font-size:12.5px; }}
    #CardFoot {{ color:{FAINT}; font-size:11px; }}
    #Kpi {{ background:{PANEL}; border:1px solid {BORDER}; border-radius:12px; }}
    #KpiLabel {{ color:{FAINT}; font-size:10.5px; font-weight:700; letter-spacing:0.6px; }}
    #KpiValue {{ font-size:18px; font-weight:800; }}
    #HeroNum {{ font-size:34px; font-weight:800; letter-spacing:-0.5px; }}

    QMenu {{ background:{COMPOSER}; color:{INK}; border:1px solid {BORDER_HI};
        border-radius:10px; padding:6px; }}
    QMenu::item {{ padding:8px 18px; border-radius:7px; }}
    QMenu::item:selected {{ background:{PANEL_HI}; }}
    QMessageBox {{ background:{COMPOSER}; }}
    QFileDialog {{ background:{COMPOSER}; }}
    """


def _run_gui(
    project_root: Path,
    *,
    screenshot_path: Path | None = None,
    initial_task: str | None = None,
):
    """Build the workspace window; either run it (default) or render it to a PNG."""
    QtCore, QtGui, QtWidgets = _qt()
    # Keep Qt's internal warnings out of the launching terminal so nothing ever
    # appears to "print to the console" - it all renders in the UI.
    QtCore.qInstallMessageHandler(lambda *_a: None)
    from opaihub.repo_context import active_repo_context

    root = active_repo_context(project_root).path
    from opaihub.gui_preferences import (
        DEFAULT_MODE,
        MODES,
        load_gui_preferences,
        pin_full_auto,
        save_gui_preferences,
    )

    def _load_app_fonts() -> None:
        # Load the crisp typeface that ships with OPai so every machine renders
        # the same premium UI, regardless of what system fonts are installed.
        fonts_dir = Path(__file__).resolve().parent / "assets" / "fonts"
        for name in [
            "Inter-Variable.ttf",
            "Inter-Italic-Variable.ttf",
        ]:
            path = fonts_dir / name
            if path.exists():
                QtGui.QFontDatabase.addApplicationFont(str(path))

    class Worker(QtCore.QThread):
        done = QtCore.Signal(object)

        def __init__(self, fn, cancel_event=None) -> None:
            super().__init__()
            self._fn = fn
            self._cancelled = False
            self._cancel_event = cancel_event

        def cancel(self) -> None:
            # Suppress the late result AND kill the underlying work (#141):
            # setting the event makes the runner terminate its CLI subprocess,
            # so Stop stops the spend, not just the display.
            self._cancelled = True
            if self._cancel_event is not None:
                self._cancel_event.set()

        def run(self) -> None:  # noqa: D401 - QThread entry point
            try:
                result = self._fn()
            except Exception as exc:  # noqa: BLE001
                result = {"status": "error", "answer": str(exc)}
            if not self._cancelled:
                self.done.emit(result)

    class Composer(QtWidgets.QPlainTextEdit):
        submit = QtCore.Signal()

        def keyPressEvent(self, event) -> None:  # noqa: N802
            key = event.key()
            if key in (QtCore.Qt.Key.Key_Return, QtCore.Qt.Key.Key_Enter) and not (
                event.modifiers() & QtCore.Qt.KeyboardModifier.ShiftModifier
            ):
                self.submit.emit()
                return
            super().keyPressEvent(event)

    class ChatWindow(QtWidgets.QMainWindow):
        def __init__(self) -> None:
            super().__init__()
            self.root = root
            self._workers: list[Any] = []
            self._pending = None
            self._recents: list[str] = []
            self._current_worker: Any | None = None
            self._is_busy = False
            self._loading_models = False
            self._loading_mode = False
            self._accounts: list[dict[str, Any]] = []
            self._preferences = load_gui_preferences(self.root)
            self._task_mode_id = str(
                self._preferences.get("default_task_mode") or DEFAULT_TASK_MODE
            )
            self._format_id = str(
                self._preferences.get("default_output_format") or DEFAULT_OUTPUT_FORMAT
            )
            self._current_view = DEFAULT_VIEW
            self._nav_buttons: dict[str, Any] = {}
            self.setWindowTitle(f"OPai · {self.root.name}")
            self.setMinimumSize(1000, 680)
            self.resize(1320, 860)
            self.setStyleSheet(_stylesheet())

            central = QtWidgets.QWidget()
            row = QtWidgets.QHBoxLayout(central)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(0)
            self.sidebar = self._build_sidebar()
            row.addWidget(self.sidebar)

            center = QtWidgets.QWidget()
            center_l = QtWidgets.QVBoxLayout(center)
            center_l.setContentsMargins(0, 0, 0, 0)
            center_l.setSpacing(0)
            center_l.addWidget(self._build_header())

            self.stack = QtWidgets.QStackedWidget()
            self.chat_page = self._build_chat_page()
            self.prompts_page = self._build_prompts_page()
            self.dashboard_page = self._build_dashboard_page()
            self.settings_page = self._build_settings_page()
            for page in (
                self.chat_page,
                self.prompts_page,
                self.dashboard_page,
                self.settings_page,
            ):
                self.stack.addWidget(page)
            center_l.addWidget(self.stack, 1)
            row.addWidget(center, 1)

            self.control_panel = self._build_control_panel()
            row.addWidget(self.control_panel)
            self.setCentralWidget(central)

            self._empty = None
            self._load_models()
            self._refresh_status()
            self._show_empty()
            self._load_recents()
            self._install_shortcuts()
            self._refresh_inspector()
            self._highlight_nav(self._current_view)
            if not self._preferences.get("show_control_panel", True):
                self.control_panel.hide()

        # -- sidebar --------------------------------------------------------- #
        def _build_sidebar(self):
            bar = QtWidgets.QFrame()
            bar.setObjectName("Sidebar")
            bar.setFixedWidth(240)
            col = QtWidgets.QVBoxLayout(bar)
            col.setContentsMargins(14, 16, 14, 14)
            col.setSpacing(7)

            brand = QtWidgets.QHBoxLayout()
            self.dot = QtWidgets.QLabel("●")
            self.dot.setStyleSheet(f"color:{GREEN}; font-size:13px;")
            brand.addWidget(self.dot)
            brand.addWidget(self._lbl("OPai", name="Brand"))
            brand.addStretch(1)
            col.addLayout(brand)
            col.addSpacing(6)

            new_chat = QtWidgets.QPushButton("  +   New chat")
            new_chat.setObjectName("NewChat")
            new_chat.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
            new_chat.clicked.connect(self._start_new_chat)
            col.addWidget(new_chat)
            col.addSpacing(6)

            for group, items in nav_groups():
                col.addWidget(self._lbl(group.upper(), name="SectionLabel"))
                for item in items:
                    btn = QtWidgets.QPushButton(f"   {item['label']}")
                    btn.setObjectName("NavItem")
                    btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
                    btn.clicked.connect(
                        lambda _c=False, i=item["id"]: self._switch_view(i)
                    )
                    self._nav_buttons[item["id"]] = btn
                    col.addWidget(btn)
                col.addSpacing(6)

            col.addWidget(self._lbl("RECENTS", name="SectionLabel"))
            self.recents_box = QtWidgets.QVBoxLayout()
            self.recents_box.setSpacing(2)
            self.recents_hint = self._lbl("Your chats appear here.", name="Account")
            self.recents_box.addWidget(self.recents_hint)
            col.addLayout(self.recents_box)
            col.addStretch(1)

            self.account_lbl = self._lbl("", name="Account")
            col.addWidget(self.account_lbl)
            col.addWidget(self._lbl("Local only · no telemetry", name="Account"))
            return bar

        def _build_header(self):
            bar = QtWidgets.QFrame()
            bar.setObjectName("HeaderBar")
            row = QtWidgets.QHBoxLayout(bar)
            row.setContentsMargins(20, 10, 16, 10)
            row.setSpacing(10)
            self.ws_btn = QtWidgets.QPushButton(workspace_label(self.root))
            self.ws_btn.setObjectName("WsSwitch")
            self.ws_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
            self.ws_btn.setToolTip("Switch project folder")
            self.ws_btn.setMenu(self._workspace_menu())
            row.addWidget(self.ws_btn)
            row.addStretch(1)
            self.header_stat = self._lbl("", name="HeaderStat")
            row.addWidget(self.header_stat)
            self.panel_btn = QtWidgets.QPushButton("Inspector")
            self.panel_btn.setObjectName("Ghost")
            self.panel_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
            self.panel_btn.clicked.connect(self._toggle_control_panel)
            row.addWidget(self.panel_btn)
            return bar

        def _workspace_menu(self):
            menu = QtWidgets.QMenu(self)
            act = menu.addAction("Open folder…")
            act.triggered.connect(self._open_workspace)
            recents = [p for p in load_recent_workspaces() if p != str(self.root)]
            if recents:
                menu.addSeparator()
                for path in recents:
                    entry = menu.addAction(workspace_label(path))
                    entry.setToolTip(path)
                    entry.triggered.connect(
                        lambda _c=False, p=path: self._switch_workspace(p)
                    )
            return menu

        def _open_workspace(self) -> None:
            chosen = QtWidgets.QFileDialog.getExistingDirectory(
                self, "Open project folder", str(self.root)
            )
            if chosen:
                self._switch_workspace(chosen)

        def _switch_workspace(self, path: str) -> None:
            if not is_valid_workspace(path):
                self._toast("That folder is no longer available.")
                return
            from opaihub.repo_context import active_repo_context

            self.root = active_repo_context(Path(path)).path
            add_recent_workspace(self.root)
            self._preferences = load_gui_preferences(self.root)
            self.setWindowTitle(f"OPai · {self.root.name}")
            self.ws_btn.setText(workspace_label(self.root))
            self.ws_btn.setMenu(self._workspace_menu())
            self._load_models()
            self._refresh_status()
            self._refresh_inspector()
            self._start_new_chat()
            self._toast(f"Switched to {self.root.name}")

        # -- chat page (default view) ---------------------------------------- #
        def _build_chat_page(self):
            page = QtWidgets.QWidget()
            pl = QtWidgets.QVBoxLayout(page)
            pl.setContentsMargins(0, 0, 0, 0)
            pl.setSpacing(0)
            self.scroll = QtWidgets.QScrollArea()
            self.scroll.setWidgetResizable(True)
            self.scroll.setHorizontalScrollBarPolicy(
                QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff
            )
            self.thread_host = QtWidgets.QWidget()
            self.thread = QtWidgets.QVBoxLayout(self.thread_host)
            self.thread.setContentsMargins(0, 24, 0, 24)
            self.thread.setSpacing(18)
            self.scroll.setWidget(self.thread_host)
            pl.addWidget(self.scroll, 1)
            pl.addWidget(self._build_composer())
            return page

        def _build_composer(self):
            wrap = QtWidgets.QWidget()
            wl = QtWidgets.QVBoxLayout(wrap)
            wl.setContentsMargins(28, 6, 28, 20)
            box = QtWidgets.QFrame()
            box.setObjectName("Composer")
            bl = QtWidgets.QVBoxLayout(box)
            bl.setContentsMargins(16, 14, 14, 12)
            bl.setSpacing(10)
            self.input = Composer()
            self.input.setObjectName("Input")
            self.input.setPlaceholderText("Reply to OPai…")
            self.input.setFixedHeight(56)
            self.input.submit.connect(self._send)
            bl.addWidget(self.input)

            ctl = QtWidgets.QHBoxLayout()
            ctl.setSpacing(8)
            self.mode = QtWidgets.QComboBox()
            self.mode.setObjectName("Pick")
            for mode_id in MODES:
                self.mode.addItem(MODE_LABELS.get(mode_id, mode_id), mode_id)
            self.mode.currentIndexChanged.connect(self._on_mode_changed)
            ctl.addWidget(self.mode)
            self.tools_btn = QtWidgets.QPushButton("Tools")
            self.tools_btn.setObjectName("Ghost")
            self.tools_btn.setMenu(self._tools_menu())
            ctl.addWidget(self.tools_btn)
            ctl.addStretch(1)
            self.provider_dot = QtWidgets.QLabel("●")
            self.provider_dot.setStyleSheet(f"color:{MUTED}; font-size:12px;")
            ctl.addWidget(self.provider_dot)
            self.model = QtWidgets.QComboBox()
            self.model.setObjectName("Pick")
            self.model.setMinimumWidth(210)
            self.model.currentIndexChanged.connect(self._on_model_changed)
            ctl.addWidget(self.model)
            self.send_btn = QtWidgets.QPushButton("Send")
            self.send_btn.setObjectName("Send")
            self.send_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
            self.send_btn.clicked.connect(self._send)
            ctl.addWidget(self.send_btn)
            bl.addLayout(ctl)
            wl.addWidget(box)
            return wrap

        # -- right control panel / session inspector ------------------------- #
        def _build_control_panel(self):
            panel = QtWidgets.QFrame()
            panel.setObjectName("ControlPanel")
            panel.setFixedWidth(286)
            outer = QtWidgets.QVBoxLayout(panel)
            outer.setContentsMargins(16, 16, 16, 16)
            outer.setSpacing(9)
            outer.addWidget(self._lbl("SESSION", name="PanelTitle"))

            outer.addWidget(self._lbl("TASK FOCUS", name="PanelLabel"))
            self.focus_pick = QtWidgets.QComboBox()
            self.focus_pick.setObjectName("Pick")
            for mode in task_modes():
                self.focus_pick.addItem(mode["label"], mode["id"])
                self.focus_pick.setItemData(
                    self.focus_pick.count() - 1,
                    mode["desc"],
                    QtCore.Qt.ItemDataRole.ToolTipRole,
                )
            self._select_combo(self.focus_pick, self._task_mode_id)
            self.focus_pick.currentIndexChanged.connect(self._on_focus_changed)
            outer.addWidget(self.focus_pick)

            outer.addWidget(self._lbl("OUTPUT FORMAT", name="PanelLabel"))
            self.format_pick = QtWidgets.QComboBox()
            self.format_pick.setObjectName("Pick")
            for fmt in output_formats():
                self.format_pick.addItem(fmt["label"], fmt["id"])
            self._select_combo(self.format_pick, self._format_id)
            self.format_pick.currentIndexChanged.connect(self._on_format_changed)
            outer.addWidget(self.format_pick)

            self.inspector_host = QtWidgets.QWidget()
            self.inspector_box = QtWidgets.QVBoxLayout(self.inspector_host)
            self.inspector_box.setContentsMargins(0, 6, 0, 0)
            self.inspector_box.setSpacing(7)
            outer.addWidget(self.inspector_host)
            outer.addStretch(1)
            return panel

        def _refresh_inspector(self) -> None:
            if not hasattr(self, "inspector_box"):
                return
            self._clear_layout(self.inspector_box)
            opt = self._selected()
            run_mode = self._selected_mode()
            try:
                ins = A.inspector_state(self.root, mode=run_mode)
            except Exception:  # noqa: BLE001
                ins = {}
            connected = any(a.get("connected") for a in self._accounts)
            data = session_inspector(
                model_label=self.model.currentText(),
                model_kind=opt.get("kind"),
                run_mode_label=self.mode.currentText(),
                task_summary=task_summary(self._task_mode_id, self._format_id),
                inspector=ins,
                permission_summary=permission_summary(run_mode),
                connected=connected,
            )
            from opaihub.workflow_state import load_workflow_state

            workflow = load_workflow_state(self.root)
            data["rows"].extend(
                [
                    {"label": "Agent mode", "value": workflow.mode.title()},
                    # F21: live preview for the NEXT run, derived from the
                    # current run mode + focus (single source of truth, F20);
                    # the persisted row above is the last completed run.
                    live_agent_mode_row(run_mode, self._task_mode_id),
                    {
                        "label": "Workflow",
                        "value": workflow.phase.replace("_", " ").title(),
                    },
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
            for r in data["rows"]:
                self.inspector_box.addLayout(self._kv_row(r["label"], r["value"]))

            self.inspector_box.addWidget(self._lbl("BUDGET", name="PanelLabel"))
            meter = QtWidgets.QProgressBar()
            meter.setObjectName("Meter")
            meter.setTextVisible(False)
            meter.setRange(0, 100)
            meter.setValue(int(data["budget"]["pct"]))
            meter.setFixedHeight(8)
            self.inspector_box.addWidget(meter)
            self.inspector_box.addWidget(
                self._lbl(data["budget"]["text"], name="RowKey")
            )

            self.inspector_box.addWidget(self._lbl("PERMISSIONS", name="PanelLabel"))
            for row in permissions_for(
                run_mode, safe_auto=self._preferences.get("safe_auto")
            ):
                self.inspector_box.addLayout(self._perm_row(row))

            self.inspector_box.addWidget(self._lbl("PRIVACY", name="PanelLabel"))
            for badge in data["privacy"]:
                self.inspector_box.addWidget(self._badge(badge["label"], badge["tone"]))

        # -- dashboard pages (surfaced from gui_view_model) ------------------ #
        def _build_dashboard_page(self):
            page = QtWidgets.QScrollArea()
            page.setWidgetResizable(True)
            page.setHorizontalScrollBarPolicy(
                QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff
            )
            self.dashboard_host = QtWidgets.QWidget()
            self.dashboard_box = QtWidgets.QVBoxLayout(self.dashboard_host)
            self.dashboard_box.setContentsMargins(36, 28, 36, 28)
            self.dashboard_box.setSpacing(14)
            page.setWidget(self.dashboard_host)
            return page

        def _render_dashboard(self, section_id: str) -> None:
            self._clear_layout(self.dashboard_box)
            try:
                vm = build_view_model(self.root)
            except Exception as exc:  # noqa: BLE001
                self.dashboard_box.addWidget(
                    self._lbl(f"Couldn't load this view: {exc}", name="PageSub")
                )
                self.dashboard_box.addStretch(1)
                return
            section = next(
                (s for s in vm["sections"] if s.get("id") == section_id), None
            )
            if section is None:
                self.dashboard_box.addWidget(
                    self._lbl("View not found.", name="PageSub")
                )
                self.dashboard_box.addStretch(1)
                return
            self.dashboard_box.addWidget(
                self._lbl(section.get("title", section_id), name="PageTitle")
            )
            if section.get("subtitle"):
                self.dashboard_box.addWidget(
                    self._lbl(section["subtitle"], name="PageSub")
                )
            if section.get("hero"):
                self.dashboard_box.addWidget(self._hero_card(section["hero"]))
            if section.get("kpis"):
                self.dashboard_box.addLayout(self._kpi_grid(section["kpis"]))
            for card in section.get("cards", []):
                self.dashboard_box.addWidget(self._dash_card(card))
            if section.get("actions"):
                self.dashboard_box.addLayout(self._action_row(section["actions"]))
            self.dashboard_box.addStretch(1)

        def _hero_card(self, hero):
            frame = QtWidgets.QFrame()
            frame.setObjectName("Card")
            fl = QtWidgets.QVBoxLayout(frame)
            fl.setContentsMargins(20, 18, 20, 18)
            fl.setSpacing(4)
            num = self._lbl(str(hero.get("headline", "")))
            num.setObjectName("HeroNum")
            num.setStyleSheet(
                f"color:{SEVERITY_COLOR.get(hero.get('severity'), ACCENT)};"
            )
            fl.addWidget(num)
            if hero.get("caption"):
                fl.addWidget(self._lbl(hero["caption"], name="CardBody"))
            return frame

        def _kpi_grid(self, kpis):
            grid = QtWidgets.QGridLayout()
            grid.setSpacing(10)
            for i, k in enumerate(kpis):
                chip = QtWidgets.QFrame()
                chip.setObjectName("Kpi")
                cl = QtWidgets.QVBoxLayout(chip)
                cl.setContentsMargins(14, 12, 14, 12)
                cl.setSpacing(3)
                cl.addWidget(
                    self._lbl(str(k.get("label", "")).upper(), name="KpiLabel")
                )
                val = self._lbl(str(k.get("value", "")), name="KpiValue")
                val.setStyleSheet(
                    f"color:{SEVERITY_COLOR.get(k.get('severity'), INK)};"
                )
                cl.addWidget(val)
                grid.addWidget(chip, i // 3, i % 3)
            return grid

        def _dash_card(self, card):
            frame = QtWidgets.QFrame()
            frame.setObjectName("Card")
            fl = QtWidgets.QVBoxLayout(frame)
            fl.setContentsMargins(18, 14, 18, 14)
            fl.setSpacing(6)
            head = QtWidgets.QHBoxLayout()
            head.addWidget(self._lbl(str(card.get("title", "")), name="CardTitle"))
            head.addStretch(1)
            if card.get("status"):
                head.addWidget(
                    self._badge(str(card["status"]), card.get("severity", "neutral"))
                )
            fl.addLayout(head)
            if card.get("body"):
                fl.addWidget(self._lbl(str(card["body"]), name="CardBody"))
            metrics = card_metric_rows(card)
            if metrics:
                metric_row = QtWidgets.QHBoxLayout()
                metric_row.setSpacing(12)
                for label, value, severity in metrics:
                    metric_box = QtWidgets.QWidget()
                    metric_layout = QtWidgets.QVBoxLayout(metric_box)
                    metric_layout.setContentsMargins(0, 4, 0, 2)
                    metric_layout.setSpacing(2)
                    metric_layout.addWidget(self._lbl(label.upper(), name="KpiLabel"))
                    metric_value = self._lbl(value, name="CardBody")
                    metric_value.setStyleSheet(
                        f"color:{SEVERITY_COLOR.get(severity, MUTED)}; font-weight:700;"
                    )
                    metric_layout.addWidget(metric_value)
                    metric_row.addWidget(metric_box, 1)
                fl.addLayout(metric_row)
            for item in card.get("items", []):
                fl.addWidget(self._lbl("• " + str(item), name="CardBody"))
            if card.get("command"):
                cmd = self._lbl(str(card["command"]), name="Mono")
                cmd.setTextInteractionFlags(
                    QtCore.Qt.TextInteractionFlag.TextSelectableByMouse
                )
                fl.addWidget(cmd)
            if card.get("footnote"):
                fl.addWidget(self._lbl(str(card["footnote"]), name="CardFoot"))
            return frame

        def _action_row(self, actions):
            row = QtWidgets.QHBoxLayout()
            row.setSpacing(8)
            for act in actions:
                btn = QtWidgets.QPushButton(act.get("label", "Action"))
                btn.setObjectName("Ghost")
                btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
                btn.clicked.connect(lambda _c=False, a=act: self._run_action(a))
                row.addWidget(btn)
            row.addStretch(1)
            return row

        def _run_action(self, act) -> None:
            aid = act.get("id", "")
            if aid == "panic_toggle":
                self._switch_view("chat")
                self._run_tool("panic")
                return
            if aid == "safe_repair":
                self._switch_view("chat")
                self._run_tool("repair")
                return
            cmd = act.get("command")
            if cmd:
                QtWidgets.QApplication.clipboard().setText(str(cmd))
                self._toast(f"Copied: {cmd}")
            else:
                self._toast(
                    f"{act.get('label', 'Action')} — run it from your terminal."
                )

        # -- prompt library page --------------------------------------------- #
        def _build_prompts_page(self):
            page = QtWidgets.QWidget()
            pl = QtWidgets.QVBoxLayout(page)
            pl.setContentsMargins(36, 28, 36, 18)
            pl.setSpacing(12)
            pl.addWidget(self._lbl("Prompt Library", name="PageTitle"))
            pl.addWidget(
                self._lbl(
                    "Curated starting points. Pick one to load it into the composer.",
                    name="PageSub",
                )
            )
            controls = QtWidgets.QHBoxLayout()
            controls.setSpacing(8)
            self.prompt_search = QtWidgets.QLineEdit()
            self.prompt_search.setObjectName("Input")
            self.prompt_search.setPlaceholderText("Search prompts…")
            self.prompt_search.textChanged.connect(lambda _t: self._render_prompts())
            controls.addWidget(self.prompt_search, 1)
            self.prompt_cat = QtWidgets.QComboBox()
            self.prompt_cat.setObjectName("Pick")
            self.prompt_cat.addItem("All categories", None)
            for cat in categories_present():
                self.prompt_cat.addItem(cat, cat)
            self.prompt_cat.currentIndexChanged.connect(
                lambda _i: self._render_prompts()
            )
            controls.addWidget(self.prompt_cat)
            pl.addLayout(controls)

            scroll = QtWidgets.QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setHorizontalScrollBarPolicy(
                QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff
            )
            self.prompts_host = QtWidgets.QWidget()
            self.prompts_box = QtWidgets.QVBoxLayout(self.prompts_host)
            self.prompts_box.setContentsMargins(0, 4, 0, 4)
            self.prompts_box.setSpacing(10)
            scroll.setWidget(self.prompts_host)
            pl.addWidget(scroll, 1)
            self._render_prompts()
            return page

        def _render_prompts(self) -> None:
            if not hasattr(self, "prompts_box"):
                return
            self._clear_layout(self.prompts_box)
            query = self.prompt_search.text() if hasattr(self, "prompt_search") else ""
            cat = self.prompt_cat.currentData() if hasattr(self, "prompt_cat") else None
            results = filter_prompts(query, cat)
            if not results:
                self.prompts_box.addWidget(
                    self._lbl("No prompts match.", name="PageSub")
                )
                self.prompts_box.addStretch(1)
                return
            for prompt in results:
                self.prompts_box.addWidget(self._prompt_card(prompt))
            self.prompts_box.addStretch(1)

        def _prompt_card(self, prompt):
            frame = QtWidgets.QFrame()
            frame.setObjectName("Card")
            fl = QtWidgets.QVBoxLayout(frame)
            fl.setContentsMargins(18, 14, 18, 14)
            fl.setSpacing(6)
            head = QtWidgets.QHBoxLayout()
            head.addWidget(self._lbl(prompt["title"], name="CardTitle"))
            head.addStretch(1)
            head.addWidget(self._badge(prompt["category"], "info"))
            fl.addLayout(head)
            fl.addWidget(self._lbl(prompt["desc"], name="CardBody"))
            btnrow = QtWidgets.QHBoxLayout()
            btnrow.addStretch(1)
            use = QtWidgets.QPushButton("Use prompt")
            use.setObjectName("Ghost")
            use.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
            use.clicked.connect(
                lambda _c=False, pid=prompt["id"]: self._use_prompt(pid)
            )
            btnrow.addWidget(use)
            fl.addLayout(btnrow)
            return frame

        def _use_prompt(self, prompt_id: str) -> None:
            prompt = find_prompt(prompt_id)
            if not prompt:
                return
            self._select_combo(self.focus_pick, prompt.get("mode", "general"))
            self._switch_view("chat")
            self.input.setPlainText(prompt["template"])
            self.input.setFocus()

        # -- settings page --------------------------------------------------- #
        def _build_settings_page(self):
            page = QtWidgets.QScrollArea()
            page.setWidgetResizable(True)
            page.setHorizontalScrollBarPolicy(
                QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff
            )
            self.settings_host = QtWidgets.QWidget()
            self.settings_box = QtWidgets.QVBoxLayout(self.settings_host)
            self.settings_box.setContentsMargins(36, 28, 36, 28)
            self.settings_box.setSpacing(10)
            page.setWidget(self.settings_host)
            return page

        def _render_settings(self) -> None:
            box = self.settings_box
            self._clear_layout(box)
            box.addWidget(self._lbl("Settings", name="PageTitle"))
            box.addWidget(self._lbl(f"Project: {self.root}", name="PageSub"))

            box.addWidget(self._settings_head("Defaults"))
            summary = task_summary(self._task_mode_id, self._format_id)
            box.addLayout(
                self._kv_row(
                    "Default model", str(self._preferences.get("default_model", "auto"))
                )
            )
            box.addLayout(
                self._kv_row(
                    "Default run mode",
                    MODE_LABELS.get(
                        self._preferences.get("default_mode"),
                        str(self._preferences.get("default_mode")),
                    ),
                )
            )
            box.addLayout(self._kv_row("Task focus", summary["focus"]))
            box.addLayout(self._kv_row("Output format", summary["format"]))
            box.addWidget(
                self._lbl(
                    "Change model and run mode from the composer; they persist "
                    "automatically. Task focus and output format live in the Inspector.",
                    name="CardFoot",
                )
            )

            box.addWidget(self._settings_head("Cost firewall"))
            try:
                cf = A.cost_firewall(self.root)
            except Exception:  # noqa: BLE001
                cf = {}
            box.addLayout(self._kv_row("Profile", str(cf.get("profile", "—"))))
            box.addLayout(
                self._kv_row(
                    "Panic mode", "ON (local-only)" if cf.get("panic") else "off"
                )
            )
            box.addLayout(
                self._kv_row(
                    "Spent today",
                    f"${float((cf.get('spent') or {}).get('today_usd') or 0):.2f}",
                )
            )
            box.addLayout(
                self._kv_row(
                    "Cloud gate",
                    "confirm" if cf.get("require_confirmation_for_cloud") else "open",
                )
            )
            panic_row = QtWidgets.QHBoxLayout()
            panic_btn = QtWidgets.QPushButton(
                "Disable panic" if cf.get("panic") else "Enable panic"
            )
            panic_btn.setObjectName("Ghost")
            panic_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
            panic_btn.clicked.connect(self._settings_panic)
            panic_row.addWidget(panic_btn)
            panic_row.addStretch(1)
            box.addLayout(panic_row)

            box.addWidget(
                self._settings_head(f"Tool permissions · {self.mode.currentText()}")
            )
            for row in permissions_for(
                self._selected_mode(), safe_auto=self._preferences.get("safe_auto")
            ):
                box.addLayout(self._perm_row(row))

            box.addWidget(self._settings_head("Accounts"))
            for account in self._accounts:
                box.addLayout(
                    self._kv_row(
                        account.get("label", account.get("id", "?")),
                        "connected" if account.get("connected") else "not connected",
                    )
                )
            connect_btn = QtWidgets.QPushButton("Connect accounts")
            connect_btn.setObjectName("Ghost")
            connect_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
            connect_btn.clicked.connect(self._settings_connect)
            box.addWidget(connect_btn)

            box.addWidget(self._settings_head("Privacy"))
            for text in (
                "No telemetry — nothing leaves your machine.",
                "Raw build prompts are never logged; saved chat is redacted, "
                "kept per workspace on this machine, and can be cleared.",
                "Local-first routing; cloud only on confirmation.",
            ):
                box.addWidget(self._lbl("• " + text, name="CardBody"))

            try:
                o = A.overview(self.root)
            except Exception:  # noqa: BLE001
                o = None
            if o is not None:
                box.addWidget(self._settings_head("About"))
                box.addLayout(self._kv_row("Version", str(o.get("version", "—"))))
                box.addLayout(
                    self._kv_row("Release stage", str(o.get("release_stage", "—")))
                )
            box.addStretch(1)

        def _settings_panic(self) -> None:
            self._switch_view("chat")
            self._run_tool("panic")

        def _settings_connect(self) -> None:
            self._switch_view("chat")
            self._run_tool("connect")

        def _settings_head(self, text):
            lbl = self._lbl(text, name="SettingHead")
            return lbl

        # -- shared small builders ------------------------------------------- #
        def _kv_row(self, key, value):
            row = QtWidgets.QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.addWidget(self._lbl(str(key), name="RowKey"))
            row.addStretch(1)
            val = self._lbl(str(value), name="RowVal")
            val.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight)
            row.addWidget(val)
            return row

        def _perm_row(self, row):
            h = QtWidgets.QHBoxLayout()
            h.setContentsMargins(0, 0, 0, 0)
            color = SEVERITY_COLOR.get(row["state"], MUTED)
            h.addWidget(self._lbl(row["label"], name="RowKey"))
            h.addStretch(1)
            state = self._lbl(row["state"])
            state.setStyleSheet(f"color:{color}; font-size:11px; font-weight:700;")
            state.setToolTip(row.get("note", ""))
            h.addWidget(state)
            return h

        def _badge(self, text, tone):
            color = SEVERITY_COLOR.get(tone, MUTED)
            lbl = QtWidgets.QLabel(str(text))
            lbl.setStyleSheet(
                f"background:{PANEL}; color:{color}; border:1px solid {BORDER};"
                " border-radius:9px; padding:4px 9px; font-size:11px; font-weight:700;"
            )
            return lbl

        def _select_combo(self, combo, data_id) -> None:
            for i in range(combo.count()):
                if combo.itemData(i) == data_id:
                    combo.setCurrentIndex(i)
                    return

        def _clear_layout(self, layout) -> None:
            while layout.count():
                item = layout.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    widget.setParent(None)
                    widget.deleteLater()
                else:
                    child = item.layout()
                    if child is not None:
                        self._clear_layout(child)

        def _toast(self, text: str) -> None:
            self.statusBar().showMessage(str(text), 4000)

        # -- navigation ------------------------------------------------------ #
        def _switch_view(self, nav_id: str) -> None:
            item = find_nav(nav_id)
            if item is None:
                nav_id, item = "chat", find_nav("chat")
            self._current_view = nav_id
            self._highlight_nav(nav_id)
            if item and item.get("kind") == "dashboard":
                self._render_dashboard(item["section"])
                self.stack.setCurrentWidget(self.dashboard_page)
            elif nav_id == "prompts":
                self.stack.setCurrentWidget(self.prompts_page)
            elif nav_id == "settings":
                self._render_settings()
                self.stack.setCurrentWidget(self.settings_page)
            else:
                self.stack.setCurrentWidget(self.chat_page)

        def _highlight_nav(self, nav_id: str) -> None:
            for nid, btn in self._nav_buttons.items():
                btn.setProperty("active", "true" if nid == nav_id else "false")
                btn.style().unpolish(btn)
                btn.style().polish(btn)

        def _toggle_control_panel(self) -> None:
            visible = not self.control_panel.isVisible()
            self.control_panel.setVisible(visible)
            self._preferences = save_gui_preferences(
                self.root, {"show_control_panel": visible}
            )

        def _start_new_chat(self) -> None:
            self._switch_view("chat")
            self._new_chat()
            self.input.setFocus()

        # -- helpers --------------------------------------------------------- #
        def _lbl(self, text, *, name=None, color=None):
            label = QtWidgets.QLabel(str(text))
            if name:
                label.setObjectName(name)
            if color:
                label.setStyleSheet(f"color:{color};")
            label.setWordWrap(True)
            return label

        def _tools_menu(self):
            menu = QtWidgets.QMenu(self)
            for tool in A.TOOLS:
                act = menu.addAction(f"{tool['label']} — {tool['desc']}")
                act.triggered.connect(lambda _c=False, t=tool["id"]: self._run_tool(t))
            return menu

        def _load_models(self) -> None:
            data = A.available_models(self.root)
            default_model = str(self._preferences.get("default_model") or "auto")
            self.model.blockSignals(True)
            self._loading_models = True
            self.model.clear()
            selected = 0
            for option in data["models"]:
                self.model.addItem(option["label"], option)
                # Capability badge as a tooltip: speed/quality/cost clarity
                # without crowding the picker label.
                self.model.setItemData(
                    self.model.count() - 1,
                    model_badge(option),
                    QtCore.Qt.ItemDataRole.ToolTipRole,
                )
                if option["id"] == default_model:
                    selected = self.model.count() - 1
            self.model.blockSignals(False)
            self._loading_models = False
            self._accounts = data.get("accounts", [])
            if self.model.count():
                self.model.setCurrentIndex(selected)
                self._on_model_changed(selected)
            self._sync_mode_combo()

        def _sync_mode_combo(self) -> None:
            """Force the composer combo to display the EFFECTIVE mode (F16).

            A persisted full-auto default that is not pinned is downgraded to
            Safe Auto on load/save; the combo must never keep showing a
            silently-downgraded Full Auto (the root cause of F16). The
            effective mode comes from the same autonomy rule every surface
            uses, so the classic GUI can never disagree with the engine.
            """
            from opaihub.autonomy import resolve_startup_mode

            self._preferences = load_gui_preferences(self.root)
            effective = resolve_startup_mode(self._preferences).effective_mode
            mode_index = MODES.index(effective) if effective in MODES else 0
            self._loading_mode = True
            self.mode.setCurrentIndex(max(0, mode_index))
            self._loading_mode = False

        def _selected(self) -> dict[str, Any]:
            data = self.model.currentData()
            return data if isinstance(data, dict) else {"id": "auto", "kind": "auto"}

        def _selected_mode(self) -> str:
            return str(self.mode.currentData() or DEFAULT_MODE)

        def _on_model_changed(self, _index) -> None:
            opt = self._selected()
            provider = opt.get("provider") or opt.get("kind", "auto")
            self.provider_dot.setStyleSheet(
                f"color:{PROVIDER_COLOR.get(provider, MUTED)}; font-size:12px;"
            )
            if not self._loading_models:
                self._preferences = save_gui_preferences(
                    self.root, {"default_model": opt.get("id", "auto")}
                )
            if hasattr(self, "inspector_box"):
                self._refresh_inspector()
            if hasattr(self, "header_stat"):
                self._refresh_status()

        def _on_mode_changed(self, _index) -> None:
            if not self._loading_mode:
                mode = self._selected_mode()
                decision = plan_mode_selection(mode, self._preferences)
                if decision["needs_pin_confirmation"]:
                    # F16: Full Auto requires the same explicit pin
                    # acknowledgement the web GUI shows (#137). Persisting a
                    # bare full-auto default would be silently downgraded to
                    # Safe Auto while the combo kept displaying Full Auto.
                    if self._confirm_full_auto_pin():
                        self._preferences = pin_full_auto(self.root)
                else:
                    self._preferences = save_gui_preferences(
                        self.root, {"default_mode": decision["effective_mode"]}
                    )
                # After a declined pin or any sanitize-downgrade, force the
                # combo back to the EFFECTIVE mode — never a stale lie.
                self._sync_mode_combo()
            if hasattr(self, "inspector_box"):
                self._refresh_inspector()
            if hasattr(self, "header_stat"):
                self._refresh_status()

        def _confirm_full_auto_pin(self) -> bool:
            """The classic Pin-Full-Auto acknowledgement — same copy as web."""
            box = QtWidgets.QMessageBox(self)
            box.setIcon(QtWidgets.QMessageBox.Icon.Warning)
            box.setWindowTitle("Pin Full Auto?")
            box.setText("Pin Full Auto?")
            box.setInformativeText(
                "Full Auto lets OPai edit files and run commands without asking "
                "first. It stays on until you unpin it. Push, deploy, and "
                "destructive actions still ask for confirmation."
            )
            pin_btn = box.addButton(
                "Pin Full Auto", QtWidgets.QMessageBox.ButtonRole.AcceptRole
            )
            keep_btn = box.addButton(
                "Keep current mode", QtWidgets.QMessageBox.ButtonRole.RejectRole
            )
            box.setDefaultButton(keep_btn)
            box.exec()
            return box.clickedButton() is pin_btn

        def _on_focus_changed(self, _index) -> None:
            self._task_mode_id = str(self.focus_pick.currentData() or DEFAULT_TASK_MODE)
            self._preferences = save_gui_preferences(
                self.root, {"default_task_mode": self._task_mode_id}
            )
            self._refresh_inspector()

        def _on_format_changed(self, _index) -> None:
            self._format_id = str(
                self.format_pick.currentData() or DEFAULT_OUTPUT_FORMAT
            )
            self._preferences = save_gui_preferences(
                self.root, {"default_output_format": self._format_id}
            )
            self._refresh_inspector()

        def _refresh_status(self) -> None:
            try:
                o = A.overview(self.root)
                ws = A.workspace_summary(self.root)
                ins = A.inspector_state(self.root, mode=self._selected_mode())
            except Exception:  # noqa: BLE001
                return
            on = bool(o.get("on"))
            self.dot.setStyleSheet(f"color:{GREEN if on else AMBER}; font-size:13px;")
            branch = f" · {ws['branch']}" if ws["branch"] else ""
            if hasattr(self, "ws_btn"):
                self.ws_btn.setToolTip(
                    f"{ws['name']}{branch} · {ws['file_count']} files indexed"
                )
            sav = o["savings"]
            # One calm status strip: model · mode · spent today · saved. Gives
            # constant visibility of the AI's state without a control pane.
            self.header_stat.setText(
                header_status(
                    self.model.currentText(),
                    self.mode.currentText(),
                    ins["budget"]["spent_today"],
                    saved=sav["estimated_savings_usd"],
                )
            )
            connected = [a["label"] for a in self._accounts if a["connected"]]
            self.account_lbl.setText(
                "  ".join(f"● {n}" for n in connected) + "  connected"
                if connected
                else "No account connected"
            )
            self.account_lbl.setStyleSheet(
                f"color:{GREEN if connected else MUTED}; font-size:12px; font-weight:600;"
            )

        # -- recents (per-workspace, redacted; #145) -------------------------- #
        def _add_recent(self, text: str) -> None:
            from opai.gui_recents import add_recent

            self._recents = add_recent(self.root, text)[:8]
            self._render_recents()

        def _fill(self, text: str) -> None:
            self._switch_view("chat")
            self.input.setPlainText(text)
            self.input.setFocus()

        def _load_recents(self) -> None:
            from opai.gui_recents import load_recents

            self._recents = load_recents(self.root)[:8]
            self._render_recents()

        def _render_recents(self) -> None:
            if not self._recents:
                return
            self.recents_hint.hide()
            while self.recents_box.count() > 1:
                item = self.recents_box.takeAt(1)
                if item.widget():
                    item.widget().setParent(None)
            for entry in self._recents:
                label = entry if len(entry) <= 28 else entry[:27] + "…"
                btn = QtWidgets.QPushButton(label)
                btn.setObjectName("Recent")
                btn.setToolTip(entry)
                btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
                btn.clicked.connect(lambda _c=False, t=entry: self._fill(t))
                self.recents_box.addWidget(btn)

        def _new_chat(self) -> None:
            while self.thread.count():
                item = self.thread.takeAt(0)
                if item.widget():
                    item.widget().setParent(None)
            self._empty = None
            self._show_empty()

        # -- conversation ---------------------------------------------------- #
        def _show_empty(self) -> None:
            self._empty = QtWidgets.QWidget()
            self._empty.setSizePolicy(
                QtWidgets.QSizePolicy.Policy.Expanding,
                QtWidgets.QSizePolicy.Policy.Expanding,
            )
            el = QtWidgets.QVBoxLayout(self._empty)
            el.setContentsMargins(40, 0, 40, 0)
            el.setAlignment(QtCore.Qt.AlignmentFlag.AlignHCenter)
            el.addStretch(1)
            title = self._lbl("What do you want to build?")
            title.setObjectName("Hero")
            title.setAlignment(QtCore.Qt.AlignmentFlag.AlignHCenter)
            el.addWidget(title)
            connected = [a["label"] for a in self._accounts if a["connected"]]
            sub = self._lbl(
                f"{' and '.join(connected)} connected · OPai picks the cheapest safe path."
                if connected
                else "Connect your Claude or Codex account, then just type.",
                name="HeroSub",
            )
            sub.setAlignment(QtCore.Qt.AlignmentFlag.AlignHCenter)
            el.addWidget(sub)
            el.addSpacing(20)
            chips = QtWidgets.QHBoxLayout()
            chips.setSpacing(9)
            chips.setAlignment(QtCore.Qt.AlignmentFlag.AlignHCenter)
            for label, payload in (
                ("Summarize my changes", "Summarize my uncommitted changes"),
                ("Explain this repo", "Give me a high-level tour of this codebase"),
                ("Find a bug", "Look for a likely bug in my recent changes"),
            ):
                chip = QtWidgets.QPushButton(label)
                chip.setObjectName("SuggestChip")
                chip.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
                chip.clicked.connect(lambda _c=False, p=payload: self._chip(p))
                chips.addWidget(chip)
            el.addLayout(chips)
            el.addSpacing(14)
            hint = self._lbl(empty_state()["hint"], name="HeroSub")
            hint.setAlignment(QtCore.Qt.AlignmentFlag.AlignHCenter)
            el.addWidget(hint)
            el.addStretch(1)
            self.thread.addWidget(self._empty, 1)

        def _clear_empty(self) -> None:
            if self._empty is not None:
                self._empty.setParent(None)
                self._empty = None

        def _row(self, inner) -> None:
            holder = QtWidgets.QWidget()
            hl = QtWidgets.QHBoxLayout(holder)
            hl.setContentsMargins(40, 0, 40, 0)
            hl.addWidget(inner, 1)
            self.thread.addWidget(holder)
            QtCore.QTimer.singleShot(30, self._to_bottom)

        def _bubble(
            self, object_name, role, body, *, role_color=None, meta=None, mono=False
        ):
            self._clear_empty()
            frame = QtWidgets.QFrame()
            frame.setObjectName(object_name)
            fl = QtWidgets.QVBoxLayout(frame)
            if object_name == "BotBubble":
                fl.setContentsMargins(2, 2, 2, 2)
            else:
                fl.setContentsMargins(16, 13, 16, 14)
            fl.setSpacing(7)
            if role:
                r = self._lbl(role)
                r.setObjectName("Role")
                r.setStyleSheet(
                    f"color:{role_color or MUTED}; font-weight:700; font-size:13px;"
                )
                fl.addWidget(r)
            if mono:
                # Diffs and raw tool output stay verbatim monospace.
                body_lbl = self._lbl(body, name="Mono")
                body_lbl.setTextInteractionFlags(
                    QtCore.Qt.TextInteractionFlag.TextSelectableByMouse
                )
            else:
                # Answers are Markdown - render them as calm, themed rich text
                # (bold, headings, lists, links and code) instead of raw symbols.
                body_lbl = self._lbl("")
                body_lbl.setTextFormat(QtCore.Qt.TextFormat.RichText)
                body_lbl.setText(render_message_html(body, MSG_COLORS))
                body_lbl.setOpenExternalLinks(True)
                body_lbl.setTextInteractionFlags(
                    QtCore.Qt.TextInteractionFlag.TextSelectableByMouse
                    | QtCore.Qt.TextInteractionFlag.LinksAccessibleByMouse
                )
            fl.addWidget(body_lbl)
            if meta:
                fl.addWidget(self._lbl(meta, name="BubbleMeta"))
            return frame

        def _say_user(self, text) -> None:
            self._clear_empty()
            frame = QtWidgets.QFrame()
            frame.setObjectName("UserBubble")
            fl = QtWidgets.QVBoxLayout(frame)
            fl.setContentsMargins(16, 13, 16, 14)
            lbl = self._lbl(text)
            lbl.setTextInteractionFlags(
                QtCore.Qt.TextInteractionFlag.TextSelectableByMouse
            )
            fl.addWidget(lbl)
            holder = QtWidgets.QWidget()
            hl = QtWidgets.QHBoxLayout(holder)
            hl.setContentsMargins(40, 0, 40, 0)
            hl.addStretch(1)
            frame.setMaximumWidth(620)
            hl.addWidget(frame)
            self.thread.addWidget(holder)
            QtCore.QTimer.singleShot(30, self._to_bottom)

        def _say(self, object_name, role, body, **kw) -> None:
            self._row(self._bubble(object_name, role, body, **kw))

        def _footer(self, text) -> None:
            holder = QtWidgets.QWidget()
            hl = QtWidgets.QHBoxLayout(holder)
            hl.setContentsMargins(42, 0, 40, 0)
            hl.addWidget(self._lbl(text, name="Footer"))
            hl.addStretch(1)
            self.thread.addWidget(holder)
            QtCore.QTimer.singleShot(30, self._to_bottom)

        def _to_bottom(self) -> None:
            bar = self.scroll.verticalScrollBar()
            bar.setValue(bar.maximum())

        # -- actions --------------------------------------------------------- #
        def _chip(self, text) -> None:
            if text.startswith("/"):
                self._run_tool(text[1:])
            else:
                self.input.setPlainText(text)
                self._send()

        # -- command palette + keyboard shortcuts ---------------------------- #
        def _install_shortcuts(self) -> None:
            def sc(seq, fn):
                shortcut = QtGui.QShortcut(QtGui.QKeySequence(seq), self)
                shortcut.activated.connect(fn)
                return shortcut

            sc("Ctrl+K", self._open_palette)
            sc("Ctrl+N", self._start_new_chat)
            sc("Ctrl+L", lambda: (self._switch_view("chat"), self.input.setFocus()))
            sc("Ctrl+M", lambda: self.model.showPopup())
            sc("Ctrl+P", lambda: self._switch_view("prompts"))
            sc("Ctrl+I", self._toggle_control_panel)
            sc("Ctrl+O", self._open_workspace)
            sc("Ctrl+B", lambda: self.sidebar.setVisible(not self.sidebar.isVisible()))
            sc("Ctrl+/", self._show_shortcuts)
            sc("Esc", lambda: self._stop() if self._is_busy else None)

        def _open_palette(self) -> None:
            dlg = QtWidgets.QDialog(self)
            dlg.setWindowTitle("Commands")
            dlg.setModal(True)
            dlg.resize(440, 380)
            lay = QtWidgets.QVBoxLayout(dlg)
            lay.setContentsMargins(14, 14, 14, 14)
            lay.setSpacing(8)
            search = QtWidgets.QLineEdit()
            search.setObjectName("Input")
            search.setPlaceholderText("Type a command…")
            lay.addWidget(search)
            lst = QtWidgets.QListWidget()
            lay.addWidget(lst, 1)

            def populate(query: str = "") -> None:
                lst.clear()
                for cmd in filter_commands(query):
                    text = cmd["label"] + (f"    {cmd['hint']}" if cmd["hint"] else "")
                    item = QtWidgets.QListWidgetItem(text)
                    item.setData(QtCore.Qt.ItemDataRole.UserRole, cmd["id"])
                    lst.addItem(item)
                if lst.count():
                    lst.setCurrentRow(0)

            def run_current() -> None:
                item = lst.currentItem()
                if item is None:
                    return
                dlg.accept()
                self._run_command(item.data(QtCore.Qt.ItemDataRole.UserRole))

            search.textChanged.connect(populate)
            search.returnPressed.connect(run_current)
            lst.itemActivated.connect(lambda _it: run_current())
            populate("")
            search.setFocus()
            dlg.exec()

        def _run_command(self, command_id: str) -> None:
            if command_id == "new_chat":
                self._start_new_chat()
            elif command_id == "focus_input":
                self._switch_view("chat")
                self.input.setFocus()
            elif command_id == "stop":
                if self._is_busy:
                    self._stop()
            elif command_id == "change_model":
                self.model.showPopup()
            elif command_id == "change_mode":
                self.mode.showPopup()
            elif command_id == "prompts":
                self._switch_view("prompts")
            elif command_id == "inspector":
                self._toggle_control_panel()
            elif command_id == "workspace":
                self._open_workspace()
            elif command_id == "settings":
                self._switch_view("settings")
            elif command_id == "savings":
                self._switch_view("home")
            elif command_id == "doctor":
                self._switch_view("agents")
            elif command_id == "shortcuts":
                self._show_shortcuts()
            elif command_id == "connect":
                self._switch_view("chat")
                self._run_tool("connect")

        def _show_shortcuts(self) -> None:
            rows = "".join(
                f"<tr><td style='padding:3px 18px 3px 0;color:{ACCENT};"
                f"font-family:monospace'>{key}</td>"
                f"<td style='color:{INK}'>{desc}</td></tr>"
                for key, desc in SHORTCUTS
            )
            box = QtWidgets.QMessageBox(self)
            box.setWindowTitle("Keyboard shortcuts")
            box.setTextFormat(QtCore.Qt.TextFormat.RichText)
            box.setText(f"<table cellspacing='0'>{rows}</table>")
            box.exec()

        def _busy(self, on) -> None:
            self._is_busy = on
            if on:
                self.send_btn.setText("■ Stop")
                self.send_btn.setStyleSheet(
                    f"background:{RED}; color:#fff; border:0; border-radius:10px;"
                    " padding:9px 18px; font-weight:700; font-size:14px;"
                )
            else:
                self.send_btn.setText("Send")
                self.send_btn.setStyleSheet("")

        def _stop(self) -> None:
            if self._current_worker is not None:
                self._current_worker.cancel()
                self._current_worker = None
            self._busy(False)
            if self._pending is not None:
                parent = self._pending.parentWidget()
                (parent or self._pending).setParent(None)
                self._pending = None
            self._say("BotBubble", "OPai", "Stopped.", role_color=MUTED)

        def closeEvent(self, event) -> None:  # noqa: N802 - Qt override
            # Quitting must never leave a paid CLI running in the background,
            # and Qt must never destroy a live worker thread (#140): cancel
            # everything, then wait (bounded) before teardown.
            for worker in list(self._workers):
                worker.cancel()
            self._current_worker = None
            self._workers = drain_workers(self._workers)
            super().closeEvent(event)

        def _send(self) -> None:
            if self._is_busy:
                self._stop()
                return
            text = self.input.toPlainText().strip()
            if not text:
                return
            self.input.clear()
            if text.startswith("/"):
                parts = text[1:].split(None, 1)
                self._run_tool(parts[0], parts[1] if len(parts) > 1 else "")
                return
            self._add_recent(text)
            self._say_user(text)
            self._busy(True)
            opt = self._selected()
            model_id = opt.get("id", "auto")
            mode = self._selected_mode()
            if opt.get("kind") == "account":
                role = opt.get("label", "Account").split(" · ")[0]
                color = PROVIDER_COLOR.get(opt.get("provider"), ACCENT)
            else:
                role, color = "OPai", MUTED
            self._pending = self._bubble(
                "BotBubble",
                role,
                thinking_text(opt.get("label")),
                role_color=color,
                meta="thinking…",
            )
            self._row(self._pending)
            job, cancel_event = build_chat_job(
                self.root,
                text,
                model_id=model_id,
                mode=mode,
                focus_hint=self._task_mode_id,
                output_instruction=output_format(self._format_id).get(
                    "instruction", ""
                ),
            )
            worker = Worker(job, cancel_event=cancel_event)
            worker.done.connect(self._on_ask)
            worker.finished.connect(
                lambda w=worker: self._workers.remove(w) if w in self._workers else None
            )
            self._workers.append(worker)
            self._current_worker = worker
            worker.start()

        def _on_ask(self, result) -> None:
            self._current_worker = None
            self._busy(False)
            if self._pending is not None:
                parent = self._pending.parentWidget()
                (parent or self._pending).setParent(None)
                self._pending = None
            try:
                if "tool_trace" in result or "receipt" in result:
                    self._on_gui_result(result)
                else:
                    self._say(
                        "BotBubble",
                        "OPai",
                        str(result.get("answer") or result.get("error") or result),
                        role_color=MUTED,
                    )
            except Exception as exc:  # noqa: BLE001
                self._say("BotBubble", "OPai", f"Render error: {exc}", role_color=RED)
            self._refresh_status()
            self._refresh_inspector()

        def _on_gui_result(self, result) -> None:
            status = result.get("status", "error")
            receipt = result.get("receipt") or {}
            warnings = result.get("warnings") or []
            answer = result.get("answer") or ""
            role, color = "OPai", INK
            if status == "blocked":
                role, color = "Safe Auto", AMBER
            elif status == "account_timeout":
                role, color = "Timed out", AMBER
            elif status in {"needs_model", "needs_confirmation"}:
                role, color = "Action needed", AMBER
            elif status not in {"answered", "cache_hit"}:
                role, color = "OPai", RED
            if warnings:
                self._say(
                    "ToolBubble",
                    "Blocked",
                    "\n".join(w.get("reason", str(w)) for w in warnings),
                    role_color=RED,
                )
            # The answer is the main thing - shown clearly, on its own.
            self._say(
                "BotBubble",
                role,
                answer or "OPai didn't return a response for that one.",
                role_color=color,
            )
            # Show the real diff when files actually changed (important).
            changed = result.get("changed_files") or []
            if changed:
                try:
                    diff = A.workspace_diff(self.root)
                except Exception:  # noqa: BLE001
                    diff = ""
                self._say(
                    "ToolBubble",
                    f"Diff · {len(changed)} file(s) changed",
                    diff or "\n".join(changed),
                    role_color=ACCENT,
                    mono=True,
                )
            # One quiet footer instead of two repetitive cards every message.
            bits = []
            bits.extend(workflow_summary(result))
            if receipt.get("mode_label"):
                receipt_mode = str(receipt["mode_label"])
                if receipt_mode not in bits:
                    bits.append(receipt_mode)
            actual = float(receipt.get("estimated_actual_usd") or 0)
            saved = float(receipt.get("estimated_savings_usd") or 0)
            if actual:
                bits.append(f"${actual:.4f} this run")
            if saved:
                bits.append(f"${saved:.4f} saved")
            if receipt.get("paid_call_avoided"):
                bits.append("paid call avoided")
            if bits:
                self._footer("   ·   ".join(bits))

        def _run_tool(self, name, arg="") -> None:
            self._busy(True)
            worker = Worker(lambda: A.run_tool(self.root, name, arg))
            worker.done.connect(self._on_tool)
            worker.finished.connect(
                lambda w=worker: self._workers.remove(w) if w in self._workers else None
            )
            self._workers.append(worker)
            worker.start()

        def _on_tool(self, result) -> None:
            self._busy(False)
            if result.get("mutates") and result.get("apply"):
                ok = QtWidgets.QMessageBox.question(
                    self, "Confirm", result.get("confirm", "Proceed?")
                )
                if ok == QtWidgets.QMessageBox.StandardButton.Yes:
                    applied = A.apply_tool(self.root, result["apply"])
                    self._say(
                        "ToolBubble",
                        result.get("title"),
                        applied.get("text", "done"),
                        role_color=MUTED,
                    )
                else:
                    self._say(
                        "ToolBubble",
                        result.get("title"),
                        "Cancelled.",
                        role_color=MUTED,
                    )
            else:
                self._say(
                    "ToolBubble",
                    result.get("title", "Tool"),
                    result.get("text", ""),
                    role_color=MUTED,
                    mono=True,
                )
            self._refresh_status()
            self._refresh_inspector()

    # Crisp on high-DPI displays: pass the real scale factor through (no blurry
    # integer rounding) so text renders sharp instead of upscaled. Must be set
    # before the QApplication exists.
    if QtWidgets.QApplication.instance() is None:
        QtGui.QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
            QtCore.Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    _load_app_fonts()
    # Inter at a true antialiased weight — not the hinting-mangled default.
    base_font = QtGui.QFont("Inter", 10)
    base_font.setStyleStrategy(QtGui.QFont.StyleStrategy.PreferAntialias)
    app.setFont(base_font)
    window = ChatWindow()
    # Window/taskbar/Alt-Tab icon + Windows taskbar grouping (#148).
    from opai.gui_identity import apply_window_identity

    apply_window_identity(app, window)
    if initial_task:
        # CLI companion: `opai gui "fix the login bug"` opens pre-loaded.
        window.input.setPlainText(initial_task)
        window.input.setFocus()
    if screenshot_path is not None:
        window.resize(screenshot_path[1], screenshot_path[2])
        window.show()
        # Optional 4th element: a nav view id to display before grabbing, so the
        # headless smoke can render any page (chat/dashboards/prompts/settings).
        if len(screenshot_path) > 3 and screenshot_path[3]:
            window._switch_view(str(screenshot_path[3]))
        app.processEvents()
        app.processEvents()
        pixmap = window.grab()
        out = Path(screenshot_path[0])
        out.parent.mkdir(parents=True, exist_ok=True)
        pixmap.save(str(out), "PNG")
        window.close()
        size = out.stat().st_size if out.exists() else 0
        return {
            "status": "written" if size else "failed",
            "path": str(out),
            "bytes": size,
            "width": pixmap.width(),
            "height": pixmap.height(),
        }
    window.show()
    app.exec()
    return 0


def launch(project_root: Path, task: str | None = None) -> int:
    """Open the native desktop chat window (blocks until closed)."""
    return int(_run_gui(project_root, initial_task=task) or 0)


def render_screenshot(
    project_root: Path,
    out_path: Path,
    *,
    width: int = 1040,
    height: int = 720,
    view: str | None = None,
) -> dict[str, Any]:
    """Render a window view to a PNG offscreen - headless GUI smoke test.

    ``view`` is an optional nav id (e.g. ``home``, ``settings``, ``prompts``);
    when given, that page is shown before the grab. ``None`` renders the default
    chat view.
    """
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return _run_gui(
        project_root,
        screenshot_path=(str(out_path), int(width), int(height), view),
    )
