"""#685: an unresolved call must stop being an *open item* eventually.

Before this, `active_calls` had no expiry and no sweep, so a call dispatched
200ms ago and one orphaned by a crash last week were the same record. Nothing
could safely gate on the set: one crashed run would have required confirmation
on every paid route forever, unclearable short of hand-editing the ledger.

The five cases the issue asks for are pinned here -- fresh call not aged,
abandoned call aged, aged call still reported, aged call no longer blocking,
and crash/restart with calls from a dead process -- plus the invariant that
ties them together: ageing changes gating eligibility, never honesty.
"""

from __future__ import annotations

import tempfile
import unittest
import unittest.mock as mock
from datetime import datetime, timedelta, timezone
from pathlib import Path

from opaihub.budget import budget_gate, budget_status, set_budget
from opaihub.call_reconciliation import (
    ABANDON_AFTER_SECONDS,
    RUNTIME_ID,
    CallLiveness,
)
from opaihub.ledger import (
    EVENT_MODEL_CALL_ABANDONED,
    abandoned_model_calls,
    cost_reconciliation,
    ledger_head_path,
    outstanding_model_calls,
    read_events,
    reconcile_abandoned_calls,
    record_model_call_finalized,
    record_model_call_started,
    summarize_ledger,
    unresolved_model_calls,
)
from opaihub.savings import build_savings_report, render_savings_markdown
from opaihub.usage import build_usage_snapshots
from opaihub.usage_report import ProviderTurnUsage

USAGE = ProviderTurnUsage.from_provider(
    turn_index=1, total=100, input_tokens=60, output_tokens=40
)


def _start(root: Path, call_id: str, *, turn_index: int = 1) -> dict:
    return record_model_call_started(
        root,
        "task",
        call_id=call_id,
        run_id="run",
        turn_index=turn_index,
        model_id="m",
        provider_id="p",
        model_tier="L3",
        provider_type="cloud",
        confirmed=True,
    )


def _orphan(root: Path, call_id: str = "lost", *, pid: int = 999_000) -> None:
    """Start a call and rewrite its owner to a process that is not us.

    Mirrors the real shape of the bug: the record survives the process that
    made it. Patching the owner at dispatch is the only honest way to build
    that state, since a live process cannot orphan its own call.
    """
    with mock.patch(
        "opaihub.ledger.owner_fields",
        return_value={"owner_pid": pid, "owner_runtime": "a-dead-process"},
    ):
        _start(root, call_id)


def _later(seconds: float) -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=seconds)


def _dead(_pid):
    return False


def _alive(_pid):
    return True


