"""Tests for the web-UI bridge data layer + bundled assets (no Chromium needed).

The rendering lives in Chromium (QtWebEngine), which isn't headlessly testable
here, so these lock the *contract* the front-end relies on: the JSON the bridge
hands the page is complete, serializable, and leaks no secrets — and the bundled
assets reference the font and bridge correctly.
"""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from _helpers import isolated_home, make_repo

from opai import gui_permissions
from opai.gui_recents import thread_status_for_result
from opai.gui_web import (
    WEB_DIR,
    _assistant_presentation,
    _attributed_changed_files,
    asset_build_identity,
    boot_payload,
    resolve_openable,
    settings_payload,
    web_available,
)
from opai.release_identity import current_release_identity
from opaihub.run_result import RunResult


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
            thread_status_for_result("answered", {"verdict": "partial"}), "partial"
        )
        self.assertEqual(
            thread_status_for_result("answered", {"verdict": "blocked"}), "blocked"
        )
        self.assertEqual(
            thread_status_for_result("answered", {"verdict": "timeout"}), "timeout"
        )
        self.assertEqual(
            thread_status_for_result("answered", {"verdict": "completed"}), "complete"
        )

    def test_a_legacy_status_alone_can_no_longer_claim_completion(self):
        """#618 narrowed this deliberately; it previously asserted "complete".

        The old contract let a legacy status stand in for a verdict, so
        "answered" alone meant the objective was met. It does not: "answered"
        records that the provider replied, which is transport, and says nothing
        about whether the work was verified. Legacy strings are now
        compatibility inputs that may narrow an unknown result but may never
        report success.

        The failure-shaped imports below are unchanged -- claiming *less* than
        the evidence supports was never the risk.
        """

        self.assertEqual(thread_status_for_result("answered", None), "needs_attention")
        self.assertEqual(thread_status_for_result("no_edits", None), "needs_attention")
        self.assertEqual(thread_status_for_result("cancelled", None), "cancelled")
        self.assertEqual(thread_status_for_result("failed", None), "failed")

    def test_canonical_run_result_overrides_both_legacy_authorities(self):
        canonical = RunResult.from_payload(
            state="partial",
            reason_detail="Verification remained incomplete.",
            final_transition_at="2026-08-11T12:00:00Z",
        ).to_dict()

        self.assertEqual(
            thread_status_for_result("answered", {"verdict": "completed"}, canonical),
            "partial",
        )

    def test_malformed_canonical_run_result_fails_closed(self):
        self.assertEqual(
            thread_status_for_result(
                "answered", {"verdict": "completed"}, {"schema_version": 1}
            ),
            "needs_attention",
        )

    def test_partial_verdict_persists_as_partial_not_complete(self):
        from opai.gui_recents import begin_thread_turn, load_thread
        from opai.gui_web import _persist_turn_result

        with isolated_home():
            root = make_repo(Path(tempfile.mkdtemp()))
            begin_thread_turn(
                root, request_id="r1", text="fix the bug", mode="safe-auto"
            )
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


