from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

try:
    from _helpers import FakeAccountRunner, make_repo
except (
    ModuleNotFoundError
):  # direct: python -m unittest tests.test_savings_dashboard_playwright
    from tests._helpers import FakeAccountRunner, make_repo
from opaihub.gui_pipeline import handle_gui_message
from opaihub.ledger import record_route_decision


@unittest.skipUnless(
    os.environ.get("OPAI_E2E_PLAYWRIGHT") == "1",
    "set OPAI_E2E_PLAYWRIGHT=1 to run browser dashboard E2E",
)
class SavingsDashboardPlaywrightTests(unittest.TestCase):
    def test_dashboard_shows_truthful_savings_without_prompt_leakage(self) -> None:
        npx = shutil.which("npx")
        npm = shutil.which("npm")
        if npx is None or npm is None:
            self.skipTest("npm/npx are required for the Playwright E2E")

        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp))
            secret_prompt = "local route SECRET token=sk-e2e1234567890"
            record_route_decision(root, secret_prompt, model_tier="L1")
            handle_gui_message(
                root,
                "paid account SECRET token=sk-paid1234567890",
                model_id="account:claude:sonnet",
                mode="ask",
                account_runner=FakeAccountRunner(cost=0.042),
            )

            env = os.environ.copy()
            env["PYTHONUNBUFFERED"] = "1"
            server = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "opai",
                    "dashboard",
                    "--serve",
                    "--port",
                    "0",
                    "--project",
                    str(root),
                ],
                cwd=Path(__file__).resolve().parents[1],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                buffer = ""
                payload = None
                for _ in range(80):
                    line = server.stdout.readline() if server.stdout is not None else ""
                    if not line:
                        break
                    buffer += line
                    try:
                        payload = json.loads(buffer)
                        break
                    except json.JSONDecodeError:
                        continue
                if payload is None:
                    stderr = server.stderr.read() if server.stderr is not None else ""
                    self.fail(
                        "dashboard server did not emit a parseable URL payload: "
                        f"{buffer}\n{stderr}"
                    )
                self.assertEqual(payload["status"], "serving")
                url = payload["url"]

                out_dir = root / ".opaihub" / "e2e"
                out_dir.mkdir(parents=True, exist_ok=True)
                script = out_dir / "dashboard-e2e.spec.mjs"
                screenshot = out_dir / "dashboard-savings-truth.png"
                script.write_text(
                    textwrap.dedent(
                        f"""
                        import {{ test, expect }} from '@playwright/test';

                        test('dashboard shows truthful savings', async ({{ page }}) => {{
                          await page.setViewportSize({{ width: 1280, height: 820 }});
                          await page.goto({json.dumps(url)}, {{ waitUntil: 'networkidle' }});
                          const body = await page.textContent('body');
                          const required = [
                            'Saved total',
                            'Spent today',
                            'Paid calls avoided',
                            'Context reduced',
                            'Latest receipt',
                            'Savings receipt',
                            'Local OPai benchmark suite result',
                          ];
                          for (const text of required) {{
                            expect(body).toContain(text);
                          }}
                          const forbidden = ['sk-e2e1234567890', 'sk-paid1234567890', 'SECRET'];
                          for (const text of forbidden) {{
                            expect(body).not.toContain(text);
                          }}
                          await page.screenshot({{ path: {json.dumps(str(screenshot))}, fullPage: true }});
                        }});
                        """
                    ).strip(),
                    encoding="utf-8",
                )
                install = subprocess.run(
                    [npm, "install", "--no-audit", "--no-fund", "@playwright/test"],
                    cwd=out_dir,
                    capture_output=True,
                    text=True,
                    timeout=180,
                )
                if install.returncode != 0:
                    self.fail(install.stdout + install.stderr)
                browser_install = subprocess.run(
                    [npx, "playwright", "install", "chromium"],
                    cwd=out_dir,
                    capture_output=True,
                    text=True,
                    timeout=240,
                )
                if browser_install.returncode != 0:
                    self.fail(browser_install.stdout + browser_install.stderr)
                result = subprocess.run(
                    [
                        npx,
                        "playwright",
                        "test",
                        script.name,
                        "--browser=chromium",
                        "--reporter=line",
                    ],
                    cwd=out_dir,
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                if result.returncode != 0:
                    combined = result.stdout + result.stderr
                    if (
                        "Executable doesn't exist" in combined
                        or "Please run" in combined
                        or "playwright install" in combined
                    ):
                        self.skipTest(
                            "Playwright browser is not installed; run `npx playwright install chromium`."
                        )
                    self.fail(combined)
                self.assertGreater(screenshot.stat().st_size, 1000)
            finally:
                server.terminate()
                try:
                    server.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    server.kill()
                if server.stdout is not None:
                    server.stdout.close()
                if server.stderr is not None:
                    server.stderr.close()


if __name__ == "__main__":
    unittest.main()
