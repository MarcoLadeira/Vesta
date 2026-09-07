"""#619 AC8: the budget ceiling cannot be enforced against a lower bound.

Criterion: "Budget protection fails closed or requires explicit policy when
authoritative maximum is unknown."

``opaihub.budget.budget_gate`` step 3 compares ``spent + next_cost`` against
the daily/monthly cap. But ``_spent`` sums ``estimated_actual_usd``, which
omits real spend, so it is a **lower bound**. Comparing a lower bound
against a cap under-triggers the ceiling: actual spend can already be over
the limit while the arithmetic reports room to spare. That is exactly the
"authoritative maximum is unknown" case the criterion names.

The gate already fails closed on an unreadable budget file (deny) and on a
degraded cost model (confirm). Incomplete spend accounting is the same shape
as the latter — the figure is *understated*, not *absent* — so it escalates
to confirm, which is also the criterion's own "requires explicit policy".

**Scope, and the judgement in it.** Only *today's* unpriced calls gate. Two
deliberate exclusions, both pinned by tests below:

* Not the month — a daily window clears on its own, so a repaired cost model
  stops the prompt tomorrow at the latest.
* Not ``cost_reconciliation``'s unresolved calls, though they are the same
  kind of blind spot. Those live in the ledger head's ``active_calls`` and
  never age out, so gating on them would make one crashed run require
  confirmation for every paid route forever, with no way to clear it. A
  permanent prompt is not a safety feature — it trains people to click
  through. They are still *reported* by ``budget_status`` and
  ``opai savings``, which is the half that costs nothing.

Isolation note: these set ``OPAI_HUB_ROOT``. ``loader.hub_root`` otherwise
falls back to the packaged ``opaihub/data/hub``, and a fixture written
without redirecting it edits the shipped cost model in the working tree.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
import unittest.mock as mock
from pathlib import Path

from datetime import datetime, timezone

from opaihub.budget import budget_gate, budget_status, set_budget
from opaihub.cost_model import DEFAULT_COST_MODEL
from opaihub.ledger import record_model_call, record_model_call_started

# Drops L2, so an L2 call has no known price and records $0.00.
PARTIAL_COST_MODEL = dict(DEFAULT_COST_MODEL, tier_usd_per_1k_tokens={"L3": 0.02})

LOWER_BOUND_MARKER = "lower bound"


class _Project:
    def __init__(self, *, cost_model: dict | None = None) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._cost_model = cost_model

    def __enter__(self) -> Path:
        hub = Path(self._tmp.name) / "hub"
        (hub / "registry").mkdir(parents=True, exist_ok=True)
        if self._cost_model is not None:
            directory = hub / "model-intelligence"
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "cost_model.yaml").write_text(
                json.dumps(self._cost_model), encoding="utf-8"
            )
        self._patch = mock.patch.dict(os.environ, {"OPAI_HUB_ROOT": str(hub)})
        self._patch.start()
        return Path(self._tmp.name)

    def __exit__(self, *exc: object) -> None:
        self._patch.stop()
        self._tmp.cleanup()


def _record_unpriced_call(root: Path) -> None:
    record_model_call(
        root,
        "task",
        model_tier="L2",  # absent from PARTIAL_COST_MODEL
        provider_type="cloud",
        tokens=1000,
        confirmed=True,
    )


def _record_lost_call(root: Path) -> None:
    # Dispatched and never finalized: real money, unknowable amount.
    record_model_call_started(
        root,
        "task",
        call_id="c:1",
        run_id="c",
        turn_index=1,
        model_id="m",
        provider_id="p",
        model_tier="L3",
        provider_type="cloud",
        confirmed=True,
    )


def _lower_bound_reasons(gate: dict) -> list[str]:
    return [r for r in gate["reasons"] if LOWER_BOUND_MARKER in r]


class IncompleteSpendEscalationTests(unittest.TestCase):
    """Assertions are on the *reason*, not the decision.

    A paid cloud route can already be `confirm` for unrelated policy reasons,
    so asserting only on `decision` would pass without the fix.
    """

    def test_an_unpriced_call_makes_the_ceiling_unenforceable(self) -> None:
        with _Project(cost_model=PARTIAL_COST_MODEL) as root:
            set_budget(root, daily_usd=10.0)
            _record_unpriced_call(root)
            gate = budget_gate(
                root, next_cost_usd=0.01, tier="L3", provider_type="cloud"
            )
        reasons = _lower_bound_reasons(gate)
        self.assertTrue(reasons, "an unpriced call must be surfaced at the gate")
        self.assertIn("no known price", reasons[0])
        self.assertIn(gate["decision"], {"confirm", "deny"})

    def test_the_reason_names_the_file_to_fix(self) -> None:
        # An escalation the user cannot act on is just an obstacle.
        with _Project(cost_model=PARTIAL_COST_MODEL) as root:
            set_budget(root, daily_usd=10.0)
            _record_unpriced_call(root)
            gate = budget_gate(
                root, next_cost_usd=0.01, tier="L3", provider_type="cloud"
            )
        self.assertIn("cost_model.yaml", _lower_bound_reasons(gate)[0])

    def test_a_call_still_in_flight_is_not_gated_on(self) -> None:
        """The original exclusion, narrowed to what it should always have been.

        #619 AC8 excluded every unresolved call, because none of them ever aged
        out: one crashed run would have required confirmation on every paid
        route forever, unclearable short of hand-editing the ledger. #685 gave
        them a bounded lifetime, so the abandoned ones are now gated on (see
        tests/test_abandoned_call_reconciliation.py).

        What stays excluded is a call that is merely *in flight*. A turn
        running right now is normal operation, not a blind spot -- it resolves
        by itself moments later, and prompting on it would fire during ordinary
        concurrent use.
        """
        with _Project() as root:
            set_budget(root, daily_usd=10.0)
            _record_lost_call(root)
            gate = budget_gate(
                root, next_cost_usd=0.01, tier="L3", provider_type="cloud"
            )
        self.assertEqual(_lower_bound_reasons(gate), [])


class NoFalsePositiveTests(unittest.TestCase):
    """The escalation must be narrow, or it becomes noise people click past."""

    def test_a_complete_ledger_raises_no_lower_bound_reason(self) -> None:
        with _Project() as root:
            set_budget(root, daily_usd=10.0)
            record_model_call(
                root,
                "task",
                model_tier="L3",
                provider_type="cloud",
                tokens=1000,
                confirmed=True,
            )
            gate = budget_gate(
                root, next_cost_usd=0.01, tier="L3", provider_type="cloud"
            )
        self.assertEqual(_lower_bound_reasons(gate), [])

    def test_a_local_route_is_never_gated_on_this(self) -> None:
        # Local execution spends nothing, so an incomplete paid total is
        # irrelevant to it.
        with _Project(cost_model=PARTIAL_COST_MODEL) as root:
            set_budget(root, daily_usd=10.0)
            _record_unpriced_call(root)
            gate = budget_gate(
                root, next_cost_usd=0.0, tier="L1", provider_type="local"
            )
        self.assertEqual(_lower_bound_reasons(gate), [])
        self.assertEqual(gate["decision"], "allow")

    def test_a_monthly_only_cap_still_triggers_it(self) -> None:
        with _Project(cost_model=PARTIAL_COST_MODEL) as root:
            set_budget(root, monthly_usd=50.0)
            _record_unpriced_call(root)
            gate = budget_gate(
                root, next_cost_usd=0.01, tier="L3", provider_type="cloud"
            )
        self.assertTrue(_lower_bound_reasons(gate))



class TodaysFigureIsQualifiedByTodaysFactsTests(unittest.TestCase):
    """#818: a hedge that can never clear is one nobody reads.

    `spend_completeness.complete` is deliberately all-time -- `#685` says an
    unaccounted call "still appears here -- permanently" -- which is right for
    the reconciliation report and wrong for qualifying a number labelled
    "today". This module's own header already reached that conclusion for the
    *gate*: "gating on them would make one crashed run require confirmation for
    every paid route forever ... A permanent prompt is not a safety feature."

    The same trap caught the header line. Observed in the running app: "at
    least $0.00 today" on a day with no calls at all, because four operations
    from a fortnight earlier were still open. `complete_today` is the narrower
    answer surfaces should use.
    """

    def test_an_old_unaccounted_call_does_not_hedge_todays_figure(self):
        with _Project() as root:
            set_budget(root, daily_usd=5.0)
            _record_lost_call(root)
            # Age it out of "today" without touching anything else.
            with mock.patch(
                "opaihub.budget.datetime"
            ) as clock:
                clock.now.return_value = datetime(
                    2099, 1, 1, tzinfo=timezone.utc
                )
                completeness = budget_status(root)["spend_completeness"]

        self.assertEqual(completeness["unresolved_calls_today"], 0)
        self.assertTrue(
            completeness["complete_today"],
            "an old open call must not make today's number a lower bound",
        )
        self.assertFalse(
            completeness["complete"],
            "the all-time fact is still true and still reported",
        )

    def test_a_call_dispatched_today_and_still_open_does_hedge_it(self):
        with _Project() as root:
            set_budget(root, daily_usd=5.0)
            _record_lost_call(root)
            completeness = budget_status(root)["spend_completeness"]

        self.assertEqual(completeness["unresolved_calls_today"], 1)
        self.assertFalse(completeness["complete_today"])

    def test_a_clean_day_is_complete(self):
        with _Project() as root:
            set_budget(root, daily_usd=5.0)
            completeness = budget_status(root)["spend_completeness"]

        self.assertTrue(completeness["complete_today"])
        self.assertTrue(completeness["complete"])

    def test_an_unpriced_call_today_still_hedges_it(self):
        """The narrowing must not drop the reason it was built for."""

        with _Project(cost_model=PARTIAL_COST_MODEL) as root:
            set_budget(root, daily_usd=5.0)
            _record_unpriced_call(root)
            completeness = budget_status(root)["spend_completeness"]

        self.assertEqual(completeness["unpriced_calls_today"], 1)
        self.assertFalse(completeness["complete_today"])

if __name__ == "__main__":
    unittest.main()
