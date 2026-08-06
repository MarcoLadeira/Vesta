"""#612 AC9: per-alias telemetry so compatibility mappings can be retired.

The criterion is "Legacy mappings have explicit usage telemetry and a
removal criterion."

Both halves already existed *in aggregate*: ``opaihub/legacy_status.py``
counts legacy reads/writes and publishes ``REMOVAL_GATE``. What it could not
answer is *which* of the 50 declared aliases (43 status words, 7 presentation
states) are still reached — so they could only ever be retired as one
all-or-nothing batch, and a single stubborn alias would keep the other 49
alive indefinitely. #612's functional requirement 7 asks for a deletion
plan; a deletion plan needs per-alias evidence.

These tests pin three things: resolution is actually observed at both
chokepoints, canonical (non-alias) values are *not* counted as legacy usage,
and the aggregate surface exposes the per-alias detail rather than growing a
second competing telemetry system.
"""

from __future__ import annotations

import unittest

from opaihub import legacy_alias_telemetry as telemetry
from opaihub.legacy_alias_telemetry import (
    STATE,
    STATUS,
    coverage_report,
    removal_candidates,
    usage_snapshot,
)
from opaihub.run_state import RunState, canonical_for


class ObservationTests(unittest.TestCase):
    def setUp(self) -> None:
        telemetry.reset()

    def tearDown(self) -> None:
        telemetry.reset()

    def test_a_presentation_alias_is_observed_through_the_real_resolver(self) -> None:
        self.assertIs(canonical_for("streaming"), RunState.RUNNING)
        self.assertEqual(usage_snapshot()[STATE].get("streaming"), 1)

    def test_a_canonical_state_is_not_counted_as_legacy_usage(self) -> None:
        """The distinction the whole criterion rests on.

        Counting canonical states here would make every alias look live
        forever and the removal plan permanently unactionable.
        """
        self.assertIs(canonical_for("running"), RunState.RUNNING)
        self.assertEqual(usage_snapshot()[STATE], {})

    def test_repeated_resolution_accumulates(self) -> None:
        for _ in range(3):
            canonical_for("sending")
        self.assertEqual(usage_snapshot()[STATE]["sending"], 3)

    def test_a_legacy_status_is_observed_through_the_import_adapter(self) -> None:
        # The second chokepoint: opaihub/legacy_status.py's status->state map.
        from opaihub.legacy_status import legacy_status_to_result

        legacy_status_to_result({"status": "answered_locally", "schema_version": 1})
        self.assertGreaterEqual(usage_snapshot()[STATUS].get("answered_locally", 0), 1)

    def test_an_unknown_status_is_not_recorded_as_an_alias(self) -> None:
        from opaihub.legacy_status import legacy_status_to_result

        legacy_status_to_result({"status": "not_a_real_status", "schema_version": 1})
        self.assertNotIn("not_a_real_status", usage_snapshot()[STATUS])

    def test_observe_ignores_unknown_families_and_blank_names(self) -> None:
        # Telemetry must never raise on a lifecycle read path.
        telemetry.observe("not_a_family", "x")
        telemetry.observe(STATE, "")
        telemetry.observe(STATE, None)  # type: ignore[arg-type]
        self.assertEqual(usage_snapshot()[STATE], {})

    def test_the_snapshot_is_a_copy_not_the_live_store(self) -> None:
        canonical_for("streaming")
        snapshot = usage_snapshot()
        snapshot[STATE]["streaming"] = 999
        self.assertEqual(usage_snapshot()[STATE]["streaming"], 1)


class RemovalCandidateTests(unittest.TestCase):
    def setUp(self) -> None:
        telemetry.reset()

    def tearDown(self) -> None:
        telemetry.reset()

    def test_every_declared_alias_is_a_candidate_before_anything_runs(self) -> None:
        from opaihub.generated_lifecycle import LEGACY_STATE_MAP, LEGACY_STATUS_MAP

        candidates = removal_candidates()
        self.assertEqual(len(candidates[STATE]), len(LEGACY_STATE_MAP))
        self.assertEqual(len(candidates[STATUS]), len(LEGACY_STATUS_MAP))

    def test_resolving_an_alias_removes_it_from_the_candidate_list(self) -> None:
        self.assertIn("streaming", removal_candidates()[STATE])
        canonical_for("streaming")
        self.assertNotIn("streaming", removal_candidates()[STATE])

    def test_candidates_are_always_declared_aliases(self) -> None:
        # A candidate must be something that actually exists to delete.
        from opaihub.generated_lifecycle import LEGACY_STATE_MAP, LEGACY_STATUS_MAP

        candidates = removal_candidates()
        self.assertTrue(set(candidates[STATE]) <= set(LEGACY_STATE_MAP))
        self.assertTrue(set(candidates[STATUS]) <= set(LEGACY_STATUS_MAP))


class CoverageReportTests(unittest.TestCase):
    def setUp(self) -> None:
        telemetry.reset()

    def tearDown(self) -> None:
        telemetry.reset()

    def test_the_report_counts_reconcile(self) -> None:
        canonical_for("streaming")
        report = coverage_report()
        for family in report["families"].values():
            self.assertEqual(
                family["declared"], family["observed"] + family["unobserved"]
            )

    def test_the_report_quotes_the_existing_gate_rather_than_inventing_one(
        self,
    ) -> None:
        """Two competing removal criteria would be the same class of drift
        this epic exists to remove."""
        from opaihub.legacy_status import REMOVAL_GATE

        self.assertEqual(coverage_report()["removal_gate"], REMOVAL_GATE)

    def test_the_aggregate_surface_exposes_the_per_alias_detail(self) -> None:
        # One place to ask, two granularities — not a second telemetry system.
        from opaihub.legacy_status import legacy_status_usage

        usage = legacy_status_usage()
        self.assertIn("removal_gate", usage)
        self.assertIn("aliases", usage)
        self.assertIn("families", usage["aliases"])
        self.assertEqual(set(usage["aliases"]["families"]), {STATUS, STATE})

    def test_the_report_carries_no_user_data(self) -> None:
        # Alias names are a closed schema vocabulary; nothing else may appear.
        from opaihub.generated_lifecycle import LEGACY_STATE_MAP, LEGACY_STATUS_MAP

        canonical_for("streaming")
        report = coverage_report()
        allowed = set(LEGACY_STATE_MAP) | set(LEGACY_STATUS_MAP)
        for family in report["families"].values():
            self.assertTrue(set(family["removal_candidates"]) <= allowed)


if __name__ == "__main__":
    unittest.main()
