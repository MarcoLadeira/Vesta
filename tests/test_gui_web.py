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

from _helpers import isolated_home, make_repo

from opai.gui_web import (
    WEB_DIR,
    boot_payload,
    resolve_openable,
    settings_payload,
    web_available,
)
from opai.gui_web import _thread_status_for


class WebAvailableTests(unittest.TestCase):
    def test_returns_bool(self):
        self.assertIsInstance(web_available(), bool)


class ThreadStatusHonestyTests(unittest.TestCase):
    """#402: the persisted thread status derives from the completion verdict, so
    a partial/blocked/timeout run is never persisted (or resumed) as 'complete'."""

    def test_verdict_overrides_answered_legacy_status(self):
        # The exact leak #402 targets: legacy status "answered" but the verdict
        # says the objective was not actually met.
        self.assertEqual(
            _thread_status_for("answered", {"verdict": "partial"}), "partial"
        )
        self.assertEqual(
            _thread_status_for("answered", {"verdict": "blocked"}), "blocked"
        )
        self.assertEqual(
            _thread_status_for("answered", {"verdict": "timeout"}), "timeout"
        )
        self.assertEqual(
            _thread_status_for("answered", {"verdict": "completed"}), "complete"
        )

    def test_falls_back_to_legacy_buckets_without_a_verdict(self):
        self.assertEqual(_thread_status_for("answered", None), "complete")
        self.assertEqual(_thread_status_for("no_edits", None), "complete")
        self.assertEqual(_thread_status_for("cancelled", None), "cancelled")
        self.assertEqual(_thread_status_for("failed", None), "failed")

    def test_partial_verdict_persists_as_partial_not_complete(self):
        from opai.gui_recents import begin_thread_turn, load_thread
        from opai.gui_web import _persist_turn_result

        with isolated_home():
            root = make_repo(Path(tempfile.mkdtemp()))
            begin_thread_turn(root, request_id="r1", text="fix the bug", mode="safe-auto")
            _persist_turn_result(
                root,
                "r1",
                {
                    "status": "answered",
                    "answer": "I looked but changed nothing.",
                    "completion_verdict": {"verdict": "partial"},
                },
                mode="safe-auto",
            )
            thread = load_thread(root)
            statuses = [
                m["status"] for m in thread["messages"] if m["role"] == "assistant"
            ]
            self.assertEqual(statuses, ["partial"])


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

    def test_inspector_is_deferred_at_boot_but_complete_on_demand(self):
        # Startup deferral (#246): boot no longer computes the inspector; the
        # inspector() slot serves the same complete payload when the panel opens.
        from opai.gui_web import _inspector

        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp))
            self.assertIsNone(self._boot(root)["inspector"])
            ins = _inspector(
                root,
                {
                    "model_label": "Auto",
                    "model_advanced_label": "Auto",
                    "model_kind": "auto",
                    "mode": "safe-auto",
                    "mode_label": "Safe Auto",
                    "focus": "general",
                    "format": "normal",
                    "accounts": [],
                },
            )
        labels = {r["label"] for r in ins["rows"]}
        for needed in ("Model", "Run mode", "Workspace", "Permissions"):
            self.assertIn(needed, labels)
        self.assertEqual(len(ins["permissions"]), 8)
        self.assertTrue(ins["privacy"])
        self.assertIn("pct", ins["budget"])

    def test_boot_exposes_live_controls_preview_that_tracks_focus(self):
        # F20/F21: boot carries describe_controls — the single live reading of
        # Run mode + Task focus + the next run's agent mode — and it changes
        # when the persisted focus changes.
        from opaihub.gui_preferences import save_gui_preferences

        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp))
            controls = boot_payload(root)["controls"]
            self.assertEqual(controls["run_mode"], "safe-auto")
            self.assertEqual(controls["focus"], "general")
            self.assertEqual(controls["agent_mode_preview"], "explain")
            save_gui_preferences(root, {"default_task_mode": "build"})
            controls = boot_payload(root)["controls"]
            self.assertEqual(controls["focus"], "build")
            self.assertEqual(controls["agent_mode_preview"], "implement")
            self.assertFalse(controls["read_only"])

    def test_inspector_shows_live_agent_mode_preview_beside_last_run(self):
        # F21: the persisted "Agent mode" row is the last completed run; the
        # new "Agent mode (next run)" row + payload field are computed live
        # from the CURRENT run mode + focus selection.
        from opai.gui_modes import describe_controls
        from opai.gui_web import _inspector

        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp))
            ins = _inspector(
                root,
                {
                    "mode": "safe-auto",
                    "mode_label": "Safe Auto",
                    "focus": "build",
                    "format": "normal",
                    "accounts": [],
                },
            )
            rows = {r["label"]: r["value"] for r in ins["rows"]}
        # Both rows exist: history stays history, the preview is live.
        self.assertIn("Agent mode", rows)
        self.assertEqual(rows["Agent mode (next run)"], "Implement")
        self.assertEqual(ins["agent_mode_preview"], "implement")
        self.assertEqual(ins["controls"], describe_controls("safe-auto", "build"))

    def test_inspector_surfaces_github_push_readiness_only_when_editing(self):
        # #300: an edit-capable run shows GitHub push readiness up front; a
        # read-only Ask run doesn't clutter the panel with it.
        from opai.gui_web import _github_row_value, _inspector

        self.assertEqual(_github_row_value({"ready": True}), "Ready to push & open PRs")
        self.assertIn(
            "token", _github_row_value({"ready": False, "reason": "no_token"})
        )
        self.assertIn(
            "Enable", _github_row_value({"ready": False, "reason": "consent_off"})
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp))
            edit_labels = {
                r["label"] for r in _inspector(root, {"mode": "safe-auto"})["rows"]
            }
            ask_labels = {r["label"] for r in _inspector(root, {"mode": "ask"})["rows"]}
        self.assertIn("GitHub", edit_labels)
        self.assertNotIn("GitHub", ask_labels)

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
        for name in ("index.html", "design-tokens.css", "design-tokens-preview.html", "icons.js", "styles.css", "app.js"):
            self.assertTrue((WEB_DIR / name).exists(), name)

    def test_index_wires_bridge_and_assets(self):
        html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
        self.assertIn("qwebchannel.js", html)
        self.assertIn("design-tokens.css", html)
        self.assertIn("styles.css", html)
        self.assertIn("icons.js", html)
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
            # github joined via the git/PR connector credential (GITHUB_TOKEN);
            # kimi joined with its Moonshot free-tier credential.
            {"claude", "codex", "copilot", "kimi", "gemini", "groq", "mistral", "github"},
            {item["providerId"] for item in payload["connectionDoctor"]},
        )
        self.assertNotIn("cli_path", json.dumps(payload["connectionDoctor"]))
        # Credits & Balance: one snapshot per AI tool, accounts and free APIs.
        self.assertIn("providerBalances", payload)
        balance_providers = {item["provider"] for item in payload["providerBalances"]}
        for provider in ("claude", "codex", "copilot", "kimi", "gemini", "groq", "mistral"):
            self.assertIn(provider, balance_providers)
        for item in payload["providerBalances"]:
            self.assertIn(
                item["status"], {"ok", "low", "out", "unknown", "not_configured"}
            )
        # Model Usage: one per-provider usage-window snapshot for accounts and
        # free APIs, each with a verifiable window + honest official status.
        self.assertIn("providerUsage", payload)
        usage_providers = {item["provider"] for item in payload["providerUsage"]}
        for provider in ("claude", "codex", "copilot", "kimi", "gemini", "groq", "mistral"):
            self.assertIn(provider, usage_providers)
        for item in payload["providerUsage"]:
            self.assertIn(
                item["status"],
                {"live", "stale", "unavailable", "not_configured", "unsupported"},
            )
            self.assertIn("window", item)
            self.assertIn("official", item)
            # Official usage is never fabricated: an unavailable card carries no
            # invented percentage.
            if not item["official"].get("available"):
                self.assertIsNone(item["official"].get("percent"))
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


