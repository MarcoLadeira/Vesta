"""Task-outcome metrics (#288): the versioned record, its idempotent recorder,
the reconciling summary, GUI/CLI parity, and privacy.

All hermetic — no real model, CLI, or network. Cost per completed task is proven
to reconcile *exactly* to the authoritative model_call spend (#286), and unknown
values are proven to stay unknown rather than being synthesised.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from _helpers import FakeAccountRunner, make_repo

from vestahub.ledger import (
    UNKNOWN,
    ledger_path,
    read_events,
    record_cache_lookup,
    record_model_call,
    record_task_outcome,
    summarize_ledger,
    summarize_outcomes,
)


def _outcomes(root: Path) -> list[dict]:
    return [e for e in read_events(root) if e.get("event_type") == "task_outcome"]


class RecordTaskOutcomeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = make_repo(Path(self._tmp.name))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_records_a_versioned_terminal_record(self):
        event = record_task_outcome(
            self.root, "some task", outcome_id="turn-1", category="completed"
        )
        self.assertEqual(event["event_type"], "task_outcome")
        self.assertEqual(event["schema_version"], 1)
        self.assertEqual(event["outcome_id"], "turn-1")
        self.assertEqual(event["category"], "completed")
        # A one-way hash, never the raw task.
        self.assertIn("task_hash", event)
        self.assertNotIn("some task", json.dumps(event))

    def test_nonfinite_outcome_metrics_are_recorded_as_unknown(self):
        event = record_task_outcome(
            self.root,
            "some task",
            outcome_id="turn-nonfinite",
            category="completed",
            total_tokens=float("nan"),
            attributed_cost_usd=float("inf"),
        )

        self.assertEqual(event["total_tokens"], UNKNOWN)
        self.assertEqual(event["attributed_cost_usd"], UNKNOWN)

    def test_at_most_one_terminal_outcome_per_id(self):
        first = record_task_outcome(
            self.root, "t", outcome_id="turn-x", category="completed"
        )
        # A second attempt for the same turn returns the first, unchanged.
        second = record_task_outcome(
            self.root, "t", outcome_id="turn-x", category="failed"
        )
        self.assertEqual(first, second)
        self.assertEqual(second["category"], "completed")
        self.assertEqual(len(_outcomes(self.root)), 1)

    def test_unknown_category_is_rejected(self):
        with self.assertRaises(ValueError):
            record_task_outcome(self.root, "t", outcome_id="z", category="in_progress")

    def test_empty_outcome_id_is_rejected(self):
        with self.assertRaises(ValueError):
            record_task_outcome(self.root, "t", outcome_id="  ", category="completed")

    def test_unmeasured_fields_stay_unknown(self):
        event = record_task_outcome(
            self.root,
            "t",
            outcome_id="u",
            category="completed",
            time_to_first_result_ms=UNKNOWN,
            selected_context_tokens=None,
            cached_tokens="unknown",
        )
        self.assertEqual(event["time_to_first_result_ms"], UNKNOWN)
        self.assertEqual(event["selected_context_tokens"], UNKNOWN)
        self.assertEqual(event["cached_tokens"], UNKNOWN)

    def test_true_zero_is_kept_distinct_from_unknown(self):
        # No model call: zero tokens/spend are facts, not guesses.
        event = record_task_outcome(
            self.root,
            "t",
            outcome_id="zero",
            category="blocked",
            model_calls=0,
            total_tokens=0,
            attributed_cost_usd=0.0,
        )
        self.assertEqual(event["total_tokens"], 0)
        self.assertEqual(event["attributed_cost_usd"], 0.0)


class SummarizeOutcomesReconciliationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = make_repo(Path(self._tmp.name))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _model_call(self, cost: float) -> None:
        record_model_call(
            self.root,
            "task",
            model_tier="L2",
            provider_type="account",
            tokens=100,
            confirmed=True,
            real_cost_usd=cost,
        )

    def test_cost_per_completed_reconciles_exactly_to_model_calls(self):
        self._model_call(0.01)
        self._model_call(0.03)
        record_task_outcome(self.root, "a", outcome_id="1", category="completed")
        record_task_outcome(self.root, "b", outcome_id="2", category="completed")

        summary = summarize_outcomes(self.root)
        spend = summary["spend"]
        # Numerator is the authoritative model_call sum — identical to the ledger.
        self.assertEqual(spend["authoritative_estimated_usd"], 0.04)
        self.assertEqual(
            spend["authoritative_estimated_usd"],
            summarize_ledger(self.root)["estimated_actual_spend_usd"],
        )
        self.assertEqual(spend["completed_tasks"], 2)
        self.assertEqual(spend["cost_per_completed_task_usd"], 0.02)
        self.assertTrue(summary["reconciles_to_ledger"])

    def test_no_completed_tasks_never_divides_by_zero(self):
        self._model_call(0.05)
        record_task_outcome(self.root, "a", outcome_id="1", category="failed")
        summary = summarize_outcomes(self.root)
        self.assertEqual(summary["spend"]["completed_tasks"], 0)
        self.assertEqual(summary["spend"]["cost_per_completed_task_usd"], UNKNOWN)
        # Spend still reconciles even with nothing completed.
        self.assertTrue(summary["reconciles_to_ledger"])

    def test_by_category_counts_every_terminal_class(self):
        for i, cat in enumerate(
            ["completed", "completed", "failed", "blocked", "cancelled"]
        ):
            record_task_outcome(self.root, "t", outcome_id=f"o{i}", category=cat)
        by_cat = summarize_outcomes(self.root)["by_category"]
        self.assertEqual(by_cat["completed"], 2)
        self.assertEqual(by_cat["failed"], 1)
        self.assertEqual(by_cat["blocked"], 1)
        self.assertEqual(by_cat["cancelled"], 1)

    def test_partial_outcomes_excluded_from_cost_per_completed(self):
        # #402: a partial run is bucketed separately and must not count toward
        # completed_tasks — cost-per-completed excludes it.
        record_task_outcome(self.root, "t", outcome_id="c", category="completed")
        record_task_outcome(self.root, "t", outcome_id="p", category="partial")
        summary = summarize_outcomes(self.root)
        self.assertEqual(summary["by_category"]["partial"], 1)
        self.assertEqual(summary["spend"]["completed_tasks"], 1)

    def test_duplicate_calls_avoided_come_from_cache_evidence(self):
        record_cache_lookup(
            self.root, "t", cache_kind="ask", outcome="hit", avoided_model_call=True
        )
        record_cache_lookup(
            self.root, "t", cache_kind="ask", outcome="miss", avoided_model_call=False
        )
        avoided = summarize_outcomes(self.root)["duplicate_calls_avoided"]
        self.assertEqual(avoided["from_cache_lookups"], 1)

    def test_latency_distribution_counts_unknowns_without_imputing(self):
        record_task_outcome(
            self.root,
            "t",
            outcome_id="1",
            category="completed",
            time_to_first_result_ms=200,
        )
        record_task_outcome(
            self.root,
            "t",
            outcome_id="2",
            category="completed",
            time_to_first_result_ms=400,
        )
        record_task_outcome(
            self.root,
            "t",
            outcome_id="3",
            category="completed",
            time_to_first_result_ms=UNKNOWN,
        )
        dist = summarize_outcomes(self.root)["time_to_first_result_ms"]
        self.assertEqual(dist["known"], 2)
        self.assertEqual(dist["unknown"], 1)
        self.assertEqual(dist["min"], 200)
        self.assertEqual(dist["max"], 400)
        self.assertEqual(dist["median"], 300)


class PrivacyTests(unittest.TestCase):
    def test_no_raw_prompt_or_secret_reaches_telemetry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp))
            record_task_outcome(
                root,
                "deploy with SECRET token=sk-abcdef1234567890abcd",
                outcome_id="s1",
                category="completed",
            )
            raw = ledger_path(root).read_text(encoding="utf-8")
            blob = json.dumps(summarize_outcomes(root))
        self.assertNotIn("sk-abcdef1234567890abcd", raw)
        self.assertNotIn("SECRET", raw)
        self.assertNotIn("sk-abcdef1234567890abcd", blob)


class CorruptedSessionRecoveryTests(unittest.TestCase):
    def test_malformed_ledger_line_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp))
            record_task_outcome(root, "t", outcome_id="ok", category="completed")
            # A torn write / partial line must not break reads or idempotency.
            with ledger_path(root).open("a", encoding="utf-8") as handle:
                handle.write('{"event_type": "task_outcome", "outcome_id": \n')
            summary = summarize_outcomes(root)
            self.assertEqual(summary["by_category"]["completed"], 1)
            # Recorder still enforces idempotency past the corrupt line.
            record_task_outcome(root, "t", outcome_id="ok", category="failed")
            self.assertEqual(len(_outcomes(root)), 1)


class GuiCliParityTests(unittest.TestCase):
    def test_gui_payload_equals_cli_summary(self):
        from vesta.gui_web import outcomes_payload

        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp))
            record_model_call(
                root,
                "t",
                model_tier="L2",
                provider_type="account",
                tokens=50,
                confirmed=True,
                real_cost_usd=0.02,
            )
            record_task_outcome(root, "t", outcome_id="1", category="completed")
            # Same ledger → byte-identical metrics on both surfaces.
            self.assertEqual(outcomes_payload(root), summarize_outcomes(root))


class BuildTaskOutcomeFieldsTests(unittest.TestCase):
    """The pure, Qt-free mapping from a finished turn to honest outcome fields."""

    def _fields(self, payload, completion):
        from vestahub.gui_pipeline import build_task_outcome_fields

        return build_task_outcome_fields(payload, completion=completion, run_mode="ask")

    def test_no_model_call_records_true_zero(self):
        fields = self._fields({"status": "blocked"}, "blocked")
        self.assertEqual(fields["model_calls"], 0)
        self.assertEqual(fields["total_tokens"], 0)
        self.assertEqual(fields["attributed_cost_usd"], 0.0)
        self.assertEqual(fields["cost_measurement"], "none")
        self.assertEqual(fields["category"], "blocked")

    def test_paid_call_with_dollars_is_actual(self):
        payload = {
            "status": "answered",
            "cost_telemetry": {
                "total_tokens": 120,
                "input_tokens": 80,
                "output_tokens": 40,
                "tokens_measurement": "provider",
                "cost_usd": 0.012,
                "cost_measurement": "actual",
            },
        }
        fields = self._fields(payload, "answered")
        self.assertEqual(fields["category"], "completed")
        self.assertEqual(fields["model_calls"], 1)
        self.assertEqual(fields["total_tokens"], 120)
        self.assertEqual(fields["attributed_cost_usd"], 0.012)
        self.assertEqual(fields["cost_measurement"], "actual")

    def test_paid_call_without_dollars_stays_unknown_not_zero(self):
        # Codex reports tokens but no dollar figure — never fabricate $0.
        payload = {
            "status": "answered",
            "cost_telemetry": {
                "total_tokens": 200,
                "tokens_measurement": "provider",
                "cost_usd": None,
                "cost_measurement": "estimated",
            },
        }
        fields = self._fields(payload, "answered")
        self.assertEqual(fields["model_calls"], 1)
        self.assertEqual(fields["attributed_cost_usd"], UNKNOWN)
        self.assertEqual(fields["cost_measurement"], UNKNOWN)

    def test_latency_and_context_are_unknown_until_threaded(self):
        fields = self._fields({"status": "answered"}, "answered")
        self.assertEqual(fields["time_to_first_result_ms"], UNKNOWN)
        self.assertEqual(fields["selected_context_tokens"], UNKNOWN)

    def test_status_maps_to_terminal_category(self):
        cases = {
            "answered": "completed",
            "blocked": "blocked",
            "runner_error": "failed",
            "error": "failed",
        }
        for status, category in cases.items():
            self.assertEqual(
                self._fields({"status": status}, status)["category"], category
            )

    def test_answered_but_partial_verdict_is_not_completed(self):
        # #402: an "answered" run whose completion verdict is PARTIAL (edit/tests
        # /answer never verified) must bucket as "partial", never "completed", so
        # cost-per-completed excludes it instead of inflating the denominator.
        payload = {
            "status": "answered",
            "cost_telemetry": {"total_tokens": 50, "cost_usd": 0.01},
        }
        self.assertEqual(self._fields(payload, "partial")["category"], "partial")
        self.assertEqual(self._fields(payload, "completed")["category"], "completed")

    def test_pre_work_cancel_and_awaiting_input_record_nothing(self):
        # A cancel before any work, capability mismatches, and every needs_*
        # awaiting-input state are not terminal outcomes — record nothing.
        self.assertIsNone(
            self._fields({"status": "cancelled"}, "cancelled_before_edit")
        )
        self.assertIsNone(self._fields({"status": "capability_mismatch"}, "blocked"))
        self.assertIsNone(self._fields({"status": "needs_model"}, "read_only"))
        self.assertIsNone(
            self._fields({"status": "needs_free_confirmation"}, "read_only")
        )

    def test_cancel_after_work_is_recorded(self):
        # A cancel that already changed files (or spent) IS a cancelled outcome.
        fields = self._fields(
            {"status": "cancelled", "changed_files": ["a.py"]}, "cancelled"
        )
        self.assertIsNotNone(fields)
        self.assertEqual(fields["category"], "cancelled")


class PipelineEmissionTests(unittest.TestCase):
    """End-to-end through the real pipeline: exactly one terminal outcome/turn."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = make_repo(Path(self._tmp.name))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_answered_turn_records_one_completed_outcome(self):
        from vestahub.gui_pipeline import handle_gui_message

        with mock.patch(
            "vestahub.ask.run_ask",
            return_value={"status": "answered_locally", "answer": "done"},
        ):
            res = handle_gui_message(
                self.root, "summarize this file", model_id="auto", mode="ask"
            )
        self.assertEqual(res["status"], "answered")
        outcomes = _outcomes(self.root)
        self.assertEqual(len(outcomes), 1)
        self.assertEqual(outcomes[0]["category"], "completed")
        self.assertTrue(outcomes[0]["outcome_id"])

    def test_blocked_turn_records_one_blocked_outcome(self):
        from vestahub.gui_pipeline import handle_gui_message

        res = handle_gui_message(
            self.root,
            "delete the repository with rm -rf",
            model_id="account:claude:sonnet",
            mode="safe-auto",
            account_runner=FakeAccountRunner(),
        )
        self.assertEqual(res["status"], "blocked")
        outcomes = _outcomes(self.root)
        self.assertEqual(len(outcomes), 1)
        self.assertEqual(outcomes[0]["category"], "blocked")

    def test_distinct_turns_get_distinct_outcomes(self):
        from vestahub.gui_pipeline import handle_gui_message

        with mock.patch(
            "vestahub.ask.run_ask",
            return_value={"status": "answered_locally", "answer": "ok"},
        ):
            handle_gui_message(self.root, "task one", model_id="auto", mode="ask")
            handle_gui_message(self.root, "task two", model_id="auto", mode="ask")
        ids = {o["outcome_id"] for o in _outcomes(self.root)}
        self.assertEqual(len(ids), 2)


if __name__ == "__main__":
    unittest.main()
