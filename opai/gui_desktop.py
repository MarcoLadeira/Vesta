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
    state = A.full_state(root)
    o = state["overview"]
    models = A.available_models(root)
    setup = models["setup"]
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
    }


def _qt():
    if not dependency_status()["available"]:
        raise RuntimeError(INSTALL_HINT)
    from PySide6 import QtCore, QtGui, QtWidgets  # type: ignore[import-not-found]

    return QtCore, QtGui, QtWidgets


# Modern dark palette (one system used everywhere).
BG = "#0d0e13"
BG2 = "#14161d"
PANEL = "#181b22"
PANEL_HI = "#20242e"
USERBG = "#212536"
BORDER = "#262a35"
BORDER_HI = "#39414f"
INK = "#e9ebf2"
MUTED = "#9aa2b3"
FAINT = "#646d80"
ACCENT = "#6d6cf6"
ACCENT_HI = "#8a89f8"
GREEN = "#3ecf8e"
AMBER = "#e0a458"
RED = "#f2667d"
CLAUDE = "#d77757"
CODEX = "#19c37d"

PROVIDER_COLOR = {"claude": CLAUDE, "codex": CODEX, "auto": ACCENT}


def _stylesheet() -> str:
    return f"""
    QWidget {{ background:{BG}; color:{INK};
        font-family:"Segoe UI Variable","Segoe UI",system-ui,sans-serif; font-size:14px; }}
    QLabel {{ background:transparent; }}
    QToolTip {{ background:{PANEL_HI}; color:{INK}; border:1px solid {BORDER_HI}; padding:4px 7px; }}

    #TopBar {{ background:{BG2}; border-bottom:1px solid {BORDER}; }}
    #Brand {{ font-size:16px; font-weight:800; letter-spacing:0.3px; }}
    #Meta {{ color:{FAINT}; font-size:12px; }}
    #Saved {{ color:{GREEN}; font-weight:700; font-size:12px; }}
    QPushButton#Chip {{ background:{PANEL}; color:{MUTED}; border:1px solid {BORDER};
        border-radius:14px; padding:5px 11px; font-size:12px; font-weight:600; }}
    QPushButton#Chip:hover {{ background:{PANEL_HI}; color:{INK}; border-color:{BORDER_HI}; }}

    QScrollArea {{ border:0; background:{BG}; }}
    QScrollBar:vertical {{ background:{BG}; width:10px; margin:2px; }}
    QScrollBar::handle:vertical {{ background:#2c3340; border-radius:5px; min-height:44px; }}
    QScrollBar::handle:vertical:hover {{ background:#39414f; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height:0; }}

    #Hero {{ font-size:25px; font-weight:800; letter-spacing:0.2px; }}
    #HeroSub {{ color:{MUTED}; font-size:14px; }}
    #SuggestChip {{ background:{PANEL}; color:{INK}; border:1px solid {BORDER};
        border-radius:18px; padding:9px 16px; font-size:13px; }}
    #SuggestChip:hover {{ background:{PANEL_HI}; border-color:{ACCENT}; }}

    #UserBubble {{ background:{USERBG}; border:1px solid #2c3146; border-radius:14px; }}
    #BotBubble {{ background:{PANEL}; border:1px solid {BORDER}; border-radius:14px; }}
    #ToolBubble {{ background:{BG2}; border:1px solid {BORDER}; border-radius:14px; }}
    #Role {{ font-weight:800; font-size:12.5px; }}
    #BubbleMeta {{ color:{FAINT}; font-size:11px; }}
    #Mono {{ font-family:"Cascadia Code","JetBrains Mono",Consolas,monospace;
        color:{INK}; font-size:12.5px; }}

    #Composer {{ background:{PANEL}; border:1px solid {BORDER}; border-radius:18px; }}
    #Composer:focus-within {{ border:1px solid {BORDER_HI}; }}
    QPlainTextEdit#Input {{ background:transparent; border:0; color:{INK};
        font-size:15px; padding:4px 4px; }}

    QComboBox#Model {{ background:{PANEL_HI}; border:1px solid {BORDER}; border-radius:10px;
        padding:6px 12px; color:{INK}; font-weight:600; font-size:13px; }}
    QComboBox#Model:hover {{ border-color:{BORDER_HI}; }}
    QComboBox#Model::drop-down {{ border:0; width:18px; }}
    QComboBox#Model QAbstractItemView {{ background:{PANEL_HI}; color:{INK};
        border:1px solid {BORDER_HI}; border-radius:8px; padding:4px;
        selection-background-color:{ACCENT}; outline:0; }}

    QPushButton#Ghost {{ background:transparent; color:{MUTED}; border:1px solid {BORDER};
        border-radius:10px; padding:6px 12px; font-weight:600; font-size:13px; }}
    QPushButton#Ghost:hover {{ color:{INK}; border-color:{BORDER_HI}; background:{PANEL_HI}; }}
    QPushButton#Toggle {{ background:transparent; color:{MUTED}; border:1px solid {BORDER};
        border-radius:10px; padding:6px 12px; font-weight:600; font-size:13px; }}
    QPushButton#Toggle:checked {{ color:{AMBER}; border-color:{AMBER}; background:rgba(224,164,88,0.10); }}
    QPushButton#Send {{ background:{ACCENT}; color:#ffffff; border:0; border-radius:11px;
        padding:8px 20px; font-weight:800; font-size:14px; }}
    QPushButton#Send:hover {{ background:{ACCENT_HI}; }}
    QPushButton#Send:disabled {{ background:{PANEL_HI}; color:{FAINT}; }}

    QMenu {{ background:{PANEL_HI}; color:{INK}; border:1px solid {BORDER_HI}; padding:5px; }}
    QMenu::item {{ padding:7px 16px; border-radius:6px; }}
    QMenu::item:selected {{ background:{ACCENT}; color:#ffffff; }}
    QMessageBox {{ background:{PANEL}; }}
    """


