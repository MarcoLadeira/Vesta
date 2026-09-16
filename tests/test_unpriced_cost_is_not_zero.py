"""#619 AC5: an unknown price must be visibly unavailable, never a silent $0.00.

The acceptance criterion is verbatim: "Invalid or missing prices become
unavailable and cannot evaluate to zero."

``cost_model.tier_cost_per_1k`` returns ``0.0`` for a tier it has never heard
of, which is exactly what it returns for a tier that genuinely costs nothing
(``L1``, local execution). Those are opposite facts. ``vestahub.budget._spent``
sums ``estimated_actual_usd`` without consulting anything else, so a paid call
at an unpriced tier contributed ``$0.00`` to the Cost Firewall's daily and
monthly totals — the cap silently stopped applying to it, and nothing said so.

Two ways to reach an unpriced tier, neither exotic:

1. A tier name the cost model does not list. ``ask.py`` takes its tier from
   the model-intelligence registry (``recommended.get("tier", ...)``), which
   is a separate file from the cost model — adding a tier to one without the
   other is enough.
2. A ``cost_model.yaml`` whose ``tier_usd_per_1k_tokens`` omits tiers.
   ``load_cost_model`` shallow-merges over the defaults, so a partial table
   replaces the whole thing rather than filling gaps.

These tests set ``VESTA_HUB_ROOT`` to a temp directory. That matters: the
config resolves through ``loader.hub_root``, which falls back to the
*packaged* ``vestahub/data/hub`` when no ``hub/`` directory is found above the
project. Writing a fixture without redirecting it edits the shipped default —
which is exactly what happened while investigating this, and briefly made
seven unrelated usage tests fail.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
import unittest.mock as mock
from pathlib import Path

from vestahub.budget import budget_status
from vestahub.cost_model import DEFAULT_COST_MODEL, tier_cost, tier_price_known
from vestahub.ledger import ledger_path, read_events, record_model_call

PARTIAL_TIER_TABLE = {"L3": 0.02}


class _IsolatedHub:
    """A temp VESTA_HUB_ROOT with an optional cost model, restored on exit."""

    def __init__(self, cost_model: dict | None = None) -> None:
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
        self._patch = mock.patch.dict(os.environ, {"VESTA_HUB_ROOT": str(hub)})
        self._patch.start()
        return Path(self._tmp.name)

    def __exit__(self, *exc: object) -> None:
        self._patch.stop()
        self._tmp.cleanup()


class TierPriceKnownTests(unittest.TestCase):
    """Pure-function level: is the price known, independent of any file."""

    def test_a_listed_paid_tier_is_known(self) -> None:
        self.assertTrue(tier_price_known("L3", DEFAULT_COST_MODEL))

    def test_an_unknown_tier_name_is_not_known(self) -> None:
        # Reachable without editing anything: the model-intelligence registry
        # names the tier, the cost model prices it, and they are separate.
        self.assertFalse(tier_price_known("L9", DEFAULT_COST_MODEL))
        self.assertFalse(tier_price_known("", DEFAULT_COST_MODEL))

    def test_a_local_tier_is_known_even_when_absent_from_the_table(self) -> None:
        # Local execution costing nothing is the premise, not an estimate.
        # Without this, a partial table would flag every local run — noise on
        # exactly the runs Vesta is most confident about.
        model = dict(DEFAULT_COST_MODEL, tier_usd_per_1k_tokens=PARTIAL_TIER_TABLE)
        self.assertTrue(tier_price_known("L1", model))
        self.assertTrue(tier_price_known("L0", model))

    def test_an_absent_paid_tier_is_not_known(self) -> None:
        model = dict(DEFAULT_COST_MODEL, tier_usd_per_1k_tokens=PARTIAL_TIER_TABLE)
        self.assertFalse(tier_price_known("L2", model))

    def test_a_garbage_or_missing_table_is_not_known(self) -> None:
        self.assertFalse(
            tier_price_known(
                "L2", dict(DEFAULT_COST_MODEL, tier_usd_per_1k_tokens=None)
            )
        )
        self.assertFalse(
            tier_price_known(
                "L2", dict(DEFAULT_COST_MODEL, tier_usd_per_1k_tokens={"L2": "free"})
            )
        )
        self.assertFalse(
            tier_price_known(
                "L2", dict(DEFAULT_COST_MODEL, tier_usd_per_1k_tokens={"L2": True})
            )
        )

    def test_the_ambiguous_zero_that_motivated_this_still_exists(self) -> None:
        # tier_cost must return a float and so cannot signal "unknown" — which
        # is precisely why tier_price_known exists as a separate question.
        model = dict(DEFAULT_COST_MODEL, tier_usd_per_1k_tokens=PARTIAL_TIER_TABLE)
        self.assertEqual(tier_cost("L2", 100_000, model), 0.0)
        self.assertEqual(tier_cost("L1", 100_000, model), 0.0)


class LedgerRecordingTests(unittest.TestCase):
    """Every recorded call says whether its price was actually known."""

    def _record(self, root: Path, tier: str, **kwargs: object) -> dict:
        record_model_call(
            root,
            f"a {tier} task",
            model_tier=tier,
            provider_type=kwargs.pop("provider_type", "cloud"),
            tokens=100_000,
            confirmed=True,
            **kwargs,
        )
        return read_events(root)[-1]

    def test_an_unpriced_paid_call_is_flagged(self) -> None:
        model = dict(DEFAULT_COST_MODEL, tier_usd_per_1k_tokens=PARTIAL_TIER_TABLE)
        with _IsolatedHub(model) as root:
            event = self._record(root, "L2")
        self.assertIs(event["cost_price_known"], False)
        self.assertEqual(event["estimated_actual_usd"], 0.0)

    def test_a_genuinely_free_local_call_is_not_flagged(self) -> None:
        model = dict(DEFAULT_COST_MODEL, tier_usd_per_1k_tokens=PARTIAL_TIER_TABLE)
        with _IsolatedHub(model) as root:
            event = self._record(root, "L1", provider_type="local")
        self.assertIs(event["cost_price_known"], True)
        self.assertEqual(event["estimated_actual_usd"], 0.0)

    def test_a_priced_call_is_unaffected(self) -> None:
        model = dict(DEFAULT_COST_MODEL, tier_usd_per_1k_tokens=PARTIAL_TIER_TABLE)
        with _IsolatedHub(model) as root:
            event = self._record(root, "L3")
        self.assertIs(event["cost_price_known"], True)
        self.assertEqual(event["estimated_actual_usd"], 2.0)

    def test_a_real_provider_cost_needs_no_price_table(self) -> None:
        model = dict(DEFAULT_COST_MODEL, tier_usd_per_1k_tokens=PARTIAL_TIER_TABLE)
        with _IsolatedHub(model) as root:
            event = self._record(root, "L2", real_cost_usd=0.42, measurement="actual")
        self.assertIs(event["cost_price_known"], True)
        self.assertEqual(event["estimated_actual_usd"], 0.42)

    def test_usage_provenance_is_not_overwritten_by_price_availability(self) -> None:
        """These are different facts and must not be conflated.

        A provider can report exact token usage for a tier Vesta has no price
        for. Reporting that window as unmeasured because the *price* was
        unknown is the contradiction #619 AC4 forbids — and doing it broke
        seven existing usage-confidence tests when first attempted.
        """
        model = dict(DEFAULT_COST_MODEL, tier_usd_per_1k_tokens=PARTIAL_TIER_TABLE)
        with _IsolatedHub(model) as root:
            event = self._record(root, "L2", measurement="provider")
        self.assertEqual(event["measurement"], "provider")
        self.assertIs(event["cost_price_known"], False)


class BudgetCompletenessTests(unittest.TestCase):
    """A cap must not be compared against an understatement that looks exact."""

    def test_a_fully_priced_project_reports_a_complete_total(self) -> None:
        with _IsolatedHub() as root:
            record_model_call(
                root,
                "priced",
                model_tier="L3",
                provider_type="cloud",
                tokens=1000,
                confirmed=True,
            )
            status = budget_status(root)
        self.assertTrue(status["spend_completeness"]["complete"])
        self.assertEqual(status["spend_completeness"]["unpriced_calls_month"], 0)

    def test_an_unpriced_call_makes_the_total_explicitly_incomplete(self) -> None:
        model = dict(DEFAULT_COST_MODEL, tier_usd_per_1k_tokens=PARTIAL_TIER_TABLE)
        with _IsolatedHub(model) as root:
            record_model_call(
                root,
                "unpriced paid call",
                model_tier="L2",
                provider_type="cloud",
                tokens=100_000,
                confirmed=True,
            )
            status = budget_status(root)
        self.assertEqual(status["spent"]["today_usd"], 0.0)
        self.assertFalse(status["spend_completeness"]["complete"])
        self.assertEqual(status["spend_completeness"]["unpriced_calls_today"], 1)
        self.assertTrue(
            any(
                "lower" in note and "cost_model.yaml" in note
                for note in status["notes"]
            ),
            "an incomplete total must say so, and say where to fix it",
        )

    def test_events_predating_the_field_are_not_treated_as_unpriced(self) -> None:
        # Absence means "not recorded", not "unpriced" — treating old events
        # as failures would flag every ledger written before this change.
        with _IsolatedHub() as root:
            record_model_call(
                root,
                "priced",
                model_tier="L3",
                provider_type="cloud",
                tokens=1000,
                confirmed=True,
            )
            path = ledger_path(root.expanduser().resolve())
            rewritten = []
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                event = json.loads(line)
                event.pop("cost_price_known", None)
                rewritten.append(json.dumps(event))
            path.write_text("\n".join(rewritten) + "\n", encoding="utf-8")
            status = budget_status(root)
        self.assertTrue(status["spend_completeness"]["complete"])


if __name__ == "__main__":
    unittest.main()
