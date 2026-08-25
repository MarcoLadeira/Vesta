from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from opaihub import budget


class BudgetPerformanceTests(unittest.TestCase):
    def test_status_reads_the_usage_ledger_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with mock.patch.object(
                budget, "read_events", wraps=budget.read_events
            ) as read_events:
                budget.budget_status(root)

        self.assertEqual(read_events.call_count, 1)


if __name__ == "__main__":
    unittest.main()
