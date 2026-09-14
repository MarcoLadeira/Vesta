"""#619 AC7/AC9: a savings claim must not omit attempts it cannot price.

Two criteria meet here:

  AC7 "Failed/cancelled/partial attempts remain in total task economics."
  AC9 "Savings require verified completion, explicit baseline and price
       snapshot."

``opaihub.ledger.cost_reconciliation`` has always known when a call was
dispatched and never came back — a crash, a timeout, a killed process — and
it even carries the right sentence:

    "N dispatched call(s) have no recorded outcome. Cost incurred by them is
     unknown, so totals below are a lower bound, not a verified figure."

That machinery was adopted by ``opaihub/usage.py`` (Settings -> Model Usage)
and by nothing else. In particular ``summarize_ledger`` — which backs
``opai savings``, the surface the run receipt explicitly points users to
("See the full ledger with: opai savings") — never asked. Reproduced before
the fix, with one dispatched-and-lost call plus one completed call:

    summarize_ledger:      estimated_actual_spend_usd = 0.5
                           estimated_savings_usd      = 0.072
                           reconciliation key         = absent
    cost_reconciliation:   verified=False, unresolved_calls=1, note="..."

So the honest caveat existed and was never shown on the primary money
surface. The fix is adoption, not new machinery: carry the reconciliation
through and let it change what the report claims.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from opaihub.ledger import (
    record_model_call_finalized,
    record_model_call_started,
    record_route_decision,
    summarize_ledger,
)
from opaihub.savings import build_savings_report, render_savings_markdown
from opaihub.usage_report import ProviderTurnUsage


def _dispatch(root: Path, call_id: str) -> None:
    record_model_call_started(
        root,
        "task",
        call_id=call_id,
        run_id="r",
        turn_index=int(call_id.split(":")[-1]),
        model_id="m",
        provider_id="p",
        model_tier="L3",
        provider_type="cloud",
        confirmed=True,
    )


def _finalize(root: Path, call_id: str, cost: float = 0.50) -> None:
    record_model_call_finalized(
        root,
        "task",
        call_id=call_id,
        usage=ProviderTurnUsage.from_provider(
            turn_index=int(call_id.split(":")[-1]),
            total=100,
            input_tokens=60,
            output_tokens=40,
            cost_usd=cost,
            cost_provenance="actual",
        ),
    )


class _Project:
    """A ledger with one routed task and a controllable set of calls."""

    def __init__(self, *, lose_one: bool) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._lose_one = lose_one

    def __enter__(self) -> Path:
        root = Path(self._tmp.name)
        record_route_decision(
            root, "task", model_tier="L1", workflow="ask", cache_hit=False, source="ask"
        )
        if self._lose_one:
            _dispatch(root, "r:1")  # dispatched, never finalized
        _dispatch(root, "r:2")
        _finalize(root, "r:2")
        return root

    def __exit__(self, *exc: object) -> None:
        self._tmp.cleanup()


class SummarizeLedgerTests(unittest.TestCase):
    def test_the_summary_now_carries_reconciliation(self) -> None:
        with _Project(lose_one=True) as root:
            summary = summarize_ledger(root)
        self.assertIn("reconciliation", summary)
        self.assertFalse(summary["reconciliation"]["verified"])
        self.assertEqual(summary["reconciliation"]["unresolved_calls"], 1)

    def test_a_fully_reconciled_ledger_reports_verified(self) -> None:
        with _Project(lose_one=False) as root:
            summary = summarize_ledger(root)
        self.assertTrue(summary["reconciliation"]["verified"])
        self.assertEqual(summary["reconciliation"]["unresolved_calls"], 0)

    def test_the_spend_total_is_unchanged_by_this(self) -> None:
        # The fix adds honesty about completeness; it must not silently alter
        # the arithmetic of what *was* recorded.
        with _Project(lose_one=True) as root:
            summary = summarize_ledger(root)
        self.assertEqual(summary["estimated_actual_spend_usd"], 0.5)


class SavingsReportTests(unittest.TestCase):
    def test_an_unresolved_attempt_makes_the_headline_a_lower_bound(self) -> None:
        with _Project(lose_one=True) as root:
            report = build_savings_report(root)
        self.assertFalse(report["reconciliation"]["verified"])
        self.assertIn("At least", report["headline"])
        self.assertIn("lower bound", report["headline"])
        self.assertNotIn("Vesta estimates", report["headline"])

    def test_a_clean_ledger_keeps_the_confident_headline(self) -> None:
        with _Project(lose_one=False) as root:
            report = build_savings_report(root)
        self.assertTrue(report["reconciliation"]["verified"])
        self.assertIn("Vesta estimates", report["headline"])
        self.assertNotIn("lower bound", report["headline"])

    def test_the_report_exposes_machine_readable_completeness(self) -> None:
        # A consumer must not have to parse prose to learn the total is partial.
        with _Project(lose_one=True) as root:
            report = build_savings_report(root)
        self.assertIn("reconciliation", report)
        self.assertIs(report["reconciliation"]["verified"], False)

    def test_the_reconciliation_note_reaches_the_assumptions(self) -> None:
        with _Project(lose_one=True) as root:
            report = build_savings_report(root)
        self.assertTrue(
            any("no recorded outcome" in item for item in report["assumptions"]),
            "the caveat must travel with the numbers it qualifies",
        )

    def test_a_clean_ledger_adds_no_spurious_caveat(self) -> None:
        with _Project(lose_one=False) as root:
            report = build_savings_report(root)
        self.assertFalse(
            any("no recorded outcome" in item for item in report["assumptions"])
        )


class SavingsMarkdownTests(unittest.TestCase):
    """Markdown is the form people paste into a doc or an issue, where the
    surrounding context that would have explained the number is gone."""

    def test_the_exported_markdown_carries_the_caveat(self) -> None:
        with _Project(lose_one=True) as root:
            markdown = render_savings_markdown(build_savings_report(root))
        self.assertIn("lower bound", markdown)
        self.assertIn("no recorded outcome", markdown)

    def test_a_clean_export_is_not_cluttered_with_it(self) -> None:
        with _Project(lose_one=False) as root:
            markdown = render_savings_markdown(build_savings_report(root))
        self.assertNotIn("lower bound", markdown)

    def test_markdown_still_renders_without_a_reconciliation_key(self) -> None:
        # Defensive: a caller building a report dict by hand (or an older
        # cached payload) must not crash the renderer.
        with _Project(lose_one=False) as root:
            report = build_savings_report(root)
        report.pop("reconciliation", None)
        self.assertIn("Vesta Savings Report", render_savings_markdown(report))


if __name__ == "__main__":
    unittest.main()
