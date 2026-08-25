from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from opaihub import ledger, usage


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


if __name__ == "__main__":
    unittest.main()
