"""#613 Stage 2: owner_lease's shadow journal, mirrored against the file it shadows.

Stage 1 named ``vestahub/owner_lease.py`` JOURNAL_OWNED -- "leases: ownership and
fencing" -- and #613 asks for a migration, not a cutover: every accepted
acquire/renew is now *also* mirrored into a run_journal (#517) event, from
inside the same lock that made the file decision. The file stays the single
source of truth a caller's return value depends on; the journal is a shadow
being proven correct over real traffic before anything is asked to read from
it instead.

These tests pin three properties:

1. every accepted transition is mirrored, and the shadow replays to the same
   state as the file;
2. a *refused* renewal (stale fence) writes neither side;
3. the comparator is exact -- it does not paper over a real divergence, and a
   shadow-write failure cannot fail the acquire/renew it was mirroring.
"""

from __future__ import annotations

import concurrent.futures
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from vestahub import owner_lease, run_journal, shadow_journal


class ShadowMirrorsAcceptedTransitionsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "resource.json"

    def test_acquire_is_mirrored_and_the_shadow_agrees_with_the_file(self):
        acquired = owner_lease.acquire(self.path)

        self.assertEqual(owner_lease.shadow_journal_projection(self.path), acquired)
        self.assertIsNone(owner_lease.lease_contradiction_report(self.path))

    def test_renewal_is_mirrored_and_the_shadow_agrees_with_the_file(self):
        acquired = owner_lease.acquire(self.path)
        renewed = owner_lease.renew(
            self.path, acquired, now=acquired["acquired_at"] + 5
        )

        self.assertEqual(renewed["fence"], acquired["fence"])
        self.assertGreater(renewed["heartbeat_at"], acquired["heartbeat_at"])
        self.assertEqual(owner_lease.shadow_journal_projection(self.path), renewed)
        self.assertIsNone(owner_lease.lease_contradiction_report(self.path))

    def test_reacquiring_advances_the_fence_on_both_sides_together(self):
        first = owner_lease.acquire(self.path)
        second = owner_lease.acquire(self.path)

        self.assertEqual(second["fence"], first["fence"] + 1)
        self.assertEqual(owner_lease.shadow_journal_projection(self.path), second)
        self.assertIsNone(owner_lease.lease_contradiction_report(self.path))

    def test_a_refused_renewal_writes_neither_the_file_nor_the_shadow(self):
        acquired = owner_lease.acquire(self.path)
        # Fence the original holder out from underneath it.
        owner_lease.acquire(self.path)
        stale_call = dict(acquired)

        before_shadow = owner_lease.shadow_journal_projection(self.path)
        result = owner_lease.renew(self.path, stale_call)

        self.assertNotEqual(result["fence"], acquired["fence"])
        # Nothing was appended for the refusal: the shadow is unchanged.
        self.assertEqual(
            owner_lease.shadow_journal_projection(self.path), before_shadow
        )
        self.assertIsNone(owner_lease.lease_contradiction_report(self.path))

    def test_concurrent_acquisitions_leave_file_and_shadow_agreeing(self):
        """Racing writers still produce one consistent, agreeing final state.

        The legacy file's own lock already serializes these; this pins that
        the shadow mirror preserves whatever order that serialization chose,
        rather than reordering under contention.
        """

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda _: owner_lease.acquire(self.path), range(20)))

        self.assertEqual(owner_lease.current(self.path)["fence"], 20)
        self.assertIsNone(owner_lease.lease_contradiction_report(self.path))


class ShadowWriteFailureNeverFailsTheLeaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "resource.json"

    def test_a_broken_journal_append_does_not_fail_acquire(self):
        with mock.patch.object(run_journal, "append", side_effect=OSError("disk full")):
            acquired = owner_lease.acquire(self.path)

        # The legacy file is still correct and unconditionally granted.
        self.assertEqual(owner_lease.current(self.path), acquired)
        self.assertEqual(acquired["fence"], 1)

    def test_a_broken_journal_append_does_not_fail_renew(self):
        acquired = owner_lease.acquire(self.path)
        with mock.patch.object(run_journal, "append", side_effect=OSError("disk full")):
            renewed = owner_lease.renew(
                self.path, acquired, now=acquired["acquired_at"] + 5
            )

        self.assertEqual(renewed["fence"], acquired["fence"])
        self.assertEqual(owner_lease.current(self.path), renewed)


class ContradictionReportIsExactTests(unittest.TestCase):
    """The comparator must catch a real divergence, not just an absent one."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "resource.json"

    def test_an_out_of_band_file_write_is_reported(self):
        """The scenario #613 exists for: something wrote the file directly.

        This bypasses acquire/renew entirely -- exactly the "individually
        plausible but mutually contradictory truths" #613's own summary
        names. The comparator must not paper over it.
        """
        owner_lease.acquire(self.path)
        tampered = {
            "pid": 999999,
            "boot": "not-a-real-boot-id",
            "acquired_at": 0.0,
            "heartbeat_at": 0.0,
            "fence": 1,
        }
        self.path.write_text(json.dumps(tampered), encoding="utf-8")

        report = owner_lease.lease_contradiction_report(self.path)

        self.assertIsNotNone(report)
        self.assertIn("pid", report["mismatched_fields"])
        self.assertIn("boot", report["mismatched_fields"])
        self.assertEqual(report["legacy"], tampered)

    def test_a_fence_that_merely_differs_by_one_is_not_silently_excused(self):
        """Regression pin: an earlier draft excused any one-apart fence.

        That was wrong -- it would have let a corrupted file with a
        coincidentally-adjacent fence and scrambled identity fields pass as
        "just lagging". The comparator reads under the file's own lock and
        must report this exactly, with no fence-distance tolerance.
        """
        acquired = owner_lease.acquire(self.path)
        corrupted = {**acquired, "fence": acquired["fence"] - 1, "pid": 424242}
        self.path.write_text(json.dumps(corrupted), encoding="utf-8")

        report = owner_lease.lease_contradiction_report(self.path)

        self.assertIsNotNone(report)
        self.assertIn("pid", report["mismatched_fields"])
        self.assertIn("fence", report["mismatched_fields"])

    def test_two_never_acquired_resources_agree_as_both_empty(self):
        untouched = Path(self._tmp.name) / "never-touched.json"

        self.assertIsNone(owner_lease.lease_contradiction_report(untouched))
        self.assertEqual(owner_lease.current(untouched), {})
        self.assertEqual(owner_lease.shadow_journal_projection(untouched), {})


class ReplayDeterminismTests(unittest.TestCase):
    """#613's own acceptance criterion: repeated rebuilds must agree."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "resource.json"

    def test_replay_agrees_with_itself_and_with_load(self):
        acquired = owner_lease.acquire(self.path)
        owner_lease.renew(self.path, acquired, now=acquired["acquired_at"] + 5)
        owner_lease.acquire(self.path)

        # The journal mechanics moved to vestahub.shadow_journal, which this
        # module and worktree_leases each hand-rolled separately first.
        journal_path = shadow_journal.journal_path_for(self.path)
        replay = lambda: run_journal.replay(  # noqa: E731 - two identical reads
            journal_path,
            reduce=shadow_journal._reduce,
            empty=shadow_journal._empty,
            validate=shadow_journal._validator(owner_lease._valid_lease_record),
        )
        first = replay()
        second = replay()

        self.assertEqual(first, second)
        self.assertEqual(first, owner_lease.shadow_journal_projection(self.path))


if __name__ == "__main__":  # pragma: no cover - convenience
    unittest.main()
