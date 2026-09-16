"""QtWebEngine egress lockdown (#149): source-level security contract.

The render surface shows untrusted model output next to a privileged
bridge, so its lockdown must not silently regress: no remote URL access,
no blanket clipboard permission, a deny-by-default CSP, and copy going
through the write-only bridge slot. Behavior is exercised by the
Playwright suite (security-egress.spec.js); these tests pin the shipped
configuration itself.
"""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GUI_WEB = ROOT / "vesta" / "gui_web.py"
INDEX_HTML = ROOT / "vesta" / "assets" / "web" / "index.html"
APP_JS = ROOT / "vesta" / "assets" / "web" / "app.js"
MARKDOWN_RENDERER_JS = ROOT / "vesta" / "assets" / "web" / "markdown-renderer.js"


class WebEngineLockdownTests(unittest.TestCase):
    def test_local_content_cannot_reach_remote_urls(self):
        source = GUI_WEB.read_text(encoding="utf-8")
        self.assertIn("LocalContentCanAccessRemoteUrls, False", source)
        self.assertNotIn("LocalContentCanAccessRemoteUrls, True", source)

    def test_blanket_clipboard_permission_stays_off(self):
        source = GUI_WEB.read_text(encoding="utf-8")
        self.assertIn("JavascriptCanAccessClipboard, False", source)
        self.assertNotIn("JavascriptCanAccessClipboard, True", source)

    def test_index_ships_a_deny_by_default_csp(self):
        html = INDEX_HTML.read_text(encoding="utf-8")
        marker = 'Content-Security-Policy" content="'
        self.assertIn(marker, html)
        csp = html.split(marker, 1)[1].split('"', 1)[0]
        self.assertIn("default-src 'none'", csp)
        self.assertIn("base-uri 'none'", csp)
        self.assertIn("form-action 'none'", csp)
        # No connect-src at all: fetch/XHR/WebSocket fall back to
        # default-src 'none', so nothing can leave the page.
        self.assertNotIn("connect-src", csp)
        for scheme in ("http://", "https://", "ws:", "wss:", "*"):
            self.assertNotIn(
                scheme, csp, f"CSP must not allow remote scheme {scheme!r}"
            )

    def test_copy_goes_through_the_write_only_bridge(self):
        app_js = APP_JS.read_text(encoding="utf-8")
        self.assertIn("function copyText", app_js)
        self.assertIn("bridge.copyText", app_js)
        # Exactly one direct clipboard write remains: the fallback inside the
        # copyText helper for non-Qt (test/browser) contexts.
        self.assertEqual(app_js.count("navigator.clipboard.writeText"), 1)

        gui = GUI_WEB.read_text(encoding="utf-8")
        self.assertIn("def copyText", gui)
        self.assertNotIn("QClipboard.Mode.Selection", gui)

    def test_external_links_stay_on_the_validated_bridge_slot(self):
        gui = GUI_WEB.read_text(encoding="utf-8")
        self.assertIn("def openExternal", gui)
        self.assertIn('str(url).startswith(("http://", "https://"))', gui)

    def test_markdown_renderer_is_local_and_fail_closed(self):
        html = INDEX_HTML.read_text(encoding="utf-8")
        renderer = MARKDOWN_RENDERER_JS.read_text(encoding="utf-8")
        self.assertIn('src="vendor/markdown-it-14.1.0.min.js"', html)
        self.assertIn("html: false", renderer)
        self.assertIn("linkify: false", renderer)
        self.assertIn("maxNesting: 20", renderer)
        self.assertIn("renderer.rules.image", renderer)
        self.assertIn('token.attrSet("data-ext", "1")', renderer)
        self.assertNotIn("http://cdn", html.lower())
        self.assertNotIn("https://cdn", html.lower())


if __name__ == "__main__":
    unittest.main()
