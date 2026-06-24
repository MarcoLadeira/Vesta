"""OPai desktop app - a simple, modern coding chat.

`opai gui` opens one clean window: an AI box where you type a coding task, pick
a model from the accounts you have connected (Claude, Codex - routed through the
CLIs you are already signed into), and OPai runs it the cheapest safe way. Every
OPai tool is one slash-command or one click away.

The module is import-safe on headless machines: PySide6 is imported lazily only
when the window is actually opened. `run_once` and `dependency_status` stay
dependency-free for tests and the install smoke.
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
import os
from pathlib import Path
from typing import Any

from opai import app_state as A
from opai.gui_view_model import SECTIONS

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
    root = project_root.expanduser().resolve()
    from opaihub.gui_pipeline import last_savings_receipt
    from opaihub.gui_preferences import DEFAULT_MODE, load_gui_preferences

    state = A.full_state(root)
    o = state["overview"]
    models = A.available_models(root)
    prefs = load_gui_preferences(root)
    setup = models["setup"]
    default_model = str(prefs.get("default_model") or "auto")
    default_mode = str(prefs.get("default_mode") or DEFAULT_MODE)
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
        "available_models": models["models"],
        "default_model": default_model,
        "selected_model": default_model,
        "mode": default_mode,
        "auto_policy": prefs.get("safe_auto", {}),
        "last_savings_receipt": last_savings_receipt(root),
        "accounts": [
            {"id": a["id"], "connected": a["connected"]} for a in models["accounts"]
        ],
        "account_count": models["account_count"],
        "account_model_count": models["account_model_count"],
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
    }


def _qt():
    if not dependency_status()["available"]:
        raise RuntimeError(INSTALL_HINT)
    from PySide6 import QtCore, QtGui, QtWidgets  # type: ignore[import-not-found]

    return QtCore, QtGui, QtWidgets


# Professional light palette (one system used everywhere).
BG = "#f4f6f9"  # app background - ultra-light grey
BG2 = "#ffffff"  # rail / topbar / inspector surfaces
PANEL = "#ffffff"  # cards + assistant bubble
PANEL_HI = "#eef1f6"  # hover / secondary fill
USERBG = "#e7efff"  # user bubble - soft blue tint
BORDER = "#e5e8ee"  # hairline border
BORDER_HI = "#d2d9e3"  # stronger border / focus
INK = "#1f2a37"  # primary text - dark slate (never pure black)
MUTED = "#586273"  # secondary text - slate grey
FAINT = "#94a0b2"  # tertiary text
ACCENT = "#3a66f0"  # primary action - professional royal blue
ACCENT_HI = "#2f57da"  # accent hover (slightly deeper)
GREEN = "#1f9d6b"  # success / savings
AMBER = "#b3791f"  # attention
RED = "#d6455d"  # error / blocked
CLAUDE = "#c2603f"  # Anthropic terracotta, darkened for a light bg
CODEX = "#0f9d75"  # OpenAI green, darkened for a light bg

PROVIDER_COLOR = {"claude": CLAUDE, "codex": CODEX, "auto": ACCENT}

MODE_LABELS = {
    "ask": "Ask",
    "plan": "Plan",
    "safe-auto": "Safe Auto",
    "approve-edits": "Approve Edits",
    "full-auto": "Full Auto",
}


# One typeface only. Weight + size create hierarchy, never a second font.
FONT = '"Segoe UI Variable","Segoe UI",system-ui,sans-serif'


def _stylesheet() -> str:
    return f"""
    QWidget {{ background:{BG}; color:{INK}; font-family:{FONT}; font-size:14px; }}
    QLabel {{ background:transparent; }}
    QToolTip {{ background:#1f2a37; color:#ffffff; border:0; padding:6px 9px; border-radius:6px; }}

    #TopBar {{ background:{BG2}; border-bottom:1px solid {BORDER}; }}
    #Brand {{ font-size:17px; font-weight:700; letter-spacing:0.2px; color:{INK}; }}
    #Meta {{ color:{FAINT}; font-size:12px; }}
    #Saved {{ color:{GREEN}; font-weight:600; font-size:12px; }}
    #Rail {{ background:{BG2}; border-right:1px solid {BORDER}; }}
    #RailTitle {{ color:{FAINT}; font-size:11px; font-weight:700; letter-spacing:0.7px; }}
    QPushButton#RailItem {{ background:transparent; color:{MUTED}; border:0;
        border-radius:10px; padding:12px 14px; text-align:left; font-weight:600; }}
    QPushButton#RailItem:hover {{ background:{PANEL_HI}; color:{INK}; }}
    QPushButton#RailItem:checked {{ background:#e9eefc; color:{ACCENT}; }}
    #Inspector {{ background:{BG2}; border-left:1px solid {BORDER}; }}
    #InspectorTitle {{ font-size:13px; font-weight:700; color:{INK}; }}
    #InspectorKey {{ color:{FAINT}; font-size:11px; font-weight:700; letter-spacing:0.5px; }}
    #InspectorValue {{ color:{INK}; font-size:13px; font-weight:600; }}
    #Workspace {{ color:{MUTED}; font-size:12.5px; font-weight:600; }}
    QTreeView#Tree {{ background:transparent; border:0; color:{MUTED}; font-size:13px; outline:0; }}
    QTreeView#Tree::item {{ padding:3px 2px; border-radius:6px; }}
    QTreeView#Tree::item:hover {{ background:{PANEL_HI}; color:{INK}; }}
    QTreeView#Tree::item:selected {{ background:#e9eefc; color:{ACCENT}; }}
    QTreeView#Tree::branch {{ background:transparent; }}
    QProgressBar#BudgetBar {{ background:{PANEL_HI}; border:0; border-radius:3px; }}
    QProgressBar#BudgetBar::chunk {{ background:{ACCENT}; border-radius:3px; }}
    QPushButton#Chip {{ background:{BG}; color:{MUTED}; border:1px solid {BORDER};
        border-radius:16px; padding:6px 13px; font-size:12px; font-weight:600; }}
    QPushButton#Chip:hover {{ background:{PANEL_HI}; color:{INK}; border-color:{BORDER_HI}; }}

    QScrollArea {{ border:0; background:{BG}; }}
    QScrollBar:vertical {{ background:transparent; width:12px; margin:4px 2px; }}
    QScrollBar::handle:vertical {{ background:#cdd4df; border-radius:5px; min-height:46px; }}
    QScrollBar::handle:vertical:hover {{ background:#b7c0ce; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height:0; }}

    #Hero {{ font-size:28px; font-weight:700; letter-spacing:-0.2px; color:{INK}; }}
    #HeroSub {{ color:{MUTED}; font-size:15px; font-weight:400; }}
    #SuggestChip {{ background:{BG2}; color:{INK}; border:1px solid {BORDER};
        border-radius:20px; padding:11px 18px; font-size:13px; font-weight:500; }}
    #SuggestChip:hover {{ background:{PANEL_HI}; border-color:{BORDER_HI}; }}

    #UserBubble {{ background:{USERBG}; border:1px solid #d4e1ff; border-radius:16px; }}
    #BotBubble {{ background:{PANEL}; border:1px solid {BORDER}; border-radius:16px; }}
    #ToolBubble {{ background:#fafbfd; border:1px solid {BORDER}; border-radius:16px; }}
    #Role {{ font-weight:700; font-size:13px; }}
    #BubbleMeta {{ color:{FAINT}; font-size:11.5px; font-weight:500; }}
    #Mono {{ color:{MUTED}; font-size:13px; font-weight:400; }}

    #Composer {{ background:{BG2}; border:1px solid {BORDER}; border-radius:20px; }}
    #Composer:focus-within {{ border:1px solid {ACCENT}; }}
    QPlainTextEdit#Input {{ background:transparent; border:0; color:{INK};
        font-size:15px; padding:6px 6px; }}

    QComboBox#Model {{ background:{BG}; border:1px solid {BORDER}; border-radius:12px;
        padding:8px 14px; color:{INK}; font-weight:600; font-size:13px; }}
    QComboBox#Model:hover {{ border-color:{BORDER_HI}; background:{PANEL_HI}; }}
    QComboBox#Model::drop-down {{ border:0; width:20px; }}
    QComboBox#Model QAbstractItemView {{ background:{BG2}; color:{INK};
        border:1px solid {BORDER_HI}; border-radius:10px; padding:6px;
        selection-background-color:#e9eefc; selection-color:{ACCENT}; outline:0; }}

    QPushButton#Ghost {{ background:transparent; color:{MUTED}; border:1px solid {BORDER};
        border-radius:12px; padding:8px 14px; font-weight:600; font-size:13px; }}
    QPushButton#Ghost:hover {{ color:{INK}; border-color:{BORDER_HI}; background:{PANEL_HI}; }}
    QPushButton#Toggle {{ background:transparent; color:{MUTED}; border:1px solid {BORDER};
        border-radius:12px; padding:8px 14px; font-weight:600; font-size:13px; }}
    QPushButton#Toggle:checked {{ color:{ACCENT}; border-color:{ACCENT}; background:#e9eefc; }}
    QPushButton#Send {{ background:{ACCENT}; color:#ffffff; border:0; border-radius:12px;
        padding:10px 22px; font-weight:700; font-size:14px; }}
    QPushButton#Send:hover {{ background:{ACCENT_HI}; }}
    QPushButton#Send:disabled {{ background:{PANEL_HI}; color:{FAINT}; }}

    QMenu {{ background:{BG2}; color:{INK}; border:1px solid {BORDER_HI}; border-radius:10px; padding:6px; }}
    QMenu::item {{ padding:8px 18px; border-radius:7px; }}
    QMenu::item:selected {{ background:#e9eefc; color:{ACCENT}; }}
    QMessageBox {{ background:{BG2}; }}
    """


def _run_gui(
    project_root: Path,
    *,
    screenshot_path: Path | None = None,
    initial_task: str | None = None,
):
    """Build the chat window; either run it (default) or render it to a PNG."""
    QtCore, QtGui, QtWidgets = _qt()
    # Keep Qt's internal style/layout warnings out of the launching terminal, so
    # nothing ever appears to "print to the console" - it all renders in the UI.
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
        fonts_dir = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
        for name in [
            "segoeui.ttf",
            "segoeuib.ttf",
            "segoeuil.ttf",
            "CascadiaCode.ttf",
            "CascadiaMono.ttf",
        ]:
            path = fonts_dir / name
            if path.exists():
                QtGui.QFontDatabase.addApplicationFont(str(path))

    class Worker(QtCore.QThread):
        done = QtCore.Signal(object)

        def __init__(self, fn) -> None:
            super().__init__()
            self._fn = fn

        def run(self) -> None:  # noqa: D401 - QThread entry point
            try:
                self.done.emit(self._fn())
            except Exception as exc:  # noqa: BLE001
                self.done.emit({"status": "error", "answer": str(exc)})

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
            self._loading_models = False
            self._loading_mode = False
            self._preferences = load_gui_preferences(self.root)
            self.setWindowTitle("OPai")
            self.setMinimumSize(1040, 700)
            self.resize(1280, 820)
            self.setStyleSheet(_stylesheet())

            central = QtWidgets.QWidget()
            outer = QtWidgets.QVBoxLayout(central)
            outer.setContentsMargins(0, 0, 0, 0)
            outer.setSpacing(0)
            outer.addWidget(self._build_topbar())

            body = QtWidgets.QWidget()
            body_l = QtWidgets.QHBoxLayout(body)
            body_l.setContentsMargins(0, 0, 0, 0)
            body_l.setSpacing(0)
            body_l.addWidget(self._build_left_rail())

            self.scroll = QtWidgets.QScrollArea()
            self.scroll.setWidgetResizable(True)
            self.scroll.setHorizontalScrollBarPolicy(
                QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff
            )
            self.thread_host = QtWidgets.QWidget()
            self.thread = QtWidgets.QVBoxLayout(self.thread_host)
            self.thread.setContentsMargins(40, 32, 40, 32)
            self.thread.setSpacing(18)
            self.scroll.setWidget(self.thread_host)
            body_l.addWidget(self.scroll, 1)
            body_l.addWidget(self._build_inspector())
            outer.addWidget(body, 1)

            outer.addWidget(self._build_composer())
            self.setCentralWidget(central)

            self._empty = None
            self._load_models()
            self._refresh_header()
            self._show_empty()

        # -- chrome ---------------------------------------------------------- #
        def _build_left_rail(self):
            rail = QtWidgets.QFrame()
            rail.setObjectName("Rail")
            rail.setFixedWidth(236)
            col = QtWidgets.QVBoxLayout(rail)
            col.setContentsMargins(14, 18, 12, 14)
            col.setSpacing(6)
            col.addWidget(self._lbl("CONTROL", name="RailTitle"))
            items = [
                ("Chats", None),
                ("Models", "connect"),
                ("Tools", None),
                ("Settings", "budget"),
            ]
            for index, (label, tool) in enumerate(items):
                button = QtWidgets.QPushButton(label)
                button.setObjectName("RailItem")
                button.setCheckable(True)
                button.setChecked(index == 0)
                button.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
                if tool:
                    button.clicked.connect(lambda _c=False, t=tool: self._run_tool(t))
                col.addWidget(button)
            col.addSpacing(10)
            col.addWidget(self._lbl("WORKSPACE", name="RailTitle"))
            self.tree = QtWidgets.QTreeView()
            self.tree.setObjectName("Tree")
            fs_model_cls = getattr(QtGui, "QFileSystemModel", None) or (
                QtWidgets.QFileSystemModel
            )
            fs = fs_model_cls(self.tree)
            fs.setRootPath(str(self.root))
            self.tree.setModel(fs)
            self.tree.setRootIndex(fs.index(str(self.root)))
            self.tree.setHeaderHidden(True)
            for column in range(1, fs.columnCount()):
                self.tree.hideColumn(column)
            self.tree.setAnimated(True)
            self.tree.setIndentation(14)
            self.tree.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
            self.tree.clicked.connect(self._on_file_clicked)
            col.addWidget(self.tree, 1)
            col.addWidget(self._lbl("Local only · no telemetry", name="Meta"))
            return rail

        def _on_file_clicked(self, index) -> None:
            # Click a file to drop an @reference into the prompt - no copy-paste.
            path = Path(self.tree.model().filePath(index))
            if not path.is_file():
                return
            try:
                rel = path.relative_to(self.root).as_posix()
            except ValueError:
                rel = path.name
            current = self.input.toPlainText()
            sep = " " if current and not current.endswith(" ") else ""
            self.input.setPlainText(f"{current}{sep}@{rel} ")
            self.input.setFocus()
            cursor = self.input.textCursor()
            cursor.movePosition(cursor.MoveOperation.End)
            self.input.setTextCursor(cursor)

        def _build_inspector(self):
            panel = QtWidgets.QFrame()
            panel.setObjectName("Inspector")
            panel.setFixedWidth(296)
            col = QtWidgets.QVBoxLayout(panel)
            col.setContentsMargins(20, 20, 20, 20)
            col.setSpacing(13)
            col.addWidget(self._lbl("Inspector", name="InspectorTitle"))
            self.inspector_model = self._inspector_pair(col, "MODEL", "Auto")
            self.inspector_mode = self._inspector_pair(col, "MODE", "Safe Auto")
            self.inspector_mode_cap = self._lbl("", name="Meta")
            col.addWidget(self.inspector_mode_cap)
            col.addWidget(self._lbl("BUDGET TODAY", name="InspectorKey"))
            self.inspector_budget = self._lbl("—", name="InspectorValue")
            col.addWidget(self.inspector_budget)
            self.budget_bar = QtWidgets.QProgressBar()
            self.budget_bar.setObjectName("BudgetBar")
            self.budget_bar.setTextVisible(False)
            self.budget_bar.setFixedHeight(6)
            self.budget_bar.setRange(0, 100)
            col.addWidget(self.budget_bar)
            self.inspector_context = self._inspector_pair(col, "WORKSPACE", "—")
            self.inspector_receipt = self._inspector_pair(col, "LAST RUN COST", "—")
            self.inspector_saved = self._inspector_pair(col, "SAVED VIA AUTO", "$0.00")
            col.addStretch(1)
            return panel

        def _inspector_pair(self, layout, key: str, value: str):
            layout.addWidget(self._lbl(key, name="InspectorKey"))
            label = self._lbl(value, name="InspectorValue")
            layout.addWidget(label)
            return label

        def _refresh_inspector(self) -> None:
            opt = self._selected()
            mode = self._selected_mode()
            self.inspector_model.setText(opt.get("label", "Auto").split(" · ")[0])
            self.inspector_mode.setText(MODE_LABELS.get(mode, mode))
            try:
                ins = A.inspector_state(self.root, mode=mode)
                sav = A.overview(self.root)["savings"]
            except Exception:  # noqa: BLE001 - telemetry must never crash the UI
                return
            self.inspector_mode_cap.setText(ins["mode"]["capability"])
            budget = ins["budget"]
            self.inspector_budget.setText(
                "panic on · local only" if budget["panic"] else budget["text"]
            )
            self.budget_bar.setValue(int(budget["pct"]))
            self.inspector_context.setText(ins["workspace"]["text"])
            self.inspector_saved.setText(
                f"${sav['estimated_savings_usd']:.2f} · "
                f"{sav['cloud_calls_avoided']} calls avoided"
            )

        def _build_topbar(self):
            bar = QtWidgets.QFrame()
            bar.setObjectName("TopBar")
            row = QtWidgets.QHBoxLayout(bar)
            row.setContentsMargins(20, 13, 18, 13)
            row.setSpacing(10)
            self.dot = QtWidgets.QLabel("●")
            row.addWidget(self.dot)
            row.addWidget(self._lbl("OPai", name="Brand"))
            row.addSpacing(6)
            self.conn_chip = QtWidgets.QPushButton("")
            self.conn_chip.setObjectName("Chip")
            self.conn_chip.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
            self.conn_chip.setToolTip("Connect Claude / Codex accounts")
            self.conn_chip.clicked.connect(lambda: self._run_tool("connect"))
            row.addWidget(self.conn_chip)
            row.addStretch(1)
            folder = QtWidgets.QLabel("\U0001f4c1")  # workspace glyph
            folder.setStyleSheet(f"color:{FAINT}; font-size:13px;")
            row.addWidget(folder)
            self.workspace_lbl = self._lbl(root.name, name="Workspace")
            row.addWidget(self.workspace_lbl)
            return bar

        def _build_composer(self):
            wrap = QtWidgets.QWidget()
            wl = QtWidgets.QVBoxLayout(wrap)
            wl.setContentsMargins(32, 8, 32, 24)
            box = QtWidgets.QFrame()
            box.setObjectName("Composer")
            self._shadow(box, blur=38, dy=10, alpha=26)
            bl = QtWidgets.QVBoxLayout(box)
            bl.setContentsMargins(18, 16, 16, 14)
            bl.setSpacing(10)

            self.input = Composer()
            self.input.setObjectName("Input")
            self.input.setPlaceholderText(
                "Ask anything, or /connect to add an account…"
            )
            self.input.setFixedHeight(64)
            self.input.submit.connect(self._send)
            bl.addWidget(self.input)

            ctl = QtWidgets.QHBoxLayout()
            ctl.setSpacing(8)
            self.provider_dot = QtWidgets.QLabel("●")
            self.provider_dot.setStyleSheet(f"color:{ACCENT}; font-size:13px;")
            ctl.addWidget(self.provider_dot)
            self.model = QtWidgets.QComboBox()
            self.model.setObjectName("Model")
            self.model.setMinimumWidth(230)
            self.model.currentIndexChanged.connect(self._on_model_changed)
            ctl.addWidget(self.model)
            self.mode = QtWidgets.QComboBox()
            self.mode.setObjectName("Model")
            self.mode.setMinimumWidth(150)
            for mode_id in MODES:
                self.mode.addItem(MODE_LABELS.get(mode_id, mode_id), mode_id)
            self.mode.currentIndexChanged.connect(self._on_mode_changed)
            ctl.addWidget(self.mode)
            self.tools_btn = QtWidgets.QPushButton("Tools")
            self.tools_btn.setObjectName("Ghost")
            self.tools_btn.setMenu(self._tools_menu())
            ctl.addWidget(self.tools_btn)
            ctl.addStretch(1)
            self.send_btn = QtWidgets.QPushButton("Send")
            self.send_btn.setObjectName("Send")
            self.send_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
            self.send_btn.clicked.connect(self._send)
            ctl.addWidget(self.send_btn)
            bl.addLayout(ctl)
            wl.addWidget(box)
            return wrap

        # -- widgets --------------------------------------------------------- #
        def _lbl(self, text, *, name=None, color=None):
            label = QtWidgets.QLabel(str(text))
            if name:
                label.setObjectName(name)
            if color:
                label.setStyleSheet(f"color:{color};")
            label.setWordWrap(True)
            return label

        def _shadow(self, widget, *, blur=28, dy=6, alpha=28):
            # Soft "light from above" depth on white cards - Qt QSS has no box-shadow.
            effect = QtWidgets.QGraphicsDropShadowEffect(widget)
            effect.setBlurRadius(blur)
            effect.setXOffset(0)
            effect.setYOffset(dy)
            effect.setColor(QtGui.QColor(31, 42, 64, alpha))
            widget.setGraphicsEffect(effect)

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
            selected_index = 0
            for option in data["models"]:
                self.model.addItem(option["label"], option)
                if option["id"] == default_model:
                    selected_index = self.model.count() - 1
            self.model.blockSignals(False)
            self._loading_models = False
            self._model_hint = data.get("hint")
            self._accounts = data.get("accounts", [])
            if self.model.count():
                self.model.setCurrentIndex(selected_index)
                self._on_model_changed(selected_index)
            mode_index = max(
                0, MODES.index(default_mode) if default_mode in MODES else 0
            )
            self._loading_mode = True
            self.mode.setCurrentIndex(mode_index)
            self._loading_mode = False
            self._on_mode_changed(mode_index)

        def _selected(self) -> dict[str, Any]:
            data = self.model.currentData()
            return data if isinstance(data, dict) else {"id": "auto", "kind": "auto"}

        def _selected_mode(self) -> str:
            data = self.mode.currentData()
            return str(data or DEFAULT_MODE)

        def _on_model_changed(self, _index) -> None:
            opt = self._selected()
            provider = opt.get("provider") or opt.get("kind", "auto")
            self.provider_dot.setStyleSheet(
                f"color:{PROVIDER_COLOR.get(provider, ACCENT)}; font-size:13px;"
            )
            if opt.get("kind") == "account":
                who = opt["label"].split(" · ")[0]
                self.input.setPlaceholderText(f"Message {who} · runs on your account…")
            elif opt.get("kind") == "local":
                self.input.setPlaceholderText("Run locally for $0 · ask anything…")
            else:
                self.input.setPlaceholderText(
                    "Ask anything — OPai routes the cheapest safe model…"
                )
            if not self._loading_models:
                self._preferences = save_gui_preferences(
                    self.root, {"default_model": opt.get("id", "auto")}
                )
            if hasattr(self, "inspector_model"):
                self._refresh_inspector()

        def _on_mode_changed(self, _index) -> None:
            mode = self._selected_mode()
            if not self._loading_mode:
                self._preferences = save_gui_preferences(
                    self.root, {"default_mode": mode}
                )
            if hasattr(self, "inspector_mode"):
                self._refresh_inspector()

        def _refresh_header(self) -> None:
            try:
                o = A.overview(self.root)
            except Exception:  # noqa: BLE001
                return
            on = bool(o.get("on"))
            self.dot.setStyleSheet(f"color:{GREEN if on else AMBER}; font-size:14px;")
            self.dot.setToolTip("OPai ON" if on else "OPai needs attention")
            # Lead the top bar with the dev workspace, not the savings number.
            try:
                ws = A.workspace_summary(self.root)
                branch = f"  ·  {ws['branch']}" if ws["branch"] else ""
                self.workspace_lbl.setText(
                    f"{ws['name']}{branch}  ·  {ws['file_count']} files"
                )
            except Exception:  # noqa: BLE001
                self.workspace_lbl.setText(self.root.name)
            if hasattr(self, "inspector_model"):
                self._refresh_inspector()
            connected = [
                a["label"] for a in getattr(self, "_accounts", []) if a["connected"]
            ]
            if connected:
                self.conn_chip.setText("  ".join(f"● {n}" for n in connected))
                self.conn_chip.setStyleSheet(
                    f"QPushButton#Chip {{ background:{PANEL}; color:{GREEN}; "
                    f"border:1px solid {BORDER}; border-radius:14px; padding:5px 11px; "
                    f"font-size:12px; font-weight:700; }}"
                    f"QPushButton#Chip:hover {{ border-color:{BORDER_HI}; }}"
                )
            else:
                self.conn_chip.setText("Connect account")
                self.conn_chip.setStyleSheet("")

        # -- conversation ---------------------------------------------------- #
        def _show_empty(self) -> None:
            self._empty = QtWidgets.QWidget()
            self._empty.setSizePolicy(
                QtWidgets.QSizePolicy.Policy.Expanding,
                QtWidgets.QSizePolicy.Policy.Expanding,
            )
            el = QtWidgets.QVBoxLayout(self._empty)
            el.setContentsMargins(0, 0, 0, 0)
            el.setAlignment(QtCore.Qt.AlignmentFlag.AlignHCenter)
            el.addStretch(1)
            title = self._lbl("What do you want to build?")
            title.setObjectName("Hero")
            title.setAlignment(QtCore.Qt.AlignmentFlag.AlignHCenter)
            el.addWidget(title)
            connected = [
                a["label"] for a in getattr(self, "_accounts", []) if a["connected"]
            ]
            sub_text = (
                f"{' and '.join(connected)} connected · OPai routes the cheapest safe path."
                if connected
                else "Connect your Claude or Codex account, then just type."
            )
            sub = self._lbl(sub_text, name="HeroSub")
            sub.setAlignment(QtCore.Qt.AlignmentFlag.AlignHCenter)
            el.addWidget(sub)
            el.addSpacing(18)
            chips = QtWidgets.QHBoxLayout()
            chips.setSpacing(9)
            chips.setAlignment(QtCore.Qt.AlignmentFlag.AlignHCenter)
            suggestions = (
                ("Summarize my changes", "Summarize my uncommitted changes"),
                ("Explain this repo", "Give me a high-level tour of this codebase"),
                ("Find a bug", "Look for a likely bug in my recent changes"),
                ("/connect", "/connect"),
            )
            for label, payload in suggestions:
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

        def _bubble(
            self, object_name, role, body, *, role_color=None, meta=None, mono=False
        ):
            self._clear_empty()
            frame = QtWidgets.QFrame()
            frame.setObjectName(object_name)
            self._shadow(frame, blur=22, dy=5, alpha=16)
            fl = QtWidgets.QVBoxLayout(frame)
            fl.setContentsMargins(18, 15, 18, 16)
            fl.setSpacing(7)
            if role:
                r = self._lbl(role)
                r.setObjectName("Role")
                r.setStyleSheet(
                    f"color:{role_color or MUTED}; font-weight:800; font-size:12.5px;"
                )
                fl.addWidget(r)
            body_lbl = self._lbl(body, name="Mono" if mono else None)
            body_lbl.setTextInteractionFlags(
                QtCore.Qt.TextInteractionFlag.TextSelectableByMouse
            )
            fl.addWidget(body_lbl)
            if meta:
                fl.addWidget(self._lbl(meta, name="BubbleMeta"))
            holder = QtWidgets.QHBoxLayout()
            if object_name == "UserBubble":
                holder.addStretch(1)
                frame.setMaximumWidth(560)
                holder.addWidget(frame)
            else:
                frame.setMaximumWidth(780)
                holder.addWidget(frame)
                holder.addStretch(1)
            container = QtWidgets.QWidget()
            container.setLayout(holder)
            holder.setContentsMargins(0, 0, 0, 0)
            self.thread.addWidget(container)
            QtCore.QTimer.singleShot(30, self._to_bottom)
            return container

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
            self.send_btn.setEnabled(not on)
            self.send_btn.setText("Working…" if on else "Send")

        def _send(self) -> None:
            text = self.input.toPlainText().strip()
            if not text:
                return
            self.input.clear()
            if text.startswith("/"):
                parts = text[1:].split(None, 1)
                self._run_tool(parts[0], parts[1] if len(parts) > 1 else "")
                return
            self._bubble("UserBubble", None, text)
            self._busy(True)
            opt = self._selected()
            model_id = opt.get("id", "auto")
            mode = self._selected_mode()
            # A live "working" bubble so the chat feels responsive while the
            # model runs (account calls can take a while), replaced on result.
            if opt.get("kind") == "account":
                role = opt.get("label", "Account").split(" · ")[0]
                color = PROVIDER_COLOR.get(opt.get("provider"), ACCENT)
                meta = f"{MODE_LABELS.get(mode, mode)} · checking budget first…"
            else:
                role, color, meta = (
                    "OPai",
                    GREEN,
                    f"{MODE_LABELS.get(mode, mode)} · auto tools running…",
                )
            self._pending = self._bubble(
                "BotBubble", role, "Working…", role_color=color, meta=meta
            )
            worker = Worker(
                lambda: handle_gui_message(
                    self.root, text, model_id=model_id, mode=mode
                )
            )
            worker.done.connect(self._on_ask)
            self._workers.append(worker)
            worker.start()

        def _trace_text(self, trace) -> str:
            if not trace:
                return "No automatic tools ran."
            lines = []
            for item in trace[:8]:
                label = item.get("label") or item.get("id") or "tool"
                result = item.get("result") or {}
                detail = ""
                if "decision" in result:
                    detail = f" · {result.get('decision')}"
                elif "tier" in result:
                    detail = f" · {result.get('tier')}"
                lines.append(f"- {label}{detail}")
            return "\n".join(lines)

        def _receipt_text(self, receipt) -> str:
            if not isinstance(receipt, dict):
                return "No savings receipt was recorded."
            saved = float(receipt.get("estimated_savings_usd") or 0.0)
            baseline = float(receipt.get("estimated_baseline_usd") or 0.0)
            actual = float(receipt.get("estimated_actual_usd") or 0.0)
            paid_avoided = "yes" if receipt.get("paid_call_avoided") else "no"
            context_saved = int(receipt.get("context_tokens_saved") or 0)
            confidence = receipt.get("confidence", "estimated")
            return (
                f"Saved: ${saved:.4f}\n"
                f"Baseline: ${baseline:.4f}\n"
                f"Actual route: ${actual:.4f}\n"
                f"Paid call avoided: {paid_avoided}\n"
                f"Context tokens saved: {context_saved}\n"
                f"Confidence: {confidence}"
            )

        def _on_gui_result(self, result) -> None:
            status = result.get("status", "error")
            trace = result.get("tool_trace") or []
            receipt = result.get("receipt") or {}
            warnings = result.get("warnings") or []
            if trace:
                self._bubble(
                    "ToolBubble",
                    "OPai auto tools",
                    self._trace_text(trace),
                    role_color=ACCENT,
                    mono=True,
                )
            if warnings:
                self._bubble(
                    "ToolBubble",
                    "Blocked",
                    "\n".join(w.get("reason", str(w)) for w in warnings),
                    role_color=RED,
                    meta="Safe Auto policy",
                )
            answer = result.get("answer") or ""
            changed = result.get("changed_files") or []
            role = "OPai"
            color = GREEN
            if status == "blocked":
                role, color = "Safe Auto", AMBER
            elif status in {"needs_model", "needs_confirmation"}:
                role, color = "Action needed", AMBER
            elif status not in {"answered", "cache_hit"}:
                role, color = "OPai", RED
            self._bubble(
                "BotBubble",
                role,
                answer or "OPai didn't return a response for that one.",
                role_color=color,
                meta=status.replace("_", " "),
                mono=False,
            )
            # Show the actual diff of what changed on disk - not just a wall of text.
            if changed:
                try:
                    diff = A.workspace_diff(self.root)
                except Exception:  # noqa: BLE001
                    diff = ""
                self._bubble(
                    "ToolBubble",
                    f"Diff · {len(changed)} file(s) changed",
                    diff or "\n".join(changed),
                    role_color=ACCENT,
                    mono=True,
                )
            self._bubble(
                "ToolBubble",
                "Savings receipt",
                self._receipt_text(receipt),
                role_color=GREEN if status != "blocked" else AMBER,
                mono=True,
            )
            if hasattr(self, "inspector_receipt") and isinstance(receipt, dict):
                actual = float(receipt.get("estimated_actual_usd") or 0)
                self.inspector_receipt.setText(f"${actual:.4f}" if actual else "free")
            if hasattr(self, "inspector_model"):
                self._refresh_inspector()

        def _on_ask(self, result) -> None:
            self._busy(False)
            if self._pending is not None:
                self._pending.setParent(None)
                self._pending = None
            if "tool_trace" in result or "receipt" in result:
                self._on_gui_result(result)
                self._refresh_header()
                return
            status = result.get("status")
            tier = result.get("tier", "")
            if status == "answered_by_account":
                provider = result.get("provider", "")
                role = provider.capitalize() or "Account"
                model = result.get("model", "")
                cost = result.get("cost_usd")
                meta_bits = []
                if model and model != provider:
                    meta_bits.append(model)
                meta_bits += ["your account", "paid"]
                if isinstance(cost, (int, float)) and cost > 0:
                    meta_bits.append(f"${cost:.4f}")
                if result.get("allow_edits"):
                    meta_bits.append("edits on")
                answer = result.get("answer", "")
                changed = result.get("changed_files") or []
                if changed:
                    answer = f"{answer}\n\nChanged files:\n" + "\n".join(changed)
                self._bubble(
                    "BotBubble",
                    role,
                    answer,
                    role_color=PROVIDER_COLOR.get(provider, ACCENT),
                    meta=" · ".join(meta_bits),
                )
            elif status in ("answered_locally", "cache_hit"):
                src = (
                    "cache"
                    if status == "cache_hit"
                    else (result.get("runner") or "local")
                )
                model = result.get("model", "")
                meta = f"{tier} · {src} · free" + (f" · {model}" if model else "")
                self._bubble(
                    "BotBubble",
                    "OPai",
                    result.get("answer", ""),
                    role_color=GREEN,
                    meta=meta,
                    mono=True,
                )
            elif status == "blocked_panic":
                self._bubble(
                    "BotBubble",
                    "Panic mode on",
                    result.get("reason", ""),
                    role_color=AMBER,
                    meta="local-only · paid accounts blocked",
                )
            elif status == "account_not_connected":
                self._bubble(
                    "BotBubble",
                    "Not connected",
                    result.get("hint", ""),
                    role_color=AMBER,
                )
            elif status == "no_local_model":
                self._bubble(
                    "BotBubble",
                    "No model available",
                    result.get("hint", "")
                    + "\n\n"
                    + (result.get("next_command", "") or ""),
                    role_color=AMBER,
                    meta=f"{tier} · would route here",
                )
            elif status == "confirmation_required":
                self._bubble(
                    "BotBubble",
                    "Cloud tier recommended",
                    "OPai won't spend automatically. Pick your Claude or Codex account "
                    "above to run this, or connect a local model to do it free.",
                    role_color=AMBER,
                    meta=f"{tier} · paid/cloud · gated",
                )
            elif status in ("account_error", "runner_error", "error"):
                self._bubble(
                    "BotBubble",
                    "Error",
                    str(result.get("error") or result.get("answer") or result),
                    role_color=RED,
                )
            else:
                self._bubble(
                    "BotBubble", "OPai", str(result), role_color=MUTED, meta=status
                )
            self._refresh_header()

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
                    self._bubble(
                        "ToolBubble",
                        result.get("title"),
                        applied.get("text", "done"),
                        role_color=MUTED,
                    )
                else:
                    self._bubble(
                        "ToolBubble",
                        result.get("title"),
                        "Cancelled.",
                        role_color=MUTED,
                    )
            else:
                self._bubble(
                    "ToolBubble",
                    result.get("title", "Tool"),
                    result.get("text", ""),
                    role_color=MUTED,
                    mono=True,
                )
            self._refresh_header()

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    _load_app_fonts()
    app.setFont(QtGui.QFont("Segoe UI", 10))
    window = ChatWindow()
    if initial_task:
        # CLI companion: `opai gui "fix the login bug"` opens pre-loaded.
        window.input.setPlainText(initial_task)
        window.input.setFocus()
        cursor = window.input.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        window.input.setTextCursor(cursor)
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
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return _run_gui(
        project_root, screenshot_path=(str(out_path), int(width), int(height))
    )
