"""OPai coding GUI: a local, code-only command center served over localhost.

`opai gui` starts a stdlib HTTP server (127.0.0.1 only, no third-party deps, no
telemetry) and serves a self-contained single-page app. The app is *code-only* -
no chat, no cowork - it surfaces OPai's coding workflow: route a task, run it
locally for free, watch the cost firewall, profile context waste, and check
client readiness.

The JSON API exposes only OPai's safe surfaces (route is read-only; ask runs a
local model; budget panic flips a flag). There is no raw shell or arbitrary file
access, and the project root is fixed when the server starts.
"""

from __future__ import annotations

import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from typing import Any


def gui_html() -> str:
    return (
        resources.files("opai")
        .joinpath("assets", "gui.html")
        .read_text(encoding="utf-8")
    )


def _status(root: Path) -> dict[str, Any]:
    from opai import __release_stage__, __version__
    from opai.integrations import project_status
    from opaihub.budget import budget_status
    from opaihub.editions import current_edition
    from opaihub.savings import build_savings_report

    status = project_status(root)
    clients = status["client_integrations"]["summary"]
    savings = build_savings_report(root)["totals"]
    return {
        "version": __version__,
        "release_stage": __release_stage__,
        "project": str(root),
        "edition": current_edition(root),
        "clients": {
            "active": clients["active"],
            "broken": clients["broken"],
            "missing": clients["missing"],
        },
        "savings": savings,
        "budget": budget_status(root),
        "stale_paths_ok": status["stale_paths"]["ok"],
    }


def handle_api(
    project_root: Path, method: str, path: str, body: dict[str, Any]
) -> tuple[int, dict[str, Any]]:
    """Pure API dispatch (no socket) - the testable core of the GUI server."""
    root = project_root.expanduser().resolve()
    task = str(body.get("task", "")).strip()

    if path == "/api/status" and method == "GET":
        return 200, _status(root)

    if path == "/api/route" and method == "POST":
        from opaihub.router import compact_decision, route_task

        if not task:
            return 400, {"error": "task required"}
        decision = route_task(root, task, include_evidence=True)
        compact = compact_decision(decision)
        compact["requires_confirmation"] = decision.get("requires_confirmation")
        compact["policy_decision"] = decision.get("policy_decision")
        return 200, compact

    if path == "/api/why" and method == "POST":
        from opaihub.runs import explain_route

        if not task:
            return 400, {"error": "task required"}
        return 200, explain_route(root, task)

    if path == "/api/ask" and method == "POST":
        from opaihub.ask import run_ask

        if not task:
            return 400, {"error": "task required"}
        return 200, run_ask(root, task, record=bool(body.get("record", True)))

    if path == "/api/record" and method == "POST":
        from opaihub.ledger import record_route_decision
        from opaihub.router import route_context_sizes, route_task

        if not task:
            return 400, {"error": "task required"}
        decision = route_task(root, task, include_evidence=True, persist_cache=True)
        sizes = route_context_sizes(decision)
        event = record_route_decision(
            root,
            task,
            model_tier=decision["model_tier"],
            workflow=decision["workflow"],
            full_context_chars=sizes["full_chars"],
            compact_context_chars=sizes["compact_chars"],
            cache_hit=decision.get("evidence_cache_hit", False),
            source="gui",
        )
        return 200, {
            "recorded": True,
            "tier": event["model_tier"],
            "estimated_savings_usd": event["estimated_savings_usd"],
        }

    if path == "/api/savings" and method == "GET":
        from opaihub.savings import build_savings_report

        return 200, build_savings_report(root)

    if path == "/api/context" and method == "GET":
        from opaihub.context_engine import profile_context

        return 200, profile_context(root)

    if path == "/api/budget" and method == "GET":
        from opaihub.budget import budget_status

        return 200, budget_status(root)

    if path == "/api/budget/panic" and method == "POST":
        from opaihub.budget import budget_status, set_budget

        set_budget(root, panic=bool(body.get("on", True)))
        return 200, budget_status(root)

    return 404, {"error": "not found", "path": path}


class _Handler(BaseHTTPRequestHandler):
    project_root: Path = Path(".")

    def log_message(self, *args: Any) -> None:  # keep the console quiet
        return

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status: int, data: dict[str, Any]) -> None:
        self._send(status, json.dumps(data).encode("utf-8"), "application/json")

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            self._send(200, gui_html().encode("utf-8"), "text/html; charset=utf-8")
            return
        if path.startswith("/api/"):
            status, data = handle_api(self.project_root, "GET", path, {})
            self._send_json(status, data)
            return
        self._send_json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            body = {}
        status, data = handle_api(self.project_root, "POST", path, body)
        self._send_json(status, data)


def make_server(
    project_root: Path, host: str = "127.0.0.1", port: int = 0
) -> ThreadingHTTPServer:
    handler = type(
        "OPaiGUIHandler",
        (_Handler,),
        {"project_root": project_root.expanduser().resolve()},
    )
    return ThreadingHTTPServer((host, port), handler)


def start_gui_server(
    project_root: Path,
    *,
    host: str = "127.0.0.1",
    port: int = 0,
    open_browser: bool = True,
) -> None:
    server = make_server(project_root, host=host, port=port)
    url = f"http://{host}:{server.server_address[1]}"
    print(f"OPai coding GUI -> {url}")
    print(f"Project: {project_root.expanduser().resolve()}")
    print("Local-only, code-only. Press Ctrl+C to stop.")
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nOPai GUI stopped.")
    finally:
        server.server_close()
