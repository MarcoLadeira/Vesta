"""OPai desktop app - a simple, Cursor-style coding chat.

`opai gui` opens one clean window: an AI box where you type a coding task, pick
your connected model, and OPai runs it the cheapest safe way (local-first). Every
OPai tool is one slash-command or one click away. No dashboards, no clutter.

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
        "tools": [t["id"] for t in A.TOOLS],
        "zero_state": o["savings"]["zero_state"],
        "sections": [key for key, _ in SECTIONS],
    }


def _qt():
    if not dependency_status()["available"]:
        raise RuntimeError(INSTALL_HINT)
    from PySide6 import QtCore, QtGui, QtWidgets  # type: ignore[import-not-found]

    return QtCore, QtGui, QtWidgets


# Palette (one set of colors used everywhere).
BG = "#0b0f17"
PANEL = "#121a27"
PANEL2 = "#0e1521"
INK = "#eef3fb"
MUTED = "#9aa8bd"
FAINT = "#5f6c81"
LINE = "#223047"
GREEN = "#34d399"
TEAL = "#22d3ee"
WARN = "#f5b54a"
BAD = "#fb7185"


def _stylesheet() -> str:
    return f"""
    QWidget {{ background:{BG}; color:{INK};
        font-family:"Segoe UI Variable","Segoe UI",sans-serif; font-size:14px; }}
    QLabel {{ background:transparent; }}
    #TopBar {{ background:{PANEL2}; border-bottom:1px solid {LINE}; }}
    #Brand {{ font-size:16px; font-weight:800; }}
    #Meta {{ color:{MUTED}; font-size:12px; }}
    #Saved {{ color:{GREEN}; font-weight:800; }}
    #Composer {{ background:{PANEL}; border:1px solid {LINE}; border-radius:14px; }}
    QPlainTextEdit#Input {{ background:transparent; border:0; color:{INK}; font-size:15px; padding:6px; }}
    QComboBox {{ background:{PANEL2}; border:1px solid {LINE}; border-radius:9px;
        padding:6px 10px; color:{INK}; }}
    QComboBox::drop-down {{ border:0; width:18px; }}
    QComboBox QAbstractItemView {{ background:{PANEL2}; color:{INK};
        selection-background-color:{LINE}; border:1px solid {LINE}; }}
    QPushButton {{ border:1px solid {LINE}; border-radius:10px; padding:8px 14px;
        background:{PANEL2}; color:{INK}; font-weight:700; }}
    QPushButton:hover {{ border-color:{TEAL}; }}
    QPushButton#Send {{ background:{TEAL}; color:#04231a; border:0; }}
    QPushButton#Chip {{ background:{PANEL2}; color:{MUTED}; border-radius:16px; padding:7px 14px; font-weight:600; }}
    QPushButton#Chip:hover {{ color:{INK}; border-color:{TEAL}; }}
    QScrollArea {{ border:0; }}
    QScrollBar:vertical {{ background:{BG}; width:9px; }}
    QScrollBar::handle:vertical {{ background:#2a3850; border-radius:4px; min-height:40px; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height:0; }}
    #UserBubble {{ background:#142133; border:1px solid {LINE}; border-radius:12px; }}
    #OpaiBubble {{ background:{PANEL}; border:1px solid {LINE}; border-radius:12px; }}
    #ToolBubble {{ background:#0c1a1c; border:1px solid #1d3a3a; border-radius:12px; }}
    #BubbleMeta {{ color:{FAINT}; font-size:11px; }}
    #Answer {{ font-family:"Cascadia Code","JetBrains Mono",Consolas,monospace; color:{INK}; font-size:13px; }}
    #Empty {{ color:{MUTED}; }}
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
            self.setWindowTitle("OPai")
            self.setMinimumSize(720, 560)
            self.resize(900, 720)
            self.setStyleSheet(_stylesheet())

            central = QtWidgets.QWidget()
            outer = QtWidgets.QVBoxLayout(central)
            outer.setContentsMargins(0, 0, 0, 0)
            outer.setSpacing(0)

            # Top bar: OPai + ON dot + savings, project on the right.
            bar = QtWidgets.QFrame()
            bar.setObjectName("TopBar")
            barl = QtWidgets.QHBoxLayout(bar)
            barl.setContentsMargins(18, 12, 18, 12)
            self.dot = QtWidgets.QLabel("●")
            barl.addWidget(self.dot)
            barl.addWidget(self._lbl("OPai", name="Brand"))
            self.saved = self._lbl("", name="Saved")
            barl.addWidget(self.saved)
            barl.addStretch(1)
            self.project_lbl = self._lbl(root.name, name="Meta")
            barl.addWidget(self.project_lbl)
            outer.addWidget(bar)

            # Conversation area.
            self.scroll = QtWidgets.QScrollArea()
            self.scroll.setWidgetResizable(True)
            self.thread_host = QtWidgets.QWidget()
            self.thread = QtWidgets.QVBoxLayout(self.thread_host)
            self.thread.setContentsMargins(18, 18, 18, 18)
            self.thread.setSpacing(12)
            self.thread.addStretch(1)
            self.scroll.setWidget(self.thread_host)
            outer.addWidget(self.scroll, 1)

            # Composer (the AI box).
            wrap = QtWidgets.QWidget()
            wl = QtWidgets.QVBoxLayout(wrap)
            wl.setContentsMargins(18, 6, 18, 16)
            box = QtWidgets.QFrame()
            box.setObjectName("Composer")
            bl = QtWidgets.QVBoxLayout(box)
            bl.setContentsMargins(10, 8, 10, 8)
            self.input = Composer()
            self.input.setObjectName("Input")
            self.input.setPlaceholderText(
                "Ask OPai to build, fix, or explain… (Enter to send, /tool for tools)"
            )
            self.input.setFixedHeight(74)
            self.input.submit.connect(self._send)
            bl.addWidget(self.input)
            row = QtWidgets.QHBoxLayout()
            self.model = QtWidgets.QComboBox()
            self.model.setMinimumWidth(240)
            row.addWidget(self.model)
            self.tools_btn = QtWidgets.QPushButton("Tools")
            self.tools_btn.setMenu(self._tools_menu())
            row.addWidget(self.tools_btn)
            row.addStretch(1)
            self.send_btn = QtWidgets.QPushButton("Send")
            self.send_btn.setObjectName("Send")
            self.send_btn.clicked.connect(self._send)
            row.addWidget(self.send_btn)
            bl.addLayout(row)
            wl.addWidget(box)
            outer.addWidget(wrap)
            self.setCentralWidget(central)

            self._empty = None
            self._load_models()
            self._refresh_header()
            self._show_empty()

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
            self.model.clear()
            for option in data["models"]:
                self.model.addItem(option["label"], option["id"])
            self._model_hint = data.get("hint")

        def _refresh_header(self) -> None:
            try:
                o = A.overview(self.root)
            except Exception:  # noqa: BLE001
                return
            on = bool(o.get("on"))
            self.dot.setStyleSheet(f"color:{GREEN if on else WARN}; font-size:15px;")
            self.dot.setToolTip("OPai ON" if on else "OPai needs attention")
            sav = o["savings"]
            self.saved.setText(
                f"${sav['estimated_savings_usd']:.2f} saved · {sav['cloud_calls_avoided']} paid calls avoided"
            )

        # -- conversation ---------------------------------------------------- #
        def _show_empty(self) -> None:
            self._empty = QtWidgets.QWidget()
            el = QtWidgets.QVBoxLayout(self._empty)
            el.setContentsMargins(0, 40, 0, 0)
            el.setAlignment(QtCore.Qt.AlignmentFlag.AlignHCenter)
            title = self._lbl("What do you want to build?")
            title.setStyleSheet("font-size:22px; font-weight:800;")
            title.setAlignment(QtCore.Qt.AlignmentFlag.AlignHCenter)
            el.addWidget(title)
            sub = self._lbl(
                "OPai routes to the cheapest safe model and runs it locally when it can.",
                name="Empty",
            )
            sub.setAlignment(QtCore.Qt.AlignmentFlag.AlignHCenter)
            el.addWidget(sub)
            chips = QtWidgets.QHBoxLayout()
            chips.setAlignment(QtCore.Qt.AlignmentFlag.AlignHCenter)
            for text in (
                "Summarize my changes",
                "Fix the failing test",
                "/savings",
                "/doctor",
            ):
                chip = QtWidgets.QPushButton(text)
                chip.setObjectName("Chip")
                chip.clicked.connect(lambda _c=False, t=text: self._chip(t))
                chips.addWidget(chip)
            el.addLayout(chips)
            if self._model_hint:
                hint = self._lbl(self._model_hint, name="Empty")
                hint.setAlignment(QtCore.Qt.AlignmentFlag.AlignHCenter)
                hint.setStyleSheet(f"color:{FAINT}; font-size:12px;")
                el.addWidget(hint)
            self.thread.insertWidget(self.thread.count() - 1, self._empty)

        def _clear_empty(self) -> None:
            if self._empty is not None:
                self._empty.setParent(None)
                self._empty = None

        def _bubble(self, object_name, title, body, *, meta=None, mono=False):
            self._clear_empty()
            frame = QtWidgets.QFrame()
            frame.setObjectName(object_name)
            fl = QtWidgets.QVBoxLayout(frame)
            fl.setContentsMargins(13, 10, 13, 10)
            fl.setSpacing(5)
            if title:
                t = self._lbl(title)
                t.setStyleSheet(f"color:{MUTED}; font-weight:700; font-size:12px;")
                fl.addWidget(t)
            body_lbl = self._lbl(body, name="Answer" if mono else None)
            body_lbl.setTextInteractionFlags(
                QtCore.Qt.TextInteractionFlag.TextSelectableByMouse
            )
            fl.addWidget(body_lbl)
            if meta:
                m = self._lbl(meta, name="BubbleMeta")
                fl.addWidget(m)
            self.thread.insertWidget(self.thread.count() - 1, frame)
            QtCore.QTimer.singleShot(30, self._to_bottom)
            return frame

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
            self.send_btn.setText("…" if on else "Send")

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
            model_id = self.model.currentData() or "auto"
            worker = Worker(lambda: A.ask(self.root, text, model_id))
            worker.done.connect(self._on_ask)
            self._workers.append(worker)
            worker.start()

        def _on_ask(self, result) -> None:
            self._busy(False)
            status = result.get("status")
            tier = result.get("tier", "")
            if status in ("answered_locally", "cache_hit"):
                src = (
                    "cache"
                    if status == "cache_hit"
                    else (result.get("runner") or "local")
                )
                model = result.get("model", "")
                meta = f"{tier} · {src} · free" + (f" · {model}" if model else "")
                self._bubble(
                    "OpaiBubble", None, result.get("answer", ""), meta=meta, mono=True
                )
            elif status == "no_local_model":
                self._bubble(
                    "OpaiBubble",
                    "No local model connected",
                    result.get("hint", "")
                    + "\n\n"
                    + (result.get("next_command", "") or ""),
                    meta=f"{tier} · OPai would route here",
                )
            elif status == "confirmation_required":
                self._bubble(
                    "OpaiBubble",
                    "Cloud tier recommended",
                    "OPai won't spend automatically. Run this in your AI agent, or "
                    "connect a local model (Tools ▸ pick a model) to do it free here.",
                    meta=f"{tier} · paid/cloud — gated",
                )
            else:
                self._bubble(
                    "OpaiBubble",
                    "Error",
                    str(result.get("answer") or result),
                    meta=status,
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
                        "ToolBubble", result.get("title"), applied.get("text", "done")
                    )
                else:
                    self._bubble("ToolBubble", result.get("title"), "Cancelled.")
            else:
                self._bubble(
                    "ToolBubble",
                    result.get("title", "Tool"),
                    result.get("text", ""),
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
