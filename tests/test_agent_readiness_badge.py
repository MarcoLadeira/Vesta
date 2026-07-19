"""Agent Readiness badge honesty (#414).

The dashboard used to render a green ``ACTIVE`` badge for Cursor/Cline off the
project-rules status alone, contradicting the ``wrapper: missing`` /
``capture: missing`` sub-rows on the same card. These tests pin the badge to the
sub-states: ``active`` only when the wrapper and capture are genuinely ready.
"""

import unittest
from pathlib import Path
from unittest import mock

from opai.app_state import _derive_card_status, agent_readiness
from opai.gui_view_model import _client_card, _status_severity


class DeriveCardStatusTests(unittest.TestCase):
    def test_active_and_fully_ready_stays_active(self):
        self.assertEqual(
            _derive_card_status(
                "active",
                wrapper_installed=True,
                capture_mode="selective_proxy",
                global_ready=True,
            ),
            "active",
        )

    def test_active_with_global_not_applicable_stays_active(self):
        # Cursor/Cline have no global file: global_ready is None (N/A) and must
        # not, by itself, hold an otherwise-ready client at "needs setup".
        self.assertEqual(
            _derive_card_status(
                "active",
                wrapper_installed=True,
                capture_mode="selective_proxy",
                global_ready=None,
            ),
            "active",
        )

    def test_active_but_wrapper_missing_needs_setup(self):
        # The exact #414 case: rules managed, wrapper missing → not "active".
        self.assertEqual(
            _derive_card_status(
                "active",
                wrapper_installed=False,
                capture_mode="missing",
                global_ready=None,
            ),
            "needs setup",
        )

    def test_active_but_capture_not_selective_proxy_needs_setup(self):
        self.assertEqual(
            _derive_card_status(
                "active",
                wrapper_installed=True,
                capture_mode="legacy_passthrough",
                global_ready=True,
            ),
            "needs setup",
        )

    def test_active_but_global_explicitly_missing_needs_setup(self):
        self.assertEqual(
            _derive_card_status(
                "active",
                wrapper_installed=True,
                capture_mode="selective_proxy",
                global_ready=False,
            ),
            "needs setup",
        )

    def test_non_active_statuses_pass_through_unchanged(self):
        for status in ("broken", "missing", "unknown"):
            with self.subTest(status=status):
                self.assertEqual(
                    _derive_card_status(
                        status,
                        wrapper_installed=False,
                        capture_mode="missing",
                        global_ready=None,
                    ),
                    status,
                )


class AgentReadinessBadgeTests(unittest.TestCase):
    """Acceptance criterion: a mock agent with wrapper=missing is not 'active'."""

    def _readiness(self):
        project_status = {
            "global": {
                "wrappers": {
                    "claude": {"exists": True, "capture_mode": "selective_proxy"},
                    "cursor": {"exists": False, "capture_mode": "missing"},
                    "cline": {"exists": False, "capture_mode": "missing"},
                }
            }
        }
        integrations = {
            "clients": [
                {
                    "id": "claude", "label": "Claude Code", "status": "active",
                    "global_ready": True, "project_managed": ["CLAUDE.md"],
                },
                {
                    "id": "cursor", "label": "Cursor", "status": "active",
                    "project_managed": [".cursor/rules/opai.mdc"],
                },
                {
                    "id": "cline", "label": "Cline", "status": "active",
                    "project_managed": [".clinerules/opai.md"],
                },
            ],
            "summary": {"active": ["claude", "cursor", "cline"]},
            "repair_command": "opai activate --repair",
        }
        with mock.patch(
            "opai.integrations.project_status", return_value=project_status
        ), mock.patch(
            "opai.clients.client_integrations_status", return_value=integrations
        ), mock.patch(
            "opai.clients.detect_stale_paths", return_value={}
        ):
            return agent_readiness(Path("."))

    def test_rules_managed_but_wrapper_missing_is_not_active(self):
        by_id = {c["id"]: c for c in self._readiness()["clients"]}
        self.assertEqual(by_id["cursor"]["status"], "needs setup")
        self.assertEqual(by_id["cline"]["status"], "needs setup")
        # And the fully-installed client is still honestly "active".
        self.assertEqual(by_id["claude"]["status"], "active")

    def test_downgraded_card_still_surfaces_repair_and_subrows(self):
        cursor = {c["id"]: c for c in self._readiness()["clients"]}["cursor"]
        self.assertFalse(cursor["wrapper_installed"])
        self.assertEqual(cursor["wrapper_capture_mode"], "missing")
        self.assertTrue(cursor["repair"])  # remediation hint is still offered


class BadgeSeverityTests(unittest.TestCase):
    def test_needs_setup_maps_to_warning_severity(self):
        self.assertEqual(_status_severity("needs setup"), "warning")

    def test_client_card_renders_needs_setup_not_green_active(self):
        card = _client_card(
            {
                "id": "cursor", "label": "Cursor", "status": "needs setup",
                "wrapper_installed": False, "wrapper_capture_mode": "missing",
                "config_rules": True, "global_ready": None,
                "repair": "opai activate --repair",
            }
        )
        self.assertEqual(card["status"], "NEEDS SETUP")
        self.assertEqual(card["severity"], "warning")
        wrapper_row = next(m for m in card["metrics"] if m["label"] == "Wrapper")
        self.assertEqual(wrapper_row["value"], "missing")


if __name__ == "__main__":
    unittest.main()
