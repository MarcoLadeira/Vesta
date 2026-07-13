"""Tests for the web-UI bridge data layer + bundled assets (no Chromium needed).

The rendering lives in Chromium (QtWebEngine), which isn't headlessly testable
here, so these lock the *contract* the front-end relies on: the JSON the bridge
hands the page is complete, serializable, and leaks no secrets — and the bundled
assets reference the font and bridge correctly.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from _helpers import make_repo

from opai.gui_web import (
    WEB_DIR,
    boot_payload,
    resolve_openable,
    settings_payload,
    web_available,
)


class WebAvailableTests(unittest.TestCase):
    def test_returns_bool(self):
        self.assertIsInstance(web_available(), bool)


class ResolveOpenableTests(unittest.TestCase):
    """The bridge may only open the project root or paths under it — never an
    arbitrary path handed in by the front-end."""

    def test_root_itself_is_openable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp))
            self.assertEqual(resolve_openable(root, ""), root.resolve())

    def test_relative_file_under_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp))
            (root / "app.py").write_text("x", encoding="utf-8")
            got = resolve_openable(root, "app.py")
            self.assertEqual(got, (root / "app.py").resolve())

    def test_missing_path_is_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp))
            self.assertIsNone(resolve_openable(root, "nope.py"))

    def test_escape_outside_root_is_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp))
            self.assertIsNone(resolve_openable(root, "../../etc/passwd"))
            self.assertIsNone(resolve_openable(root, str(Path(tmp).parent)))


class BootPayloadTests(unittest.TestCase):
    def _boot(self, root):
        return boot_payload(root, initial_task="fix login")

    def test_has_all_shell_keys_and_is_serializable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp))
            payload = self._boot(root)
        blob = json.dumps(payload)  # must not raise
        for key in (
            "workspace",
            "models",
            "modes",
            "navGroups",
            "taskModes",
            "outputFormats",
            "prefs",
            "status",
            "inspector",
            "tools",
            "recents",
        ):
            self.assertIn(key, payload)
        self.assertEqual(payload["initialTask"], "fix login")
        self.assertGreater(len(blob), 100)

    def test_nav_groups_are_simple_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp))
            payload = self._boot(root)
        # Simple top level (unlabeled) + one folded Insights group.
        self.assertEqual(
            [g["group"] for g in payload["navGroups"]],
            ["", "Insights"],
        )
        by_name = {g["group"]: g for g in payload["navGroups"]}
        self.assertFalse(by_name[""].get("collapsed"))
        self.assertTrue(by_name["Insights"]["collapsed"])

    def test_models_carry_badges_and_auto_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp))
            payload = self._boot(root)
        ids = [m["id"] for m in payload["models"]]
        self.assertIn("auto", ids)
        for m in payload["models"]:
            self.assertIn("badge", m)

    def test_inspector_is_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp))
            ins = self._boot(root)["inspector"]
        labels = {r["label"] for r in ins["rows"]}
        for needed in ("Model", "Run mode", "Workspace", "Permissions"):
            self.assertIn(needed, labels)
        self.assertEqual(len(ins["permissions"]), 8)
        self.assertTrue(ins["privacy"])
        self.assertIn("pct", ins["budget"])

    def test_workspace_has_root_and_recents_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp))
            ws = self._boot(root)["workspace"]
        self.assertEqual(ws["root"], str(root.resolve()))
        self.assertIsInstance(ws["recents"], list)

    def test_status_line_is_a_string(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp))
            payload = self._boot(root)
        self.assertIsInstance(payload["status"]["line"], str)
        self.assertIn("Auto", payload["status"]["line"])

    def test_boot_payload_never_leaks_a_recorded_secret(self):
        from opaihub.ledger import record_route_decision

        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp))
            record_route_decision(
                root,
                "deploy with SECRET token=sk-abcdef1234567890abcd",
                model_tier="L0",
            )
            blob = json.dumps(boot_payload(root))
        self.assertNotIn("SECRET", blob)
        self.assertNotIn("sk-abcdef1234567890abcd", blob)


class WebAssetsTests(unittest.TestCase):
    def test_core_assets_exist(self):
        for name in ("index.html", "styles.css", "app.js"):
            self.assertTrue((WEB_DIR / name).exists(), name)

    def test_index_wires_bridge_and_assets(self):
        html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
        self.assertIn("qwebchannel.js", html)
        self.assertIn("styles.css", html)
        self.assertIn("app.js", html)

    def test_index_uses_one_unified_desktop_header(self):
        html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
        self.assertEqual(html.count('id="appHeader"'), 1)
        self.assertNotIn('<header class="header">', html)
        for control in (
            "sidebarToggle",
            "headerNewChat",
            "headerSettings",
            "panelToggle",
            "windowControls",
        ):
            self.assertIn(f'id="{control}"', html)

    def test_styles_load_bundled_inter(self):
        css = (WEB_DIR / "styles.css").read_text(encoding="utf-8")
        self.assertIn("@font-face", css)
        self.assertIn("Inter-Variable.ttf", css)
        self.assertIn("-webkit-font-smoothing", css)
        font = WEB_DIR.parent / "fonts" / "Inter-Variable.ttf"
        self.assertTrue(font.exists())

    def test_app_js_uses_qwebchannel_and_bridge(self):
        js = (WEB_DIR / "app.js").read_text(encoding="utf-8")
        self.assertIn("QWebChannel", js)
        self.assertIn("channel.objects.bridge", js)


class SettingsPayloadTests(unittest.TestCase):
    def test_settings_exposes_normalized_connections(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp))
            with mock.patch(
                "opaihub.accounts._account_cli_version",
                side_effect=AssertionError("synchronous CLI version lookup"),
            ):
                payload = settings_payload(root)

        self.assertIn("connections", payload)
        self.assertEqual(
            {"claude", "codex", "copilot"},
            {connection["providerId"] for connection in payload["connections"]},
        )
        for connection in payload["connections"]:
            self.assertIn("authStatus", connection)
            self.assertIn("credentialSource", connection)
            self.assertNotIn("cli_path", connection)
        self.assertIn("connectionDoctor", payload)
        self.assertEqual(
            # github joined via the git/PR connector credential (GITHUB_TOKEN).
            {"claude", "codex", "copilot", "gemini", "groq", "mistral", "github"},
            {item["providerId"] for item in payload["connectionDoctor"]},
        )
        self.assertNotIn("cli_path", json.dumps(payload["connectionDoctor"]))
        json.dumps(payload)

    def test_settings_exposes_one_capability_truth(self):
        # #168: the picker/settings/doctor read one provider capability table,
        # and every doctor entry carries the canonical health state.
        from opaihub.provider_capabilities import ProviderHealth, all_provider_profiles

        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp))
            with mock.patch(
                "opaihub.accounts._account_cli_version",
                side_effect=AssertionError("synchronous CLI version lookup"),
            ):
                payload = settings_payload(root)

        self.assertEqual(payload["providerProfiles"], all_provider_profiles())
        valid = {h.value for h in ProviderHealth}
        for entry in payload["connectionDoctor"]:
            self.assertIn(entry["healthState"], valid)


class ScaffoldAppPayloadTests(unittest.TestCase):
    """GUI "New app" contract (#276): the Bridge slot's Qt-free core."""

    def test_scaffolds_under_the_workspace_root(self):
        from opai.gui_web import scaffold_app_payload

        with tempfile.TemporaryDirectory() as tmp:
            payload = scaffold_app_payload(
                Path(tmp), json.dumps({"description": "a todo app"})
            )
            self.assertTrue(payload["ok"], payload)
            self.assertEqual(payload["name"], "a-todo-app")
            self.assertTrue((Path(payload["root"]) / "index.html").exists())
            self.assertGreater(payload["boilerplate_tokens_avoided"], 0)
            json.dumps(payload)  # serializable for the wire

    def test_empty_description_is_a_clean_error(self):
        from opai.gui_web import scaffold_app_payload

        with tempfile.TemporaryDirectory() as tmp:
            payload = scaffold_app_payload(Path(tmp), json.dumps({"description": ""}))
            self.assertFalse(payload["ok"])
            self.assertIn("Describe", payload["error"])

    def test_clobber_and_malformed_json_never_raise(self):
        from opai.gui_web import scaffold_app_payload

        with tempfile.TemporaryDirectory() as tmp:
            taken = Path(tmp) / "taken"
            taken.mkdir()
            (taken / "f.txt").write_text("x", encoding="utf-8")
            payload = scaffold_app_payload(
                Path(tmp), json.dumps({"description": "taken"})
            )
            self.assertFalse(payload["ok"])
            self.assertIn("exists", payload["error"])
            self.assertFalse(scaffold_app_payload(Path(tmp), "{not json")["ok"])

    def test_app_receipt_payload_round_trip(self):
        from opai.gui_web import app_receipt_payload, scaffold_app_payload

        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(app_receipt_payload(Path(tmp))["status"], "not_an_app")
            created = scaffold_app_payload(
                Path(tmp), json.dumps({"description": "an app"})
            )
            receipt = app_receipt_payload(Path(created["root"]))
            self.assertTrue(receipt["ok"])
            self.assertEqual(receipt["builds"], 0)
            self.assertGreater(receipt["tokens_never_sent"], 0)

    def test_boot_flags_a_scaffolded_app_for_build_mode(self):
        # #276: the workspace payload tells the GUI to offer Build mode.
        from _helpers import make_repo

        from opai.gui_web import boot_payload, scaffold_app_payload

        with tempfile.TemporaryDirectory() as tmp:
            plain_dir = Path(tmp) / "plain"
            plain_dir.mkdir()
            plain = make_repo(plain_dir)
            self.assertFalse(boot_payload(plain)["workspace"]["build_app"])

        with tempfile.TemporaryDirectory() as tmp:
            created = scaffold_app_payload(
                Path(tmp), json.dumps({"description": "a todo app"})
            )
            root = make_repo(Path(created["root"]))
            ws = boot_payload(root)["workspace"]
            self.assertTrue(ws["build_app"])
            self.assertEqual(ws["build_app_name"], created["name"])


if __name__ == "__main__":
    unittest.main()