class FirewallSettingsPayloadTests(unittest.TestCase):
    """Budgets in settings (#238): straight from budget_status, no duplicates."""

    def test_firewall_block_carries_caps_spend_and_remaining(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp))
            firewall = settings_payload(root)["firewall"]
        for key in ("caps", "spent_month", "remaining", "local_first"):
            self.assertIn(key, firewall)
        self.assertEqual(
            set(firewall["caps"]),
            {"daily_usd_limit", "monthly_usd_limit", "per_task_hard_limit_usd"},
        )
        self.assertIn("today_usd", firewall["remaining"])


class PermissionsPrivacyPayloadTests(unittest.TestCase):
    """Permissions & Privacy pages (#239) are backed by real, derived data."""

    def test_mode_permissions_cover_every_mode_and_mark_the_active_one(self):
        from opaihub.gui_preferences import MODES

        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp))
            payload = settings_payload(root)
        modes = payload["modePermissions"]
        self.assertEqual([m["id"] for m in modes], list(MODES))
        for mode in modes:
            # Summary is derived from permissions_for, not invented copy.
            self.assertRegex(mode["summary"], r"\d+ allowed · \d+ ask · \d+ blocked")
        active = [m for m in modes if m["active"]]
        self.assertEqual(len(active), 1)

    def test_privacy_stance_is_factual_and_states_no_prompt_storage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp))
            privacy = settings_payload(root)["privacy"]
        self.assertFalse(privacy["prompts_stored"])
        joined = " ".join(privacy["statements"]).lower()
        self.assertIn("never stored", joined)
        self.assertIn("no telemetry", joined)


