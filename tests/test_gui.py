import json
import subprocess
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

from opai.gui import gui_html, handle_api, make_server


def _repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True, capture_output=True)
    (root / "pyproject.toml").write_text("[project]\n", encoding="utf-8")


class GuiHtmlTests(unittest.TestCase):
    def setUp(self):
        self.html = gui_html()

    def test_is_code_only_no_chat_no_cowork(self):
        self.assertIn("Coding Cockpit", self.html)
        lowered = self.html.lower()
        self.assertNotIn("cowork", lowered)
        # No conversational chat surface.
        self.assertNotIn(">chat<", lowered)

    def test_is_self_contained_no_external_loads(self):
        lowered = self.html.lower()
        self.assertNotIn('src="http', lowered)
        self.assertNotIn("googleapis", lowered)
        self.assertNotIn('rel="stylesheet"', lowered)


class HandleApiTests(unittest.TestCase):
    def test_status_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _repo(root)
            status, data = handle_api(root, "GET", "/api/status", {})
        self.assertEqual(status, 200)
        for key in ["version", "project", "edition", "clients", "savings", "budget"]:
            self.assertIn(key, data)

    def test_route_is_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _repo(root)
            status, data = handle_api(
                root, "POST", "/api/route", {"task": "show git status"}
            )
            self.assertEqual(status, 200)
            self.assertEqual(data["tier"], "L0")
            self.assertFalse((root / ".opaihub" / "ledger").exists())

    def test_record_writes_and_savings_reflects(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _repo(root)
            handle_api(root, "POST", "/api/record", {"task": "fix bug"})
            status, data = handle_api(root, "GET", "/api/savings", {})
        self.assertEqual(data["totals"]["routed_tasks"], 1)

    def test_ask_degrades_without_local_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _repo(root)
            # No Ollama in CI -> graceful no_local_model (no network hang beyond probe).
            status, data = handle_api(
                root, "POST", "/api/ask", {"task": "summarize the diff"}
            )
        self.assertEqual(status, 200)
        self.assertIn(
            data["status"], {"no_local_model", "answered_locally", "cache_hit"}
        )

    def test_panic_toggle(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _repo(root)
            status, data = handle_api(root, "POST", "/api/budget/panic", {"on": True})
        self.assertTrue(data["panic"])

    def test_missing_task_is_400(self):
        with tempfile.TemporaryDirectory() as tmp:
            status, data = handle_api(Path(tmp), "POST", "/api/route", {})
        self.assertEqual(status, 400)

    def test_unknown_path_is_404(self):
        with tempfile.TemporaryDirectory() as tmp:
            status, _ = handle_api(Path(tmp), "GET", "/api/nope", {})
        self.assertEqual(status, 404)


class ServerTests(unittest.TestCase):
    def test_server_serves_html_and_api_on_localhost(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _repo(root)
            server = make_server(root, host="127.0.0.1", port=0)
            port = server.server_address[1]
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/", timeout=5
                ) as resp:
                    html = resp.read().decode("utf-8")
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/api/status", timeout=5
                ) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()
        self.assertIn("Coding Cockpit", html)
        self.assertEqual(data["project"], str(root.resolve()))


if __name__ == "__main__":
    unittest.main()
