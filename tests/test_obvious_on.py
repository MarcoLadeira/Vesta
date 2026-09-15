import contextlib
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

from vesta.cli import main
from vesta.cockpit import build_cockpit, render_cockpit
from vesta.integrations import activate_project, render_statusline
from vesta.visibility import write_visibility_status
from vestahub.dashboard_html import build_dashboard_html


class CockpitTests(unittest.TestCase):
    def setUp(self):
        # Hermetic home: a client's global-discovery files must not depend on
        # (or pollute) the developer's real ~/. Without this, claude/codex/
        # copilot read as "broken" on a clean machine (CI) but "active" locally,
        # making the cockpit's ON state non-deterministic across environments.
        self._home = tempfile.TemporaryDirectory()
        self._env = mock.patch.dict(
            os.environ, {"HOME": self._home.name, "USERPROFILE": self._home.name}
        )
        self._env.start()

    def tearDown(self):
        self._env.stop()
        self._home.cleanup()

    def test_cockpit_renders_obvious_on_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            activate_project(root, install_global=True)

            payload = build_cockpit(root)
            text = render_cockpit(payload)

        self.assertEqual(payload["status"], "on")
        self.assertIn("Vesta ON", text)
        self.assertIn("Clients: 6/6 active", text)
        self.assertIn("Savings:", text)
        self.assertIn("Budget:", text)
        self.assertIn("Run `vesta route", text)

    def test_status_human_aliases_cockpit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            activate_project(root, install_global=True)

            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = main(["status", "--project", str(root), "--human"])

        self.assertEqual(code, 0)
        self.assertIn("Vesta ON", out.getvalue())

    def test_cockpit_command_supports_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            activate_project(root, install_global=True)

            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = main(["cockpit", "--project", str(root), "--json"])

        self.assertEqual(code, 0)
        payload = json.loads(out.getvalue())
        self.assertEqual(payload["report"], "vesta-cockpit")
        self.assertEqual(payload["status"], "on")

    def test_project_statusline_is_compact_and_human(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            activate_project(root, install_global=True)

            line = render_statusline(project_root=root, width=120, color=False)

        self.assertIn("Vesta ON", line)
        self.assertIn("6/6 clients", line)
        self.assertIn("$0.00 saved", line)
        self.assertIn("budget ok", line)

    def test_cockpit_uses_activation_home_after_process_home_changes(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            tempfile.TemporaryDirectory() as active_home_tmp,
            tempfile.TemporaryDirectory() as other_home_tmp,
        ):
            root = Path(tmp)
            active_home = Path(active_home_tmp)
            other_home = Path(other_home_tmp)
            activate_project(root, home=active_home, install_global=True)

            with mock.patch("pathlib.Path.home", return_value=other_home):
                payload = build_cockpit(root)

        self.assertEqual(payload["status"], "on")
        self.assertEqual(
            set(payload["clients"]["summary"]["active"]),
            {"claude", "codex", "copilot", "gemini", "cursor", "cline"},
        )


class VisibilityTests(unittest.TestCase):
    def test_visibility_install_writes_safe_status_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            activate_project(root, install_global=False)

            result = write_visibility_status(root)
            markdown = (root / "VESTA_STATUS.md").read_text(encoding="utf-8")
            payload = json.loads(
                (root / ".vestahub" / "vesta-status.json").read_text(encoding="utf-8")
            )

        self.assertEqual(result["status"], "installed")
        self.assertEqual(payload["status"], "on")
        self.assertIn("Vesta is active", markdown)
        self.assertIn("vesta cockpit", markdown)
        self.assertNotIn("sk-", markdown)
        self.assertNotIn("raw_prompt", markdown.lower())

    def test_visibility_cli_install(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            activate_project(root, install_global=False)

            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = main(["visibility", "install", "--project", str(root)])

        self.assertEqual(code, 0)
        self.assertIn("VESTA_STATUS.md", out.getvalue())

    def test_visibility_ignores_local_status_and_proof_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git" / "info").mkdir(parents=True)
            activate_project(root, install_global=False)

            write_visibility_status(root)
            exclude = (root / ".git" / "info" / "exclude").read_text(encoding="utf-8")

        self.assertIn("VESTA_STATUS.md", exclude)
        self.assertIn(".vestahub/", exclude)
        self.assertIn(".vestahub/vesta-status.json", exclude)
        self.assertIn(".vestahub/dashboard.html", exclude)
        self.assertIn(".vestahub/benchmarks/", exclude)

    def test_visibility_exclude_uses_exact_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            info = root / ".git" / "info"
            info.mkdir(parents=True)
            (info / "exclude").write_text(
                "# existing\n.vestahub/vesta-status.json\n",
                encoding="utf-8",
            )
            activate_project(root, install_global=False)

            write_visibility_status(root)
            exclude = (info / "exclude").read_text(encoding="utf-8")

        self.assertIn(".vestahub/vesta-status.json", exclude)
        self.assertIn(".vestahub/", exclude)

    def test_activate_repair_writes_visibility_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = main(["activate", "--repair", "--project", str(root)])

            self.assertEqual(code, 0)
            payload = json.loads(out.getvalue())
            self.assertIn("visibility", payload)
            self.assertTrue((root / "VESTA_STATUS.md").exists())
            self.assertTrue((root / ".vestahub" / "vesta-status.json").exists())


class DashboardVisibilityTests(unittest.TestCase):
    def test_html_dashboard_is_control_center(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            activate_project(root, install_global=False)

            path = build_dashboard_html(root)
            html = path.read_text(encoding="utf-8")

        for label in [
            "Activation",
            "Clients",
            "Savings Ledger",
            "Budget Firewall",
            "Context Waste",
            "Benchmark Proof",
            "Proof Bundle",
            "Local Model / Ask",
            "Launch Readiness",
        ]:
            self.assertIn(label, html)
        self.assertIn("No real routed tasks recorded yet", html)
        self.assertNotIn("issues/new", html)
        self.assertIn("Free public alpha", html)
        self.assertNotIn("private checkout", html.lower())

    def test_dashboard_serve_root_opens_control_center(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]

            proc = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "vesta",
                    "dashboard",
                    "--serve",
                    "--port",
                    str(port),
                    "--project",
                    str(root),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                body = ""
                for _ in range(40):
                    if proc.poll() is not None:
                        stdout, stderr = proc.communicate()
                        self.fail(
                            "dashboard server exited early: "
                            f"stdout={stdout} stderr={stderr}"
                        )
                    try:
                        with urllib.request.urlopen(
                            f"http://127.0.0.1:{port}/", timeout=0.5
                        ) as response:
                            body = response.read().decode("utf-8")
                            break
                    except OSError:
                        time.sleep(0.1)
                else:
                    self.fail("dashboard server did not respond on localhost")
            finally:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
                if proc.stdout:
                    proc.stdout.close()
                if proc.stderr:
                    proc.stderr.close()

        self.assertIn("Activation", body)
        self.assertIn("Savings Ledger", body)
        self.assertIn("Launch Readiness", body)


if __name__ == "__main__":
    unittest.main()
