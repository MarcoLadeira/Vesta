from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from vestahub import ledger, usage


class UsagePerformanceTests(unittest.TestCase):
    def test_usage_snapshots_share_events_with_reconciliation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ledger.record_model_call(
                root,
                "task",
                model_tier="L2",
                provider_type="free_api",
                tokens=10,
                confirmed=True,
            )
            with (
                mock.patch.object(
                    usage, "read_events", wraps=usage.read_events
                ) as usage_reads,
                mock.patch.object(
                    ledger, "read_events", wraps=ledger.read_events
                ) as reconciliation_reads,
            ):
                usage.build_usage_snapshots(root, [])

        self.assertEqual(usage_reads.call_count + reconciliation_reads.call_count, 1)

    def test_usage_snapshots_index_events_once_for_many_models(self) -> None:
        class CountingEvent(dict[str, object]):
            model_id_reads = 0

            def get(self, key: str, default: object = None) -> object:
                if key == "model_id":
                    type(self).model_id_reads += 1
                return super().get(key, default)

        models = [{"id": f"model-{index}", "provider": "test"} for index in range(20)]
        events = [
            CountingEvent(event_type=ledger.EVENT_MODEL_CALL, model_id="model-0")
            for _ in range(100)
        ]

        with tempfile.TemporaryDirectory() as tmp:
            usage.build_usage_snapshots(Path(tmp), models, events=events)

        self.assertLessEqual(CountingEvent.model_id_reads, len(events))


if __name__ == "__main__":
    unittest.main()
