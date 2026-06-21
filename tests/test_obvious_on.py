import contextlib
import io
import json
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path

from opai.cli import main
from opai.cockpit import build_cockpit, render_cockpit
from opai.integrations import activate_project, render_statusline
from opai.visibility import write_visibility_status
from opaihub.dashboard_html import build_dashboard_html


class CockpitTests(unittest.TestCase):
    def test_cockpit_renders_obvious_on_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            activate_project(root, install_global=False)

            payload = build_cockpit(root)
            text = render_cockpit(payload)

        self.assertEqual(payload["status"], "on")
        self.assertIn("OPai ON", text)
        self.assertIn("Clients: 5/5 active", text)
        self.assertIn("Savings:", text)
        self.assertIn("Budget:", text)
        self.assertIn("Run `opai route", text)

    def test_status_human_aliases_cockpit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            activate_project(root, install_global=False)

            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = main(["status", "--project", str(root), "--human"])

        self.assertEqual(code, 0)
        self.assertIn("OPai ON", out.getvalue())

    def test_cockpit_command_supports_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            activate_project(root, install_global=False)

            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = main(["cockpit", "--project", str(root), "--json"])

        self.assertEqual(code, 0)
        payload = json.loads(out.getvalue())
        self.assertEqual(payload["report"], "opai-cockpit")
        self.assertEqual(payload["status"], "on")

    def test_project_statusline_is_compact_and_human(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            activate_project(root, install_global=False)

            line = render_statusline(project_root=root, width=120, color=False)

        self.assertIn("OPai ON", line)
        self.assertIn("5/5 clients", line)
        self.assertIn("$0.00 saved", line)
        self.assertIn("budget ok", line)


class VisibilityTests(unittest.TestCase):
    def test_visibility_install_writes_safe_status_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            activate_project(root, install_global=False)

            result = write_visibility_status(root)
            markdown = (root / "OPAI_STATUS.md").read_text(encoding="utf-8")
            payload = json.loads(
                (root / ".opaihub" / "opai-status.json").read_text(encoding="utf-8")
            )

        self.assertEqual(result["status"], "installed")
        self.assertEqual(payload["status"], "on")
        self.assertIn("OPai is active", markdown)
        self.assertIn("opai cockpit", markdown)
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
        self.assertIn("OPAI_STATUS.md", out.getvalue())

    def test_visibility_ignores_local_status_and_proof_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git" / "info").mkdir(parents=True)
            activate_project(root, install_global=False)

            write_visibility_status(root)
            exclude = (root / ".git" / "info" / "exclude").read_text(encoding="utf-8")

        self.assertIn("OPAI_STATUS.md", exclude)
        self.assertIn(".opaihub/", exclude)
        self.assertIn(".opaihub/opai-status.json", exclude)
        self.assertIn(".opaihub/dashboard.html", exclude)
        self.assertIn(".opaihub/benchmarks/", exclude)

    def test_visibility_exclude_uses_exact_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            info = root / ".git" / "info"
            info.mkdir(parents=True)
            (info / "exclude").write_text(
                "# existing\n.opaihub/opai-status.json\n",
                encoding="utf-8",
            )
            activate_project(root, install_global=False)

            write_visibility_status(root)
            exclude = (info / "exclude").read_text(encoding="utf-8")

        self.assertIn(".opaihub/opai-status.json", exclude)
        self.assertIn(".opaihub/", exclude)

    def test_activate_repair_writes_visibility_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = main(["activate", "--repair", "--project", str(root)])

            self.assertEqual(code, 0)
            payload = json.loads(out.getvalue())
            self.assertIn("visibility", payload)
            self.assertTrue((root / "OPAI_STATUS.md").exists())
            self.assertTrue((root / ".opaihub" / "opai-status.json").exists())


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
                    "opai",
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
