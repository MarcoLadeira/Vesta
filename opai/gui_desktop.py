"""OPai desktop app - a calm, Claude-style coding chat.

`opai gui` opens one clean window: a left sidebar (new chat + recents +
connected accounts), an uncluttered conversation, and a simple composer where
you pick a model and a run mode and type. OPai's control plane (modes, intent
router, savings receipts, account routing) runs underneath; the surface stays
simple on purpose.

PySide6 is imported lazily only when the window opens, so `run_once` and
`dependency_status` stay dependency-free for tests and the install smoke.
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
from typing import Any

from opai import app_state as A
from opai.gui_view_model import SECTIONS
from opai.message_render import render_message_html

INSTALL_HINT = (
    'Install desktop GUI support with: python -m pip install -e ".[desktop-gui]"'
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
    from opaihub.gui_preferences import DEFAULT_MODE, MODES, load_gui_preferences

    root = project_root.expanduser().resolve()
    state = A.full_state(root)
    o = state["overview"]
    models = A.available_models(root)
    setup = models["setup"]
    mode = load_gui_preferences(root).get("default_mode") or DEFAULT_MODE
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
        "auto_policy": {"default_mode": mode, "modes": list(MODES)},
        "last_savings_receipt": last_savings_receipt(root),
    }


def _qt():
    if not dependency_status()["available"]:
        raise RuntimeError(INSTALL_HINT)
    from PySide6 import QtCore, QtGui, QtWidgets  # type: ignore[import-not-found]

    return QtCore, QtGui, QtWidgets


# OPai's own dark identity: a cool charcoal with an emerald accent (the savings /
# cost-firewall signal) - deliberately not Claude's warm coral. One typeface only.
BG = "#1b1d21"  # main conversation surface (cool charcoal)
SIDEBAR = "#16181b"  # left sidebar (a touch darker)
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

PROVIDER_COLOR = {"claude": CLAUDE, "codex": CODEX, "auto": MUTED}
# One soft, friendly typeface everywhere. Nunito (rounded humanist sans, SIL
# OFL) ships in opai/assets/fonts and is loaded at startup, so the app looks the
# same on every machine; the system fonts are only a fallback if loading fails.
FONT = '"Nunito","Segoe UI Variable","Segoe UI",system-ui,sans-serif'
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

MODE_LABELS = {
    "ask": "Ask",
    "plan": "Plan",
    "safe-auto": "Safe Auto",
    "approve-edits": "Approve Edits",
    "full-auto": "Full Auto",
}


def _stylesheet() -> str:
    return f"""
    QWidget {{ background:{BG}; color:{INK}; font-family:{FONT}; font-size:14px; }}
    QLabel {{ background:transparent; }}
    QToolTip {{ background:{PANEL_HI}; color:{INK}; border:1px solid {BORDER_HI};
        padding:6px 9px; border-radius:6px; }}

    #Sidebar {{ background:{SIDEBAR}; border-right:1px solid {BORDER}; }}
    #Brand {{ font-size:16px; font-weight:700; letter-spacing:0.2px; color:{INK}; }}
    #SectionLabel {{ color:{FAINT}; font-size:11px; font-weight:700; letter-spacing:0.7px; }}
    QPushButton#NewChat {{ background:{PANEL}; color:{INK}; border:1px solid {BORDER};
        border-radius:11px; padding:10px 12px; text-align:left; font-weight:600; }}
    QPushButton#NewChat:hover {{ background:{PANEL_HI}; border-color:{BORDER_HI}; }}
    QPushButton#NavItem {{ background:transparent; color:{MUTED}; border:0;
        border-radius:9px; padding:9px 12px; text-align:left; font-weight:600; }}
    QPushButton#NavItem:hover {{ background:{PANEL}; color:{INK}; }}
    QPushButton#Recent {{ background:transparent; color:{MUTED}; border:0;
        border-radius:8px; padding:7px 12px; text-align:left; font-size:13px; }}
    QPushButton#Recent:hover {{ background:{PANEL}; color:{INK}; }}
    #Account {{ color:{MUTED}; font-size:12px; font-weight:600; }}

    #HeaderBar {{ background:{BG}; border-bottom:1px solid {BORDER}; }}
    #HeaderWS {{ color:{MUTED}; font-size:12.5px; font-weight:600; }}
    #HeaderStat {{ color:{FAINT}; font-size:12px; }}

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

    QMenu {{ background:{COMPOSER}; color:{INK}; border:1px solid {BORDER_HI};
        border-radius:10px; padding:6px; }}
    QMenu::item {{ padding:8px 18px; border-radius:7px; }}
    QMenu::item:selected {{ background:{PANEL_HI}; }}
    QMessageBox {{ background:{COMPOSER}; }}
    """


def _run_gui(
    project_root: Path,
    *,
    screenshot_path: Path | None = None,
    initial_task: str | None = None,
):
    """Build the chat window; either run it (default) or render it to a PNG."""
    QtCore, QtGui, QtWidgets = _qt()
    # Keep Qt's internal warnings out of the launching terminal so nothing ever
    # appears to "print to the console" - it all renders in the UI.
    QtCore.qInstallMessageHandler(lambda *_a: None)
    root = project_root.expanduser().resolve()
    from opaihub.gui_pipeline import handle_gui_message
    from opaihub.gui_preferences import (
        DEFAULT_MODE,
        MODES,
        load_gui_preferences,
        save_gui_preferences,
    )

    def _load_app_fonts() -> None:
        # Load the soft typeface that ships with OPai so every machine renders
        # the same calm UI, regardless of what system fonts are installed.
        fonts_dir = Path(__file__).resolve().parent / "assets" / "fonts"
        for name in [
            "Nunito-Variable.ttf",
            "Nunito-Italic-Variable.ttf",
        ]:
            path = fonts_dir / name
            if path.exists():
                QtGui.QFontDatabase.addApplicationFont(str(path))

    class Worker(QtCore.QThread):
        done = QtCore.Signal(object)

        def __init__(self, fn) -> None:
            super().__init__()
            self._fn = fn
            self._cancelled = False

        def cancel(self) -> None:
            self._cancelled = True

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
            self._preferences = load_gui_preferences(self.root)
            self.setWindowTitle("OPai")
            self.setMinimumSize(900, 640)
            self.resize(1160, 780)
            self.setStyleSheet(_stylesheet())

            central = QtWidgets.QWidget()
            row = QtWidgets.QHBoxLayout(central)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(0)
            row.addWidget(self._build_sidebar())

            main = QtWidgets.QWidget()
            main_l = QtWidgets.QVBoxLayout(main)
            main_l.setContentsMargins(0, 0, 0, 0)
            main_l.setSpacing(0)
            main_l.addWidget(self._build_header())

            self.scroll = QtWidgets.QScrollArea()
            self.scroll.setWidgetResizable(True)
            self.scroll.setHorizontalScrollBarPolicy(
                QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff
            )
            self.thread_host = QtWidgets.QWidget()
            self.thread = QtWidgets.QVBoxLayout(self.thread_host)
            self.thread.setContentsMargins(0, 26, 0, 26)
            self.thread.setSpacing(20)
            self.scroll.setWidget(self.thread_host)
            main_l.addWidget(self.scroll, 1)
            main_l.addWidget(self._build_composer())
            row.addWidget(main, 1)
            self.setCentralWidget(central)

            self._empty = None
            self._load_models()
            self._refresh_status()
            self._show_empty()
            self._load_recents()

        # -- sidebar --------------------------------------------------------- #
        def _build_sidebar(self):
            bar = QtWidgets.QFrame()
            bar.setObjectName("Sidebar")
            bar.setFixedWidth(252)
            col = QtWidgets.QVBoxLayout(bar)
            col.setContentsMargins(16, 18, 16, 16)
            col.setSpacing(10)

            brand = QtWidgets.QHBoxLayout()
            self.dot = QtWidgets.QLabel("●")
            self.dot.setStyleSheet(f"color:{GREEN}; font-size:13px;")
            brand.addWidget(self.dot)
            brand.addWidget(self._lbl("OPai", name="Brand"))
            brand.addStretch(1)
            col.addLayout(brand)

            new_chat = QtWidgets.QPushButton("  +   New chat")
            new_chat.setObjectName("NewChat")
            new_chat.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
            new_chat.clicked.connect(self._new_chat)
            col.addWidget(new_chat)

            for label, tool in (
                ("Connect accounts", "connect"),
                ("Settings", "budget"),
            ):
                btn = QtWidgets.QPushButton(label)
                btn.setObjectName("NavItem")
                btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
                btn.clicked.connect(lambda _c=False, t=tool: self._run_tool(t))
                col.addWidget(btn)

            col.addSpacing(8)
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
            row.setContentsMargins(28, 12, 24, 12)
            folder = QtWidgets.QLabel("\U0001f4c1")
            folder.setStyleSheet(f"color:{FAINT}; font-size:13px;")
            row.addWidget(folder)
            self.workspace_lbl = self._lbl(root.name, name="HeaderWS")
            row.addWidget(self.workspace_lbl)
            row.addStretch(1)
            self.header_stat = self._lbl("", name="HeaderStat")
            row.addWidget(self.header_stat)
            return bar

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
            default_mode = str(self._preferences.get("default_mode") or DEFAULT_MODE)
            self.model.blockSignals(True)
            self._loading_models = True
            self.model.clear()
            selected = 0
            for option in data["models"]:
                self.model.addItem(option["label"], option)
                if option["id"] == default_model:
                    selected = self.model.count() - 1
            self.model.blockSignals(False)
            self._loading_models = False
            self._accounts = data.get("accounts", [])
            if self.model.count():
                self.model.setCurrentIndex(selected)
                self._on_model_changed(selected)
            mode_index = MODES.index(default_mode) if default_mode in MODES else 0
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

        def _on_mode_changed(self, _index) -> None:
            mode = self._selected_mode()
            if not self._loading_mode:
                self._preferences = save_gui_preferences(
                    self.root, {"default_mode": mode}
                )

        def _refresh_status(self) -> None:
            try:
                o = A.overview(self.root)
                ws = A.workspace_summary(self.root)
                ins = A.inspector_state(self.root, mode=self._selected_mode())
            except Exception:  # noqa: BLE001
                return
            on = bool(o.get("on"))
            self.dot.setStyleSheet(f"color:{GREEN if on else AMBER}; font-size:13px;")
            branch = f"  ·  {ws['branch']}" if ws["branch"] else ""
            self.workspace_lbl.setText(
                f"{ws['name']}{branch}  ·  {ws['file_count']} files"
            )
            sav = o["savings"]
            self.header_stat.setText(
                f"${sav['estimated_savings_usd']:.2f} saved via Auto  ·  "
                f"${ins['budget']['spent_today']:.2f} spent today"
            )
            connected = [
                a["label"] for a in getattr(self, "_accounts", []) if a["connected"]
            ]
            self.account_lbl.setText(
                "  ".join(f"● {n}" for n in connected) + "  connected"
                if connected
                else "No account connected"
            )
            self.account_lbl.setStyleSheet(
                f"color:{GREEN if connected else MUTED}; font-size:12px; font-weight:600;"
            )

        # -- recents --------------------------------------------------------- #
        def _add_recent(self, text: str) -> None:
            short = text.strip().replace("\n", " ")
            if not short or short in self._recents:
                return
            self._recents.insert(0, short)
            self._recents = self._recents[:8]
            self.recents_hint.hide()
            while self.recents_box.count() > 1:
                item = self.recents_box.takeAt(1)
                if item.widget():
                    item.widget().setParent(None)
            for entry in self._recents:
                label = entry if len(entry) <= 30 else entry[:29] + "…"
                btn = QtWidgets.QPushButton(label)
                btn.setObjectName("Recent")
                btn.setToolTip(entry)
                btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
                btn.clicked.connect(lambda _c=False, t=entry: self._fill(t))
                self.recents_box.addWidget(btn)
            self._save_recents()

        def _fill(self, text: str) -> None:
            self.input.setPlainText(text)
            self.input.setFocus()

        def _recents_path(self) -> Path:
            return Path.home() / ".opai" / "gui_recents.json"

        def _save_recents(self) -> None:
            try:
                p = self._recents_path()
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(
                    json.dumps(self._recents, ensure_ascii=False), encoding="utf-8"
                )
            except OSError:
                pass

        def _load_recents(self) -> None:
            try:
                data = json.loads(
                    self._recents_path().read_text(encoding="utf-8")
                )
                if isinstance(data, list):
                    self._recents = [str(x) for x in data if x][:8]
            except (OSError, ValueError):
                return
            if not self._recents:
                return
            self.recents_hint.hide()
            while self.recents_box.count() > 1:
                item = self.recents_box.takeAt(1)
                if item.widget():
                    item.widget().setParent(None)
            for entry in self._recents:
                label = entry if len(entry) <= 30 else entry[:29] + "…"
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
            connected = [
                a["label"] for a in getattr(self, "_accounts", []) if a["connected"]
            ]
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
                "BotBubble", role, "Working…", role_color=color, meta="thinking…"
            )
            self._row(self._pending)
            worker = Worker(
                lambda: handle_gui_message(
                    self.root, text, model_id=model_id, mode=mode
                )
            )
            worker.done.connect(self._on_ask)
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
            if receipt.get("mode_label"):
                bits.append(str(receipt["mode_label"]))
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

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    _load_app_fonts()
    app.setFont(QtGui.QFont("Nunito", 10))
    window = ChatWindow()
    if initial_task:
        # CLI companion: `opai gui "fix the login bug"` opens pre-loaded.
        window.input.setPlainText(initial_task)
        window.input.setFocus()
    if screenshot_path is not None:
        window.resize(screenshot_path[1], screenshot_path[2])
        window.show()
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
    project_root: Path, out_path: Path, *, width: int = 1040, height: int = 720
) -> dict[str, Any]:
    """Render the chat window to a PNG offscreen - headless GUI smoke test."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return _run_gui(
        project_root, screenshot_path=(str(out_path), int(width), int(height))
    )