class OnboardingPreferenceTests(unittest.TestCase):
    """First-run onboarding flag (#250): default-show, persist-once, boot-surfaced."""

    def test_fresh_profile_boots_with_onboarding_unseen(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp))
            prefs = boot_payload(root)["prefs"]
        self.assertFalse(prefs["onboardingSeen"])

    def test_marking_seen_persists_and_surfaces_at_boot(self):
        from opaihub.gui_preferences import save_gui_preferences

        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp))
            saved = save_gui_preferences(root, {"onboarding_seen": True})
            self.assertTrue(saved["onboarding_seen"])
            self.assertTrue(boot_payload(root)["prefs"]["onboardingSeen"])

    def test_non_boolean_flag_is_coerced(self):
        from opaihub.gui_preferences import save_gui_preferences

        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp))
            saved = save_gui_preferences(root, {"onboarding_seen": "yes"})
            self.assertIs(saved["onboarding_seen"], True)


class AppearancePreferenceTests(unittest.TestCase):
    """Appearance prefs (#241): persisted, sanitized, and surfaced at boot."""

    def test_appearance_prefs_round_trip_and_reach_boot(self):
        from opaihub.gui_preferences import save_gui_preferences

        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp))
            saved = save_gui_preferences(
                root, {"density": "compact", "reduced_motion": "on"}
            )
            self.assertEqual(saved["density"], "compact")
            self.assertEqual(saved["reduced_motion"], "on")
            prefs = boot_payload(root)["prefs"]
            self.assertEqual(prefs["density"], "compact")
            self.assertEqual(prefs["reducedMotion"], "on")

    def test_invalid_appearance_values_sanitize_to_defaults(self):
        from opaihub.gui_preferences import save_gui_preferences

        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp))
            saved = save_gui_preferences(
                root, {"density": "microscopic", "reduced_motion": "sometimes"}
            )
            self.assertEqual(saved["density"], "comfortable")
            self.assertEqual(saved["reduced_motion"], "system")
            prefs = boot_payload(root)["prefs"]
            self.assertEqual(prefs["density"], "comfortable")
            self.assertEqual(prefs["reducedMotion"], "system")

    def test_defaults_present_without_any_saved_preferences(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp))
            prefs = boot_payload(root)["prefs"]
        self.assertEqual(prefs["density"], "comfortable")
        self.assertEqual(prefs["reducedMotion"], "system")


if __name__ == "__main__":
    unittest.main()