class FreshCallsAreNotAgedTests(unittest.TestCase):
    """Case 1, and AC4."""

    def test_a_call_just_dispatched_is_still_outstanding(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _start(root, "c1")
            self.assertEqual(len(outstanding_model_calls(root)), 1)
            self.assertEqual(reconcile_abandoned_calls(root), [])

    def test_our_own_call_survives_a_sweep_from_the_far_future(self) -> None:
        """A long turn of ours must never be retired underneath it."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _start(root, "c1")
            retired = reconcile_abandoned_calls(
                root, now=_later(ABANDON_AFTER_SECONDS * 50)
            )
            self.assertEqual(retired, [])
            self.assertEqual(len(unresolved_model_calls(root)), 1)

    def test_the_dispatch_record_carries_its_owner(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            started = _start(root, "c1")
            self.assertEqual(started["owner_runtime"], RUNTIME_ID)
            self.assertIsInstance(started["owner_pid"], int)


class AbandonedCallsAreAgedTests(unittest.TestCase):
    """Case 2."""

    def test_an_old_call_from_another_process_is_retired(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _orphan(root)
            retired = reconcile_abandoned_calls(
                root, now=_later(ABANDON_AFTER_SECONDS + 60)
            )
            self.assertEqual(len(retired), 1)
            self.assertEqual(retired[0]["event_type"], EVENT_MODEL_CALL_ABANDONED)
            self.assertEqual(
                retired[0]["abandon_reason"], CallLiveness.ABANDONED_EXPIRED.value
            )
            self.assertEqual(unresolved_model_calls(root), [])

    def test_the_sweep_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _orphan(root)
            when = _later(ABANDON_AFTER_SECONDS + 60)
            self.assertEqual(len(reconcile_abandoned_calls(root, now=when)), 1)
            self.assertEqual(reconcile_abandoned_calls(root, now=when), [])
            self.assertEqual(len(abandoned_model_calls(root)), 1)

    def test_a_finalized_call_is_never_swept(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _start(root, "c1")
            record_model_call_finalized(root, "task", call_id="c1", usage=USAGE)
            self.assertEqual(
                reconcile_abandoned_calls(root, now=_later(ABANDON_AFTER_SECONDS * 9)),
                [],
            )
            self.assertEqual(abandoned_model_calls(root), [])


class AgeingNeverInventsACostTests(unittest.TestCase):
    """AC2: preserve #619 AC5's 'unavailable, never zero'."""

    def test_the_abandoned_record_has_no_cost(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _orphan(root)
            [event] = reconcile_abandoned_calls(
                root, now=_later(ABANDON_AFTER_SECONDS + 1)
            )
            self.assertIsNone(event["cost_usd"])
            self.assertEqual(event["cost_measurement"], "unavailable")
            self.assertIs(event["cost_price_known"], False)

    def test_no_abandoned_record_ever_claims_a_number(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for index in range(4):
                _orphan(root, f"lost-{index}")
            reconcile_abandoned_calls(root, now=_later(ABANDON_AFTER_SECONDS + 1))
            for event in abandoned_model_calls(root):
                with self.subTest(call_id=event["call_id"]):
                    self.assertIsNone(event["cost_usd"])

    def test_spend_totals_do_not_move_when_a_call_is_aged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _orphan(root)
            before = summarize_ledger(root)["estimated_actual_spend_usd"]
            reconcile_abandoned_calls(root, now=_later(ABANDON_AFTER_SECONDS + 1))
            after = summarize_ledger(root)["estimated_actual_spend_usd"]
            self.assertEqual(before, after)


class AgedCallsAreStillReportedTests(unittest.TestCase):
    """Case 3 and AC3: ageing changes gating eligibility, not honesty."""

    def _aged(self, root: Path) -> None:
        _orphan(root)
        reconcile_abandoned_calls(root, now=_later(ABANDON_AFTER_SECONDS + 1))

    def test_reconciliation_stays_unverified(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._aged(root)
            report = cost_reconciliation(root)
            self.assertFalse(report["verified"])
            self.assertEqual(report["abandoned_calls"], 1)
            self.assertEqual(report["unaccounted_calls"], 1)

    def test_the_note_names_the_abandoned_call(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._aged(root)
            self.assertIn("abandoned", cost_reconciliation(root)["note"])

    def test_the_detail_survives_for_chasing_the_turn(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._aged(root)
            [detail] = cost_reconciliation(root)["abandoned"]
            self.assertEqual(detail["call_id"], "lost")
            self.assertEqual(detail["run_id"], "run")
            self.assertEqual(detail["provider_id"], "p")
            self.assertTrue(detail["abandon_note"])

    def test_savings_still_reads_as_a_lower_bound(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._aged(root)
            report = build_savings_report(root)
            self.assertFalse(report["reconciliation"]["verified"])

    def test_the_savings_headline_never_says_zero_calls(self) -> None:
        """The regression the split would have caused if left unhandled."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._aged(root)
            report = build_savings_report(root)
            markdown = render_savings_markdown(report)
            self.assertNotIn("0 dispatched call(s)", report["headline"])
            self.assertNotIn("0 dispatched call(s)", markdown)
            self.assertIn("1 dispatched call(s)", markdown)

    def test_settings_model_usage_still_flags_the_model(self) -> None:
        """The third surface AC3 names, and the one that nearly slipped.

        Settings → Model Usage builds its per-model badge from the
        reconciliation detail. Reading only the in-flight half would show a
        reconciled row for a model whose cost was never learned, one field away
        from the same report saying `verified: false`.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._aged(root)
            [snapshot] = build_usage_snapshots(
                root, models=[{"id": "m", "provider": "p"}]
            )
            self.assertEqual(snapshot["unresolvedCalls"], 1)
            self.assertFalse(snapshot["reconciled"])

    def test_budget_status_reports_it_all_month(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._aged(root)
            completeness = budget_status(root)["spend_completeness"]
            self.assertFalse(completeness["complete"])
            self.assertEqual(completeness["abandoned_calls_month"], 1)


class AgedCallsStopBlockingTests(unittest.TestCase):
    """Case 4 and AC1: it never blocks a gate indefinitely."""

    def _reasons(self, gate: dict) -> list[str]:
        return [r for r in gate.get("reasons", []) if "lower bound" in r]

    def test_an_abandoned_call_gates_on_the_day_it_is_found(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            set_budget(root, daily_usd=10.0)
            _orphan(root)
            reconcile_abandoned_calls(root, now=_later(ABANDON_AFTER_SECONDS + 1))
            gate = budget_gate(
                root, next_cost_usd=0.01, tier="L3", provider_type="cloud"
            )
            self.assertTrue(self._reasons(gate))

    def test_the_gate_clears_by_the_next_day(self) -> None:
        """The property that makes this safe to gate on at all.

        The old exclusion existed because an unresolved call never aged out, so
        one crash gated every paid route forever. The window is now a day.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            set_budget(root, daily_usd=10.0)
            _orphan(root)
            reconcile_abandoned_calls(root, now=_later(ABANDON_AFTER_SECONDS + 1))
            tomorrow = datetime.now(timezone.utc) + timedelta(days=1)
            with mock.patch("opaihub.budget.datetime") as clock:
                clock.now.return_value = tomorrow
                gate = budget_gate(
                    root, next_cost_usd=0.01, tier="L3", provider_type="cloud"
                )
            self.assertEqual(self._reasons(gate), [])

    def test_a_call_merely_in_flight_does_not_gate(self) -> None:
        """Normal concurrent operation must not prompt."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            set_budget(root, daily_usd=10.0)
            _start(root, "c1")
            gate = budget_gate(
                root, next_cost_usd=0.01, tier="L3", provider_type="cloud"
            )
            self.assertEqual(self._reasons(gate), [])

    def test_a_local_route_is_never_gated_on_this(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            set_budget(root, daily_usd=10.0)
            _orphan(root)
            reconcile_abandoned_calls(root, now=_later(ABANDON_AFTER_SECONDS + 1))
            gate = budget_gate(
                root, next_cost_usd=0.0, tier="L0", provider_type="local"
            )
            self.assertEqual(self._reasons(gate), [])

    def test_a_clean_ledger_raises_no_reason(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            set_budget(root, daily_usd=10.0)
            gate = budget_gate(
                root, next_cost_usd=0.01, tier="L3", provider_type="cloud"
            )
            self.assertEqual(self._reasons(gate), [])


class CrashAndRestartTests(unittest.TestCase):
    """Case 5: calls left by a process that is gone."""

    def test_a_dead_owner_is_retired_without_waiting_for_the_full_window(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _orphan(root)
            with mock.patch(
                "opaihub.call_reconciliation.pid_is_running", side_effect=_dead
            ):
                retired = reconcile_abandoned_calls(root, now=_later(120))
            self.assertEqual(len(retired), 1)
            self.assertEqual(
                retired[0]["abandon_reason"],
                CallLiveness.ABANDONED_OWNER_GONE.value,
            )

    def test_a_live_owner_from_another_process_is_left_alone(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _orphan(root)
            with mock.patch(
                "opaihub.call_reconciliation.pid_is_running", side_effect=_alive
            ):
                self.assertEqual(reconcile_abandoned_calls(root, now=_later(120)), [])

    def test_a_restart_reports_the_orphan_rather_than_assuming_it_is_live(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _orphan(root)
            with mock.patch(
                "opaihub.call_reconciliation.pid_is_running", side_effect=_dead
            ):
                report = cost_reconciliation(root, now=_later(120))
            self.assertEqual(report["unresolved_calls"], 0)
            self.assertEqual(report["abandoned_calls"], 1)
            self.assertFalse(report["verified"])

    def test_a_late_outcome_after_ageing_still_wins(self) -> None:
        """Ageing is a statement about knowledge, not a lock on the truth."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _orphan(root, "late")
            reconcile_abandoned_calls(root, now=_later(ABANDON_AFTER_SECONDS + 1))
            final = record_model_call_finalized(
                root, "task", call_id="late", usage=USAGE
            )
            self.assertEqual(final["call_id"], "late")
            self.assertEqual(final["total_tokens"], 100)


class ReportingConsistencyTests(unittest.TestCase):
    def test_reporting_sweeps_so_nothing_falls_between_the_two_lists(self) -> None:
        """Without the sweep inside cost_reconciliation this loses the call.

        It would be too old to count as outstanding and not yet recorded as
        abandoned -- unaccounted spend that vanished from the report.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _orphan(root)
            with mock.patch(
                "opaihub.call_reconciliation.pid_is_running", side_effect=_dead
            ):
                report = cost_reconciliation(root, now=_later(120))
            self.assertEqual(report["unaccounted_calls"], 1)

    def test_a_crash_mid_sweep_loses_nothing(self) -> None:
        """The sweep persists the head once per batch, not once per event.

        That is only safe because the ledger file is the record and the head is
        a cache of it: a head whose offset trails the log is detected and the
        tail replayed. Proven here rather than asserted -- the head write is
        made to fail after the events are appended, and the retirements must
        still be there afterwards.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for index in range(3):
                _orphan(root, f"lost-{index}")
            when = _later(ABANDON_AFTER_SECONDS + 1)
            with mock.patch(
                "opaihub.ledger._persist_ledger_head",
                side_effect=OSError("disk gone"),
            ):
                with self.assertRaises(OSError):
                    reconcile_abandoned_calls(root, now=when)
            # Recovery replays the tail: retired, counted, and not re-appended.
            self.assertEqual(len(abandoned_model_calls(root)), 3)
            self.assertEqual(unresolved_model_calls(root), [])
            self.assertEqual(reconcile_abandoned_calls(root, now=when), [])

    def test_the_sweep_reports_the_same_totals_after_a_head_wipe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _orphan(root)
            reconcile_abandoned_calls(root, now=_later(ABANDON_AFTER_SECONDS + 1))
            before = cost_reconciliation(root)["unaccounted_calls"]
            ledger_head_path(root).unlink()
            self.assertEqual(cost_reconciliation(root)["unaccounted_calls"], before)

    def test_unresolved_model_calls_still_returns_the_raw_open_set(self) -> None:
        """The #619 accessor keeps its meaning; #685 adds, never rewrites."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _start(root, "c1")
            self.assertEqual(len(unresolved_model_calls(root)), 1)

    def test_abandoned_events_are_not_counted_as_model_calls(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _orphan(root)
            reconcile_abandoned_calls(root, now=_later(ABANDON_AFTER_SECONDS + 1))
            summary = summarize_ledger(root)
            self.assertEqual(summary["estimated_actual_spend_usd"], 0.0)
            kinds = [e["event_type"] for e in read_events(root)]
            self.assertIn(EVENT_MODEL_CALL_ABANDONED, kinds)

    def test_a_verified_ledger_is_unchanged_by_all_of_this(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _start(root, "c1")
            record_model_call_finalized(root, "task", call_id="c1", usage=USAGE)
            report = cost_reconciliation(root)
            self.assertTrue(report["verified"])
            self.assertEqual(report["unaccounted_calls"], 0)
            self.assertEqual(report["abandoned_calls"], 0)
            self.assertIn("Every dispatched provider call", report["note"])


if __name__ == "__main__":
    unittest.main()
