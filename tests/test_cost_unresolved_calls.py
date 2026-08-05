"""Spend must be reconciled before it is presented as authoritative (#619).

A `model_call_started` with no matching `model_call_finalized` means the
request left OPai — the provider may well have billed for it — and the outcome
never landed: the process died, the machine slept, the write failed. The work
happened; only its cost is unknown.

Before this, that state was indistinguishable from "nothing happened": the
usage snapshot reported `used: 0, confidence: "no-data"`. A lower bound was
being presented as a complete figure, which is the specific dishonesty #619
exists to prevent ("no false $0 cost", "assert no uncounted calls remain").

Distinct from tests/test_cost_reconciliation.py, which pins #390's
cross-surface arithmetic; this file is about *in-flight* work the arithmetic
cannot see.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from opaihub.ledger import (
    cost_reconciliation,
    record_model_call_finalized,
    record_model_call_started,
    unresolved_model_calls,
)
from opaihub.usage import build_usage_snapshots
from opaihub.usage_report import ProviderTurnUsage

MODELS = [{"id": "m", "provider": "p"}]
USAGE = ProviderTurnUsage.from_provider(
    turn_index=1, total=100, input_tokens=60, output_tokens=40
)


def _start(
    root: Path, call_id: str, *, turn_index: int = 1, model_id: str = "m"
) -> None:
    record_model_call_started(
        root,
        "task",
        call_id=call_id,
        run_id="run",
        turn_index=turn_index,
        model_id=model_id,
        provider_id="p",
        model_tier="L3",
        provider_type="cloud",
        confirmed=True,
    )


class UnresolvedCallTests(unittest.TestCase):
    def test_a_clean_ledger_has_nothing_outstanding(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(unresolved_model_calls(Path(tmp)), [])

    def test_a_dispatched_call_with_no_outcome_is_outstanding(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _start(root, "run:1")
            outstanding = unresolved_model_calls(root)
        self.assertEqual(len(outstanding), 1)
        self.assertEqual(outstanding[0]["call_id"], "run:1")

    def test_finalizing_clears_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _start(root, "run:1")
            record_model_call_finalized(root, "task", call_id="run:1", usage=USAGE)
            self.assertEqual(unresolved_model_calls(root), [])

    def test_only_the_unfinished_call_of_several_is_outstanding(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _start(root, "run:1", turn_index=1)
            record_model_call_finalized(root, "task", call_id="run:1", usage=USAGE)
            _start(root, "run:2", turn_index=2)
            outstanding = unresolved_model_calls(root)
        self.assertEqual([item["call_id"] for item in outstanding], ["run:2"])


class ReconciliationTests(unittest.TestCase):
    def test_a_fully_recorded_ledger_is_verified(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _start(root, "run:1")
            record_model_call_finalized(root, "task", call_id="run:1", usage=USAGE)
            report = cost_reconciliation(root)
        self.assertTrue(report["verified"])
        self.assertEqual(report["unresolved_calls"], 0)

    def test_an_outstanding_call_blocks_the_verified_claim(self) -> None:
        # The gate: while anything is in flight, no total may be authoritative.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _start(root, "run:1")
            report = cost_reconciliation(root)
        self.assertFalse(report["verified"])
        self.assertEqual(report["unresolved_calls"], 1)
        self.assertIn("lower bound", report["note"])

    def test_the_report_identifies_which_turn_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _start(root, "run:7", turn_index=7)
            entry = cost_reconciliation(root)["unresolved"][0]
        self.assertEqual(entry["call_id"], "run:7")
        self.assertEqual(entry["turn_index"], 7)
        self.assertEqual(entry["provider_type"], "cloud")
        self.assertTrue(entry["started_at"])

    def test_the_report_carries_no_prompt_text(self) -> None:
        # Reconciliation output reaches receipts and support bundles.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record_model_call_started(
                root,
                "refactor the billing module for ACME Corp",
                call_id="run:1",
                run_id="run",
                turn_index=1,
                model_id="m",
                provider_id="p",
                model_tier="L3",
                provider_type="cloud",
                confirmed=True,
            )
            payload = str(cost_reconciliation(root))
        self.assertNotIn("ACME", payload)
        self.assertNotIn("billing module", payload)

    def test_the_report_is_json_safe(self) -> None:
        import json

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _start(root, "run:1")
            json.dumps(cost_reconciliation(root))


class UsageSnapshotTests(unittest.TestCase):
    def test_a_row_is_flagged_unreconciled_when_a_call_is_in_flight(self) -> None:
        # The original defect: this reported used=0 / "no-data" — "nothing
        # happened" — when a paid call had in fact been dispatched.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _start(root, "run:1")
            snapshot = build_usage_snapshots(root, MODELS)[0]
        self.assertFalse(snapshot["reconciled"])
        self.assertEqual(snapshot["unresolvedCalls"], 1)

    def test_a_completed_run_reports_reconciled_with_its_tokens(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _start(root, "run:1")
            record_model_call_finalized(root, "task", call_id="run:1", usage=USAGE)
            snapshot = build_usage_snapshots(root, MODELS)[0]
        self.assertTrue(snapshot["reconciled"])
        self.assertEqual(snapshot["used"], 100)

    def test_an_unrelated_model_is_not_tainted(self) -> None:
        # One provider's in-flight call must not mark every other row unverified.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _start(root, "run:1", model_id="m")
            rows = build_usage_snapshots(
                root, [{"id": "m", "provider": "p"}, {"id": "other", "provider": "p"}]
            )
        by_id = {row["modelId"]: row for row in rows}
        self.assertFalse(by_id["m"]["reconciled"])
        self.assertTrue(by_id["other"]["reconciled"])

    def test_an_empty_ledger_is_reconciled_not_suspicious(self) -> None:
        # Nothing dispatched is genuinely reconciled; only in-flight work is not.
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = build_usage_snapshots(Path(tmp), MODELS)[0]
        self.assertTrue(snapshot["reconciled"])
        self.assertEqual(snapshot["unresolvedCalls"], 0)


if __name__ == "__main__":
    unittest.main()
