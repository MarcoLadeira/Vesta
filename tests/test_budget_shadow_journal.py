"""#613 Stage 2: budget caps' shadow journal, and the record that means "no ceiling".

Stage 1 named ``vestahub/budget.py`` JOURNAL_OWNED -- "cost_events: budget
ceilings and spend". ``budget.json`` holds the spending ceilings and the panic
flag, and ``budget_gate`` fails closed on them, so this file decides whether a
paid route is allowed to happen at all.

The module arrived in good shape: validated before locking, atomic, and it
already keeps a ``budget.json.bak``. That backup is a hand-rolled single-slot
version of what the journal does properly -- it survives a torn write, but not
a bad value written twice, because the second write overwrites the only copy of
the good one. The journal keeps the whole sequence.

The one trap here is the empty state, for the sixth time in this migration and
with the most expensive failure mode yet. An *unset* cap is ``None``, and
``None`` is a real record: ``_default_caps`` returns it for every ceiling the
policy does not set, and lifting a cap persists ``None`` over a number. A
validator demanding numeric caps would drop exactly the record that says "this
ceiling was removed", leaving the shadow asserting a spending limit the user no
longer has.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from vestahub.budget import (
    budget_contradiction_report,
    budget_path,
    budget_shadow_projection,
    load_budget,
    set_budget,
)


class _BudgetFixture(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)


class ShadowMirrorsCapChangesTests(_BudgetFixture):
    def test_setting_a_cap_is_mirrored_and_the_shadow_agrees(self):
        set_budget(self.root, daily_usd=5.0)

        self.assertEqual(budget_shadow_projection(self.root)["daily_usd_limit"], 5.0)
        self.assertIsNone(budget_contradiction_report(self.root))

    def test_independent_caps_accumulate_on_both_sides(self):
        set_budget(self.root, daily_usd=5.0)
        set_budget(self.root, monthly_usd=50.0)

        shadow = budget_shadow_projection(self.root)

        self.assertEqual(shadow["daily_usd_limit"], 5.0)
        self.assertEqual(shadow["monthly_usd_limit"], 50.0)
        self.assertIsNone(budget_contradiction_report(self.root))

    def test_panic_mode_is_mirrored(self):
        """Panic forces local-only routing; losing it would silently re-enable spend."""

        set_budget(self.root, panic=True)

        self.assertTrue(budget_shadow_projection(self.root)["panic"])
        self.assertIsNone(budget_contradiction_report(self.root))

        set_budget(self.root, panic=False)

        self.assertFalse(budget_shadow_projection(self.root)["panic"])
        self.assertIsNone(budget_contradiction_report(self.root))

    def test_the_shadow_matches_what_the_module_itself_loads(self):
        set_budget(self.root, daily_usd=5.0, monthly_usd=50.0, per_task_usd=1.0)

        shadow = budget_shadow_projection(self.root)
        loaded = load_budget(self.root)

        for key in (
            "daily_usd_limit",
            "monthly_usd_limit",
            "per_task_hard_limit_usd",
            "panic",
        ):
            self.assertEqual(shadow[key], loaded[key], key)

    def test_a_rejected_cap_writes_neither_side(self):
        """Validation happens before the lock, so nothing should be persisted."""

        set_budget(self.root, daily_usd=5.0)
        before = budget_shadow_projection(self.root)

        with self.assertRaises(ValueError):
            set_budget(self.root, daily_usd=float("nan"))

        self.assertEqual(budget_shadow_projection(self.root), before)
        self.assertIsNone(budget_contradiction_report(self.root))


class LiftingACapIsARecordNotAnAbsenceTests(_BudgetFixture):
    """The empty-state trap, sixth occurrence and the costliest.

    A shadow that quietly kept a removed ceiling would report a spending limit
    the user has already lifted.
    """

    def test_removing_a_cap_is_mirrored_as_none_not_dropped(self):
        set_budget(self.root, daily_usd=5.0)
        set_budget(self.root, daily_usd=None)

        # `daily_usd=None` means "no change" to set_budget's signature, so the
        # cap is lifted by writing the file directly the way the policy layer
        # would -- the point is that a None-valued cap survives the mirror.
        path = budget_path(self.root)
        caps = json.loads(path.read_text(encoding="utf-8"))
        caps["daily_usd_limit"] = None
        path.write_text(json.dumps(caps, indent=2, sort_keys=True), encoding="utf-8")
        set_budget(self.root, monthly_usd=50.0)

        shadow = budget_shadow_projection(self.root)

        self.assertIn("daily_usd_limit", shadow)
        self.assertIsNone(shadow["daily_usd_limit"])
        self.assertIsNone(budget_contradiction_report(self.root))

    def test_an_all_unset_budget_is_still_mirrored(self):
        """The first write on a fresh project has every cap at None."""

        set_budget(self.root, panic=False)

        shadow = budget_shadow_projection(self.root)

        self.assertNotEqual(shadow, {}, "an all-unset budget is still a record")
        self.assertIn("daily_usd_limit", shadow)
        self.assertIsNone(budget_contradiction_report(self.root))


class ContradictionReportIsExactTests(_BudgetFixture):
    def test_an_out_of_band_cap_raise_is_reported(self):
        """The scenario #613 exists for: something raised the ceiling directly."""

        set_budget(self.root, daily_usd=5.0)
        path = budget_path(self.root)
        tampered = {**json.loads(path.read_text(encoding="utf-8"))}
        tampered["daily_usd_limit"] = 500.0
        path.write_text(json.dumps(tampered), encoding="utf-8")

        report = budget_contradiction_report(self.root)

        self.assertIsNotNone(report)
        self.assertIn("daily_usd_limit", report["mismatched_fields"])
        self.assertEqual(report["legacy"]["daily_usd_limit"], 500.0)
        self.assertEqual(report["shadow"]["daily_usd_limit"], 5.0)

    def test_panic_being_switched_off_behind_our_back_is_reported(self):
        set_budget(self.root, panic=True)
        path = budget_path(self.root)
        tampered = {**json.loads(path.read_text(encoding="utf-8")), "panic": False}
        path.write_text(json.dumps(tampered), encoding="utf-8")

        report = budget_contradiction_report(self.root)

        self.assertIsNotNone(report)
        self.assertIn("panic", report["mismatched_fields"])

    def test_a_corrupt_budget_file_is_reported_against_a_readable_shadow(self):
        """The case the .bak was invented for -- the journal covers it too."""

        set_budget(self.root, daily_usd=5.0)
        budget_path(self.root).write_text("{not json", encoding="utf-8")

        report = budget_contradiction_report(self.root)

        self.assertIsNotNone(report)
        self.assertEqual(report["legacy"], {})
        self.assertEqual(report["shadow"]["daily_usd_limit"], 5.0)

    def test_a_project_with_no_budget_agrees_as_both_empty(self):
        self.assertEqual(budget_shadow_projection(self.root), {})
        self.assertIsNone(budget_contradiction_report(self.root))


if __name__ == "__main__":  # pragma: no cover - convenience
    unittest.main()