class AssistantPresentationProjectionTests(unittest.TestCase):
    def test_changed_file_attribution_is_ordered_deduplicated_and_rollback_true(self):
        result = {
            "status": "answered",
            "receipt": {"changed_files": ["b.py", "a.py", "b.py"]},
            "workflow": {
                "diff_review": {"files": [{"path": "a.py"}, {"path": "c.py"}]},
                "changed_files": ["d.py", "c.py"],
            },
            "changed_files": ["e.py", "a.py"],
            "applied": [{"path": "fallback.py"}],
        }

        self.assertEqual(
            _attributed_changed_files(result),
            ["b.py", "a.py", "c.py", "d.py", "e.py"],
        )
        self.assertEqual(
            _attributed_changed_files(
                {"changed_files": ["../escape.py", "C:drive.py", "file.py:ads"]}
            ),
            [],
        )
        self.assertEqual(
            _attributed_changed_files(
                {
                    **result,
                    "status": "partial_rollback",
                    "remaining_changed_files": ["remaining.py", "remaining.py"],
                }
            ),
            ["remaining.py"],
        )
        self.assertEqual(
            _attributed_changed_files({**result, "status": "rolled_back"}), []
        )
        self.assertEqual(
            _attributed_changed_files(
                {"status": "applied", "applied": [{"path": "build.js"}]}
            ),
            ["build.js"],
        )

    def test_projection_keeps_only_canonical_bounded_structured_evidence(self):
        canonical = RunResult.from_payload(
            state="completed",
            reason_detail="The requested explanation was delivered.",
            final_transition_at="2026-08-30T12:00:00Z",
            mutating=False,
            verification={"applicable": False, "verdict": "not_applicable"},
            delivery={
                "applicable": True,
                "verdict": "delivered",
                "record_ref": {"kind": "receipt", "id": "delivery-1"},
            },
            economics={
                "integrity": "reconciled",
                "record_ref": {"kind": "receipt", "id": "economics-1"},
            },
            diagnostics={"record_refs": [], "codes": ["SAFE_DIAGNOSTIC"]},
        ).to_dict()
        result = {
            "status": "answered",
            "run_result": canonical,
            "completion_verdict": {
                "verdict": "completed",
                "reason_code": "answer_delivered",
                "next_action": "",
                "answer_conflicts": False,
                "objective": {"objective_text": "never persist"},
                "evidence": [{"raw": "never persist"}],
            },
            "workflow": {
                "phase": "completed",
                "message": "Read-only task completed",
                "history": [
                    {
                        "phase": "context_gathering",
                        "message": "Read bounded context",
                        "next_actions": ["Explain the result"],
                        "metadata": {"source": "private source"},
                    },
                    {
                        "phase": "completed",
                        "message": "Read-only task completed",
                    },
                ],
                "diff_review": {
                    "summary": {
                        "files": 1,
                        "additions": 2,
                        "deletions": 1,
                        "pending": 0,
                        "approved": 1,
                        "rejected": 0,
                        "risky": 0,
                        "truncated": False,
                    },
                    "files": [
                        {
                            "path": "src/app.py",
                            "decision": "approved",
                            "additions": 2,
                            "deletions": 1,
                            "risky": False,
                            "risk_reasons": [],
                            "untracked": False,
                            "sensitive": False,
                            "hunks": [{"lines": ["private source"]}],
                            "source": "private source",
                        }
                    ],
                },
            },
            "tool_trace": [{"output": "private source"}],
            "provider_output": "private source",
        }

        projected = _assistant_presentation(result, ["src/app.py"])
        encoded = json.dumps(projected, sort_keys=True)

        self.assertEqual(projected["schema_version"], 1)
        self.assertEqual(projected["run"]["state"], "completed")
        self.assertEqual(projected["run"]["reason_code"], "answer_delivered")
        self.assertEqual(
            projected["evidence"]["delivery"],
            {"applicable": True, "verdict": "delivered"},
        )
        self.assertEqual(projected["changes"]["files"][0]["path"], "src/app.py")
        self.assertEqual(
            projected["activity"][0],
            {
                "phase": "context_gathering",
                "message": "Read bounded context",
                "next_action": "Explain the result",
            },
        )
        for forbidden in (
            "tool_trace",
            "provider_output",
            "objective",
            "record_ref",
            "hunks",
            "source",
            "private source",
        ):
            self.assertNotIn(forbidden, encoded)

    def test_projection_uses_typed_inert_approval_fields(self):
        projected = _assistant_presentation(
            {
                "status": "needs_command_approval",
                "awaiting": {
                    "kind": "approval",
                    "status": "needs_command_approval",
                    "question": "I need your OK to run this command.",
                    "backgroundActive": False,
                    "resumable": True,
                },
                "command": "pytest -q",
                "reason": "confirm-class command",
                "command_approval": {
                    "command": "pytest -q",
                    "reason": "confirm-class command",
                    "authority": "must not persist",
                },
            },
            [],
        )

        self.assertEqual(
            projected["approval"],
            {
                "kind": "approval",
                "state": "needs_command_approval",
                "question": "I need your OK to run this command.",
                "command": "pytest -q",
                "reason": "confirm-class command",
                "background_active": False,
            },
        )

    def test_test_counts_come_only_from_a_valid_verification_manifest(self):
        from datetime import datetime, timedelta, timezone

        from opaihub.verification_execution import (
            CheckRecord,
            CheckStatus,
            VerificationAttempt,
            VerificationExecutionContext,
            VerificationManifest,
        )
        from opaihub.verification_policy import (
            PolicyCheck,
            PolicySource,
            VerificationPolicy,
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            check = PolicyCheck(
                "unit",
                "unit",
                "required",
                "Run unit tests.",
                command=("python", "-m", "pytest", "-q"),
            )
            policy = VerificationPolicy(
                status="ready",
                classification={"mode": "implement", "edit_capable": True},
                checks=(check,),
                sources=(PolicySource("builtin", "Test policy"),),
            )
            started = datetime(2026, 8, 30, tzinfo=timezone.utc)
            attempt = VerificationAttempt(
                check_id="unit",
                index=1,
                status=CheckStatus.PASSED,
                command=("python", "-m", "pytest", "-q"),
                working_directory=root,
                started_at=started,
                ended_at=started + timedelta(seconds=1),
                exit_status=0,
                output_summary="secret verification output",
                environment_digest="a" * 64,
                teardown_verified=True,
            )
            manifest = VerificationManifest.from_policy(
                policy,
                VerificationExecutionContext(
                    task_id="task-1",
                    run_id="run-1",
                    worktree=root,
                    repository_id="repo-1",
                    head_sha="b" * 40,
                ),
                (CheckRecord.from_attempts(check, (attempt,)),),
            ).to_dict()

            projected = _assistant_presentation(
                {
                    "verification_manifest": manifest,
                    "tests": {"status": "failed", "failed": 999},
                },
                [],
            )

        self.assertEqual(
            projected["tests"],
            {"status": "passed", "passed": 1, "failed": 0, "skipped": 0},
        )
        self.assertNotIn("secret verification output", json.dumps(projected))


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

    def test_boot_identifies_the_exact_hosted_asset_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp))
            payload = self._boot(root)

        self.assertEqual(payload["build"], asset_build_identity())
        self.assertRegex(payload["build"]["assetFingerprint"], r"^[0-9a-f]{64}$")
        self.assertGreater(payload["build"]["assetCount"], 0)
        self.assertIn(
            payload["build"]["runtimeSource"], {"source_checkout", "installed_package"}
        )

    def test_nav_groups_are_simple_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp))
            payload = self._boot(root)
        # No nav rows at all: the sidebar is the recents list. Chat, Prompt
        # Library and the Insights dashboards are routable but unlisted, and
        # New chat is a header action.
        self.assertEqual(payload["navGroups"], [])

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
        # One row per capability the panel reports on, including the per-push
        # approval row added for Round 5 finding 1.
        self.assertEqual(len(ins["permissions"]), len(gui_permissions.CAPABILITIES))
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

    def test_a_first_run_boots_with_the_inspector_hidden(self):
        """The boot payload must not ask for an inspector nobody requested.

        Honest scope: this passed before the fix too. ``load_gui_preferences``
        always merges ``DEFAULT_PREFERENCES``, so the ``.get(..., True)``
        fallback that used to sit here never actually fired -- it was a latent
        disagreement with the documented default, not the cause of SMOKE-UX-001.
        That cause was in the front end (``app.js`` state and ``index.html``),
        where the shell painted the panel open before any preference was known.
        This pins the payload half so the two cannot drift apart later.
        """
        from opaihub.gui_preferences import save_gui_preferences

        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp))
            self.assertFalse(boot_payload(root)["prefs"]["showPanel"])

            # ...and an explicit choice is still honoured in both directions.
            save_gui_preferences(root, {"show_control_panel": True})
            self.assertTrue(boot_payload(root)["prefs"]["showPanel"])
            save_gui_preferences(root, {"show_control_panel": False})
            self.assertFalse(boot_payload(root)["prefs"]["showPanel"])

    def test_bypass_is_a_switch_that_leaves_the_mode_intact(self):
        """Toggling Bypass must not cost the user the mode they were in.

        As a sixth entry in the mode list, turning bypass on discarded the
        selected mode and turning it off could not give it back. As a switch it
        composes: the mode persists underneath and reappears when it is off.
        """
        from opaihub.command_policy import resolve_autonomy
        from opaihub.gui_preferences import load_gui_preferences, save_gui_preferences

        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp))
            save_gui_preferences(root, {"default_mode": "auto-edits"})

            def effective():
                prefs = load_gui_preferences(root)
                return prefs["default_mode"], resolve_autonomy(
                    prefs["default_mode"],
                    bypass_permissions=prefs["bypass_permissions"],
                )

            self.assertEqual(effective(), ("auto-edits", "auto-edits"))
            save_gui_preferences(root, {"bypass_permissions": True})
            self.assertEqual(effective(), ("auto-edits", "bypass"))
            save_gui_preferences(root, {"bypass_permissions": False})
            self.assertEqual(effective(), ("auto-edits", "auto-edits"))

    def test_the_bypass_switch_is_persistable_from_the_bridge(self):
        # A switch the front end cannot save is not a switch.
        from opai.gui_web import _BRIDGE_PREFERENCE_KEYS

        self.assertIn("bypass_permissions", _BRIDGE_PREFERENCE_KEYS)

    def test_the_stored_default_and_the_payload_fallback_agree(self):
        # The bug was a disagreement between these two, so assert them together.
        from opaihub.gui_preferences import DEFAULT_PREFERENCES

        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp))
            self.assertEqual(
                bool(DEFAULT_PREFERENCES["show_control_panel"]),
                bool(boot_payload(root)["prefs"]["showPanel"]),
            )

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

    def test_workspace_state_reflects_a_commit_made_after_boot(self):
        # Round 2: the "N uncommitted" badge is built from the boot payload,
        # which is computed once per window — so it kept showing the startup
        # count after a run committed the files. The GUI now re-reads this on
        # every finished turn, so it has to see the post-commit truth.
        import subprocess

        from opai.gui_web import _workspace

        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp))
            (root / "leftover.txt").write_text("work\n", encoding="utf-8")
            before = _workspace(root)

            for argv in (
                ["git", "add", "leftover.txt"],
                ["git", "commit", "-m", "chore: commit the leftover file"],
            ):
                subprocess.run(argv, cwd=root, check=True, capture_output=True)

            after = _workspace(root)

        self.assertIn("leftover.txt", before["dirty_paths"])
        self.assertNotIn("leftover.txt", after["dirty_paths"])

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