def _run_gui(project_root: Path, *, screenshot_path: Path | None = None):
    """Build the chat window; either run it (default) or render it to a PNG."""
    QtCore, QtGui, QtWidgets = _qt()
    root = project_root.expanduser().resolve()

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
            self._edits_warned = False
            self.setWindowTitle("OPai")
            self.setMinimumSize(760, 580)
            self.resize(960, 760)
            self.setStyleSheet(_stylesheet())

            central = QtWidgets.QWidget()
            outer = QtWidgets.QVBoxLayout(central)
            outer.setContentsMargins(0, 0, 0, 0)
            outer.setSpacing(0)
            outer.addWidget(self._build_topbar())

            self.scroll = QtWidgets.QScrollArea()
            self.scroll.setWidgetResizable(True)
            self.scroll.setHorizontalScrollBarPolicy(
                QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff
            )
            self.thread_host = QtWidgets.QWidget()
            self.thread = QtWidgets.QVBoxLayout(self.thread_host)
            self.thread.setContentsMargins(28, 22, 28, 22)
            self.thread.setSpacing(14)
            self.scroll.setWidget(self.thread_host)
            outer.addWidget(self.scroll, 1)

            outer.addWidget(self._build_composer())
            self.setCentralWidget(central)

            self._empty = None
            self._load_models()
            self._refresh_header()
            self._show_empty()

        # -- chrome ---------------------------------------------------------- #
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
            self.saved = self._lbl("", name="Saved")
            row.addWidget(self.saved)
            row.addSpacing(8)
            row.addWidget(self._lbl(root.name, name="Meta"))
            return bar

        def _build_composer(self):
            wrap = QtWidgets.QWidget()
            wl = QtWidgets.QVBoxLayout(wrap)
            wl.setContentsMargins(28, 4, 28, 18)
            box = QtWidgets.QFrame()
            box.setObjectName("Composer")
            bl = QtWidgets.QVBoxLayout(box)
            bl.setContentsMargins(14, 12, 12, 10)
            bl.setSpacing(8)

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
            self.edits = QtWidgets.QPushButton("Read-only")
            self.edits.setObjectName("Toggle")
            self.edits.setCheckable(True)
            self.edits.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
            self.edits.setToolTip("Read-only: the model answers but won't change files")
            self.edits.toggled.connect(self._on_edits_toggled)
            ctl.addWidget(self.edits)
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

        def _tools_menu(self):
            menu = QtWidgets.QMenu(self)
            for tool in A.TOOLS:
                act = menu.addAction(f"{tool['label']} — {tool['desc']}")
                act.triggered.connect(lambda _c=False, t=tool["id"]: self._run_tool(t))
            return menu

        def _load_models(self) -> None:
            data = A.available_models(self.root)
            self.model.blockSignals(True)
            self.model.clear()
            for option in data["models"]:
                self.model.addItem(option["label"], option)
            self.model.blockSignals(False)
            self._model_hint = data.get("hint")
            self._accounts = data.get("accounts", [])
            if self.model.count():
                self.model.setCurrentIndex(0)
                self._on_model_changed(0)

        def _selected(self) -> dict[str, Any]:
            data = self.model.currentData()
            return data if isinstance(data, dict) else {"id": "auto", "kind": "auto"}

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

        def _on_edits_toggled(self, on) -> None:
            if on and not self._edits_warned:
                self._edits_warned = True
                ok = QtWidgets.QMessageBox.warning(
                    self,
                    "Allow edits",
                    "The selected model may edit files and run commands in this "
                    "project. Only enable this for tasks you want it to act on.\n\n"
                    "Enable edit mode?",
                    QtWidgets.QMessageBox.StandardButton.Yes
                    | QtWidgets.QMessageBox.StandardButton.No,
                )
                if ok != QtWidgets.QMessageBox.StandardButton.Yes:
                    self.edits.setChecked(False)
                    return
            self.edits.setText("Allow edits" if on else "Read-only")
            self.edits.setToolTip(
                "Edit mode: the model may change files and run commands"
                if on
                else "Read-only: the model answers but won't change files"
            )

        def _refresh_header(self) -> None:
            try:
                o = A.overview(self.root)
            except Exception:  # noqa: BLE001
                return
            on = bool(o.get("on"))
            self.dot.setStyleSheet(f"color:{GREEN if on else AMBER}; font-size:14px;")
            self.dot.setToolTip("OPai ON" if on else "OPai needs attention")
            sav = o["savings"]
            self.saved.setText(
                f"${sav['estimated_savings_usd']:.2f} saved · "
                f"{sav['cloud_calls_avoided']} paid calls avoided"
            )
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
            fl = QtWidgets.QVBoxLayout(frame)
            fl.setContentsMargins(15, 11, 15, 12)
            fl.setSpacing(6)
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
            # A live "working" bubble so the chat feels responsive while the
            # model runs (account calls can take a while), replaced on result.
            if opt.get("kind") == "account":
                role = opt.get("label", "Account").split(" · ")[0]
                color = PROVIDER_COLOR.get(opt.get("provider"), ACCENT)
                meta = "running on your account…"
            else:
                role, color, meta = "OPai", GREEN, "routing the cheapest safe path…"
            self._pending = self._bubble(
                "BotBubble", role, "Working…", role_color=color, meta=meta
            )
            allow_edits = self.edits.isChecked()
            worker = Worker(
                lambda: A.ask(self.root, text, model_id, allow_edits=allow_edits)
            )
            worker.done.connect(self._on_ask)
            self._workers.append(worker)
            worker.start()

        def _on_ask(self, result) -> None:
            self._busy(False)
            if self._pending is not None:
                self._pending.setParent(None)
                self._pending = None
            status = result.get("status")
            tier = result.get("tier", "")
            if status == "answered_by_account":
                provider = result.get("provider", "")
                role = provider.capitalize() or "Account"
                edits = " · edits on" if result.get("allow_edits") else ""
                self._bubble(
                    "BotBubble",
                    role,
                    result.get("answer", ""),
                    role_color=PROVIDER_COLOR.get(provider, ACCENT),
                    meta=f"your account · paid{edits}",
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
    window = ChatWindow()
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


def launch(project_root: Path) -> int:
    """Open the native desktop chat window (blocks until closed)."""
    return int(_run_gui(project_root) or 0)


def render_screenshot(
    project_root: Path, out_path: Path, *, width: int = 1040, height: int = 720
) -> dict[str, Any]:
    """Render the chat window to a PNG offscreen - headless GUI smoke test."""
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return _run_gui(
        project_root, screenshot_path=(str(out_path), int(width), int(height))
    )
