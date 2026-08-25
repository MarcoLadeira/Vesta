from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from opai import gui_web
from opaihub import ledger, provider_usage, usage


class SettingsPerformanceTests(unittest.TestCase):
    def test_settings_payload_shares_one_ledger_snapshot(self) -> None:
        models = {"accounts": [], "connections": [], "models": []}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with (
                mock.patch.object(gui_web, "_models", return_value=models),
                mock.patch.object(gui_web, "cached_overview", return_value={}),
                mock.patch.object(gui_web.A, "cost_firewall", return_value={}),
                mock.patch.object(gui_web, "_usage_providers", return_value=[]),
                mock.patch.object(
                    gui_web, "provider_balances_payload", return_value=[]
                ),
                mock.patch.object(gui_web, "asset_build_identity", return_value={}),
                mock.patch(
                    "opaihub.accounts.provider_connection_doctor", return_value=[]
                ),
                mock.patch("opaihub.credentials.credential_statuses", return_value=[]),
                mock.patch("opaihub.github_connector.github_status", return_value={}),
                mock.patch.object(
                    usage, "read_events", wraps=usage.read_events
                ) as model_reads,
                mock.patch.object(
                    provider_usage, "read_events", wraps=provider_usage.read_events
                ) as provider_reads,
                mock.patch.object(
                    ledger, "read_events", wraps=ledger.read_events
                ) as shared_reads,
            ):
                gui_web.settings_payload(root)

        self.assertEqual(
            model_reads.call_count
            + provider_reads.call_count
            + shared_reads.call_count,
            1,
        )

    def test_provider_usage_indexes_events_once_for_many_providers(self) -> None:
        class CountingEvent(dict[str, object]):
            provider_reads = 0

            def get(self, key: str, default: object = None) -> object:
                if key in {"provider_id", "provider_type"}:
                    type(self).provider_reads += 1
                return super().get(key, default)

        events = [
            CountingEvent(event_type=ledger.EVENT_MODEL_CALL, provider_id="claude")
            for _ in range(100)
        ]
        providers = [
            {"provider": provider, "configured": True}
            for provider in provider_usage.USAGE_MODELS
        ]

        with tempfile.TemporaryDirectory() as tmp:
            provider_usage.usage_overview(Path(tmp), providers, events=events)

        self.assertLessEqual(CountingEvent.provider_reads, len(events) * 3)


if __name__ == "__main__":
    unittest.main()