class StreamTokenPayloadTests(unittest.TestCase):
    def test_block_marker_is_present_only_for_a_new_message(self):
        from opai.gui_web import _stream_token_payload

        self.assertEqual(
            _stream_token_payload("request-1", "hello", start_block=False),
            {"requestId": "request-1", "text": "hello"},
        )
        self.assertEqual(
            _stream_token_payload("request-1", "next", start_block=True),
            {"requestId": "request-1", "text": "next", "blockStart": True},
        )


class WebAssetsTests(unittest.TestCase):
    def test_asset_fingerprint_changes_when_a_hosted_asset_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            assets = Path(tmp)
            (assets / "index.html").write_text(
                "<script src='app.js'></script>", encoding="utf-8"
            )
            (assets / "app.js").write_text("window.build = 1;", encoding="utf-8")
            first = asset_build_identity(assets)
            (assets / "app.js").write_text("window.build = 2;", encoding="utf-8")
            second = asset_build_identity(assets)

        self.assertNotEqual(first["assetFingerprint"], second["assetFingerprint"])
        self.assertEqual(first["assetCount"], 2)
        self.assertEqual(second["assetCount"], 2)

    def test_core_assets_exist(self):
        for name in (
            "index.html",
            "design-tokens.css",
            "design-tokens-preview.html",
            "icons.js",
            "run-result.js",
            "markdown-renderer.js",
            "chat-components.js",
            "styles.css",
            "app.js",
        ):
            self.assertTrue((WEB_DIR / name).exists(), name)

    def test_index_wires_bridge_and_assets(self):
        html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
        self.assertIn("qwebchannel.js", html)
        self.assertIn("design-tokens.css", html)
        self.assertIn("styles.css", html)
        self.assertIn("icons.js", html)
        self.assertIn("vendor/markdown-it-14.1.0.min.js", html)
        self.assertIn("markdown-renderer.js", html)
        self.assertIn("chat-components.js", html)
        self.assertIn("app.js", html)
        self.assertLess(
            html.index("vendor/markdown-it-14.1.0.min.js"),
            html.index("markdown-renderer.js"),
        )
        self.assertLess(html.index("markdown-renderer.js"), html.index("app.js"))
        self.assertLess(html.index("chat-components.js"), html.index("app.js"))

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
    def test_custom_account_models_are_projected_into_the_live_catalog(self):
        from opai.gui_web import _models

        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp))
            overrides = Path(tmp) / "models.json"
            overrides.write_text(
                json.dumps(
                    {
                        "providers": {
                            "codex": {
                                "models": [
                                    {
                                        "id": "gpt-custom",
                                        "display": "My GPT",
                                        "capability": "best",
                                    }
                                ]
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            base = {
                "models": [
                    {
                        "id": "account:codex:gpt-default",
                        "model": "gpt-default",
                        "label": "Codex · Default",
                        "provider": "codex",
                        "kind": "account",
                        "group": "codex",
                    }
                ],
                "accounts": [],
                "connections": [],
            }
            with (
                mock.patch.dict("os.environ", {"OPAI_MODEL_OVERRIDES": str(overrides)}),
                mock.patch("opai.gui_web.A.available_models", return_value=base),
            ):
                payload = _models(root, discover_local=False)

        custom = next(
            model
            for model in payload["models"]
            if model["id"] == "account:codex:gpt-custom"
        )
        self.assertEqual(custom["label"], "Codex · My GPT")
        self.assertEqual(custom["badge"], "best")

    def test_settings_exposes_the_global_model_override_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp))
            overrides = Path(tmp) / "models.json"
            overrides.write_text(
                json.dumps(
                    {
                        "providers": {
                            "codex": {
                                "models": [
                                    {
                                        "id": "gpt-custom",
                                        "display": "My GPT",
                                        "capability": "best",
                                    }
                                ],
                                "hide": ["gpt-old"],
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            with (
                mock.patch.dict("os.environ", {"OPAI_MODEL_OVERRIDES": str(overrides)}),
                mock.patch("opai.gui_web._cached_update_check", return_value={}),
            ):
                payload = settings_payload(root)

        self.assertEqual(payload["modelOverrides"]["path"], "~/.opai/models.json")
        self.assertEqual(
            payload["modelOverrides"]["providers"]["codex"]["models"][0]["id"],
            "gpt-custom",
        )
        self.assertEqual(payload["modelOverrides"]["hidden"]["codex"], ["gpt-old"])

    def test_about_exposes_the_same_asset_build_identity_as_boot(self):
        from opai.compatibility import runtime_compatibility_payload

        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp))
            payload = settings_payload(root)

        self.assertEqual(payload["about"]["build"], asset_build_identity())
        expected = current_release_identity().to_dict()
        expected.pop("metadata_source")
        self.assertEqual(payload["about"]["release_identity"], expected)
        self.assertEqual(
            payload["about"]["compatibility"], runtime_compatibility_payload()
        )

    def test_settings_exposes_normalized_connections(self):
        from opaihub.provider_catalog import provider_ids

        accounts = [
            {
                "id": provider,
                "label": provider.title(),
                "vendor": f"Test {provider.title()}",
                "cli": provider,
                "cli_path": None,
                "cli_present": False,
                "authenticated": False,
                "connected": False,
                "login_hint": "",
            }
            for provider in ("claude", "codex", "copilot")
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp))
            with (
                mock.patch(
                    "opaihub.accounts._account_cli_version",
                    side_effect=AssertionError("synchronous CLI version lookup"),
                ),
                mock.patch(
                    "opaihub.accounts.list_connected_accounts", return_value=accounts
                ),
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
            # GitHub is the non-AI connector; every AI doctor entry must follow
            # the version-pinned catalog rather than a drifting hand-written set.
            set(provider_ids()) | {"github"},
            {item["providerId"] for item in payload["connectionDoctor"]},
        )
        self.assertNotIn("cli_path", json.dumps(payload["connectionDoctor"]))
        # Credits & Balance: one snapshot per AI tool, accounts and free APIs.
        self.assertIn("providerBalances", payload)
        balance_providers = {item["provider"] for item in payload["providerBalances"]}
        for provider in (
            "claude",
            "codex",
            "copilot",
            "kimi",
            "gemini",
            "groq",
            "mistral",
        ):
            self.assertIn(provider, balance_providers)
        for item in payload["providerBalances"]:
            self.assertIn(
                item["status"], {"ok", "low", "out", "unknown", "not_configured"}
            )
        # Model Usage: one per-provider usage-window snapshot for accounts and
        # free APIs, each with a verifiable window + honest official status.
        self.assertIn("providerUsage", payload)
        usage_providers = {item["provider"] for item in payload["providerUsage"]}
        for provider in (
            "claude",
            "codex",
            "copilot",
            "kimi",
            "gemini",
            "groq",
            "mistral",
        ):
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

    def test_nested_scaffold_is_the_workspace_even_inside_a_parent_repo(self):
        from _helpers import make_repo

        from opai.gui_web import boot_payload, scaffold_app_payload

        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(Path(tmp))
            created = scaffold_app_payload(
                repo, json.dumps({"description": "a nested notes app"})
            )
            app = Path(created["root"]).resolve()

            ws = boot_payload(app)["workspace"]

            self.assertEqual(ws["root"], str(app))
            self.assertTrue(ws["build_app"])
            self.assertEqual(ws["build_app_name"], created["name"])


class ContextPickerPayloadTests(unittest.TestCase):
    def test_picker_returns_only_workspace_relative_paths(self):
        from opai.gui_web import context_picker_payload

        with (
            tempfile.TemporaryDirectory() as tmp,
            tempfile.TemporaryDirectory() as other,
        ):
            root = Path(tmp).resolve()
            inside = root / "src" / "app.js"
            inside.parent.mkdir()
            inside.write_text("x", encoding="utf-8")
            outside = Path(other).resolve() / "secret.txt"
            outside.write_text("secret", encoding="utf-8")

            payload = context_picker_payload(root, [str(inside), str(outside)])

        self.assertEqual(payload["paths"], ["src/app.js"])
        self.assertEqual(payload["rejected"], 1)

    def test_folder_paths_keep_a_trailing_slash(self):
        from opai.gui_web import context_picker_payload

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            folder = root / "src"
            folder.mkdir()

            payload = context_picker_payload(root, [str(folder)])

        self.assertEqual(payload["paths"], ["src/"])


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
                root,
                {
                    "density": "compact",
                    "response_density": "detailed",
                    "reduced_motion": "on",
                    "activity_copy": "off",
                    "composer_style": "command",
                },
            )
            self.assertEqual(saved["density"], "compact")
            self.assertEqual(saved["response_density"], "detailed")
            self.assertEqual(saved["reduced_motion"], "on")
            self.assertEqual(saved["activity_copy"], "off")
            self.assertEqual(saved["composer_style"], "command")
            prefs = boot_payload(root)["prefs"]
            self.assertEqual(prefs["density"], "compact")
            self.assertEqual(prefs["responseDensity"], "detailed")
            self.assertEqual(prefs["reducedMotion"], "on")
            self.assertEqual(prefs["activityCopy"], "off")
            self.assertEqual(prefs["composerStyle"], "command")

    def test_invalid_appearance_values_sanitize_to_defaults(self):
        from opaihub.gui_preferences import save_gui_preferences

        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp))
            saved = save_gui_preferences(
                root,
                {
                    "density": "microscopic",
                    "response_density": "microscopic",
                    "reduced_motion": "sometimes",
                    "activity_copy": "sometimes",
                    "composer_style": "floating",
                },
            )
            self.assertEqual(saved["density"], "comfortable")
            self.assertEqual(saved["response_density"], "balanced")
            self.assertEqual(saved["reduced_motion"], "system")
            self.assertEqual(saved["activity_copy"], "on")
            self.assertEqual(saved["composer_style"], "toolbar")
            prefs = boot_payload(root)["prefs"]
            self.assertEqual(prefs["density"], "comfortable")
            self.assertEqual(prefs["responseDensity"], "balanced")
            self.assertEqual(prefs["reducedMotion"], "system")
            self.assertEqual(prefs["activityCopy"], "on")
            self.assertEqual(prefs["composerStyle"], "toolbar")

    def test_defaults_present_without_any_saved_preferences(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp))
            prefs = boot_payload(root)["prefs"]
        self.assertEqual(prefs["density"], "comfortable")
        self.assertEqual(prefs["responseDensity"], "balanced")
        self.assertEqual(prefs["reducedMotion"], "system")
        self.assertEqual(prefs["activityCopy"], "on")
        self.assertEqual(prefs["composerStyle"], "toolbar")

    def test_bridge_allows_response_and_composer_presentation_preferences(self):
        from opai.gui_web import _BRIDGE_PREFERENCE_KEYS

        self.assertIn("response_density", _BRIDGE_PREFERENCE_KEYS)
        self.assertIn("composer_style", _BRIDGE_PREFERENCE_KEYS)


@unittest.skipUnless(
    importlib.util.find_spec("PySide6") is not None,
    "PySide6 not installed (desktop GUI extra)",
)
class RuntimeIndexUrlTests(unittest.TestCase):
    """A missing packaged web asset must fail loudly, not open a blank window
    (#365). Before this, index.html's own absence was indistinguishable from
    "the cache-busted copy could not be written" -- both were silently
    swallowed as the same OSError, and the window opened anyway pointed at a
    file that does not exist."""

    def test_missing_index_html_raises_instead_of_loading_nothing(self):
        from opai.gui_web import _runtime_index_url

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError):
                _runtime_index_url(Path(tmp))

    def test_present_index_html_still_resolves_to_an_existing_file(self):
        from opai.gui_web import _runtime_index_url

        with tempfile.TemporaryDirectory() as tmp:
            web_dir = Path(tmp)
            (web_dir / "index.html").write_text(
                "<html><body>ok</body></html>", encoding="utf-8"
            )
            url = _runtime_index_url(web_dir)
            resolved = Path(url.toLocalFile())
            self.assertTrue(resolved.is_file())
            self.assertIn("ok", resolved.read_text(encoding="utf-8"))

    def test_unwritable_directory_falls_back_to_the_plain_file_not_an_error(self):
        # The narrower except OSError still does its original job: an
        # existing source file plus a write failure degrades gracefully.
        from opai.gui_web import _runtime_index_url

        with tempfile.TemporaryDirectory() as tmp:
            web_dir = Path(tmp)
            (web_dir / "index.html").write_text("<html></html>", encoding="utf-8")
            with mock.patch(
                "pathlib.Path.write_text", side_effect=OSError("read-only")
            ):
                url = _runtime_index_url(web_dir)
            self.assertEqual(Path(url.toLocalFile()).name, "index.html")


if __name__ == "__main__":
    unittest.main()
