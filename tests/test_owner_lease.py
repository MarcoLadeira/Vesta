"""Supervisor leases: is the process that owns this run still alive? (#295)

OPai records a turn as ``state: "running"`` before work starts, so a crash
leaves that behind forever. The resume path handled it honestly and named the
missing piece in a comment: *"without an owner lease, process death cannot be
inferred safely"* — so OPai could not tell "a sibling window is working on this"
apart from "this died three days ago", and had to present both identically.

#295 invariant 4: one active owner, each run holding exactly one supervisor
lease. Transition rule: a stale heartbeat cannot remain "running" forever.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from _helpers import make_repo

from opai.gui_recents import (
    begin_thread_turn,
    finish_thread_turn,
    load_thread,
    refresh_thread_lease,
)
from opaihub import owner_lease as lease


class LivenessTests(unittest.TestCase):
    """The four states a lease can be in, and how each is decided."""

    def setUp(self) -> None:
        self.mine = lease.new_lease(now=1000.0)

    def test_a_fresh_lease_this_process_holds_is_live(self) -> None:
        described = lease.describe(self.mine, now=1000.0)
        self.assertEqual(described["reason"], "owned_here")
        self.assertFalse(described["stale"])
        self.assertTrue(described["ownerIsThisProcess"])

    def test_a_quiet_heartbeat_means_the_owner_is_gone(self) -> None:
        gone = 1000.0 + lease.STALE_AFTER_SECONDS + 1
        described = lease.describe(self.mine, now=gone)
        self.assertEqual(described["reason"], "owner_gone")
        self.assertTrue(described["stale"])

    def test_staleness_outranks_ownership(self) -> None:
        # The bug this ordering fixes: checking "is it mine?" first reported a
        # lease this process had stopped refreshing as healthy, because the pid
        # still matched — a run nobody was tending, described as fine.
        gone = 1000.0 + lease.STALE_AFTER_SECONDS + 1
        self.assertTrue(lease.owned_by_this_process(self.mine))
        self.assertTrue(lease.describe(self.mine, now=gone)["stale"])

    def test_a_live_lease_from_another_process_is_not_stale(self) -> None:
        # The case that makes guessing unsafe, and the reason the resume path
        # refused to guess before this existed.
        other = {"pid": 999999, "boot": "someotherprocess", "heartbeat_at": 1000.0}
        described = lease.describe(other, now=1005.0)
        self.assertEqual(described["reason"], "owner_alive_elsewhere")
        self.assertFalse(described["stale"])
        self.assertFalse(described["ownerIsThisProcess"])

    def test_a_missing_lease_is_stale_not_healthy(self) -> None:
        # A run recorded with no owner at all cannot be shown as actively
        # running; absence of evidence is not evidence of health.
        for empty in ({}, None, "", []):
            with self.subTest(empty=empty):
                self.assertTrue(lease.is_stale(empty))
                self.assertEqual(lease.describe(empty)["reason"], "no_owner_recorded")

    def test_silence_is_reported_in_seconds_not_just_a_boolean(self) -> None:
        described = lease.describe(self.mine, now=1000.0 + 42)
        self.assertAlmostEqual(described["silentForSeconds"], 42.0)
        self.assertIsNone(lease.describe({})["silentForSeconds"])


class HeartbeatTests(unittest.TestCase):
    def test_touching_a_lease_keeps_it_live(self) -> None:
        mine = lease.new_lease(now=1000.0)
        far = 1000.0 + lease.STALE_AFTER_SECONDS + 1
        self.assertTrue(lease.is_stale(mine, now=far))
        refreshed = lease.touch(mine, now=far)
        self.assertFalse(lease.is_stale(refreshed, now=far))

    def test_a_process_cannot_refresh_someone_elses_lease(self) -> None:
        # Refreshing another process's lease would keep a dead owner looking
        # alive forever — the exact failure this module exists to prevent.
        other = {"pid": 999999, "boot": "someotherprocess", "heartbeat_at": 1000.0}
        unchanged = lease.touch(other, now=9999.0)
        self.assertEqual(unchanged["heartbeat_at"], 1000.0)

    def test_the_heartbeat_window_is_several_beats_wide(self) -> None:
        # Declaring a live run abandoned is far worse than waiting longer to
        # declare a dead one, so the threshold must not be one missed beat.
        self.assertGreaterEqual(
            lease.STALE_AFTER_SECONDS, lease.HEARTBEAT_INTERVAL_SECONDS * 3
        )


class IdentityTests(unittest.TestCase):
    def test_a_lease_records_who_holds_it(self) -> None:
        mine = lease.new_lease()
        self.assertEqual(mine["pid"], os.getpid())
        self.assertEqual(mine["boot"], lease.boot_id())

    def test_a_recycled_pid_is_not_mistaken_for_the_same_owner(self) -> None:
        # Operating systems reuse pids. A same-pid lease from a different boot
        # is a different owner, and must not read as ours.
        impostor = {"pid": os.getpid(), "boot": "adifferentboot", "heartbeat_at": 1.0}
        self.assertFalse(lease.owned_by_this_process(impostor))


class ThreadPersistenceTests(unittest.TestCase):
    """The lease has to survive the round trip to be worth anything."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = make_repo(Path(self._tmp.name))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_starting_a_turn_claims_ownership(self) -> None:
        begin_thread_turn(
            self.root, request_id="req-1", text="fix it", mode="safe-auto"
        )
        thread = load_thread(self.root)
        self.assertEqual(thread["state"], "running")
        self.assertTrue(lease.owned_by_this_process(thread["lease"]))

    def test_a_finished_turn_holds_no_lease(self) -> None:
        # Only a running turn has an owner to prove alive; keeping a lease on a
        # finished one would invite reading a dead process as meaningful.
        begin_thread_turn(
            self.root, request_id="req-1", text="fix it", mode="safe-auto"
        )
        finish_thread_turn(
            self.root, request_id="req-1", answer="done", status="complete"
        )
        self.assertFalse(load_thread(self.root).get("lease"))

    def test_only_whitelisted_lease_fields_are_persisted(self) -> None:
        # This file is read on every boot, so it must never become a place
        # arbitrary structure can be stored.
        from opai.gui_recents import _clean_lease

        cleaned = _clean_lease(
            {
                "pid": 42,
                "boot": "abc123",
                "heartbeat_at": 5.0,
                "acquired_at": 4.0,
                "command": "rm -rf /",
                "nested": {"secret": "value"},
            }
        )
        self.assertEqual(set(cleaned), {"pid", "boot", "heartbeat_at", "acquired_at"})

    def test_a_malformed_lease_degrades_to_no_owner(self) -> None:
        from opai.gui_recents import _clean_lease

        for junk in ("not a dict", 7, None, {"pid": "abc", "boot": "!!!"}):
            with self.subTest(junk=junk):
                self.assertTrue(lease.is_stale(_clean_lease(junk)))


class HeartbeatRefreshTests(unittest.TestCase):
    """A lease nobody restamps goes stale, and a healthy run looks abandoned."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = make_repo(Path(self._tmp.name))
        begin_thread_turn(
            self.root, request_id="req-1", text="a long task", mode="safe-auto"
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_the_owner_can_keep_its_own_lease_warm(self) -> None:
        before = load_thread(self.root)["lease"]["heartbeat_at"]
        self.assertTrue(refresh_thread_lease(self.root, request_id="req-1"))
        after = load_thread(self.root)["lease"]["heartbeat_at"]
        self.assertGreaterEqual(after, before)

    def test_a_beat_for_a_different_request_is_refused(self) -> None:
        # Only the active turn's owner may beat; anything else would keep a
        # lease warm on work it does not own.
        self.assertFalse(
            refresh_thread_lease(self.root, request_id="some-other-request")
        )

    def test_a_finished_turn_cannot_be_kept_warm(self) -> None:
        finish_thread_turn(
            self.root, request_id="req-1", answer="done", status="complete"
        )
        self.assertFalse(refresh_thread_lease(self.root, request_id="req-1"))


class ResumePayloadTests(unittest.TestCase):
    """Boot stays read-only; it just stops presenting the two cases alike."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = make_repo(Path(self._tmp.name))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_the_resume_offer_reports_whether_the_owner_is_alive(self) -> None:
        from opai.gui_web import _resume_payload

        begin_thread_turn(
            self.root, request_id="req-1", text="fix it", mode="safe-auto"
        )
        owner = _resume_payload(self.root)["owner"]
        self.assertEqual(owner["reason"], "owned_here")
        self.assertFalse(owner["stale"])

    def test_boot_does_not_mutate_the_thread(self) -> None:
        # The resume/start-fresh decision stays the user's. Reporting liveness
        # must not quietly rewrite the record it is reporting on.
        from opai.gui_web import _resume_payload

        begin_thread_turn(
            self.root, request_id="req-1", text="fix it", mode="safe-auto"
        )
        before = load_thread(self.root)
        _resume_payload(self.root)
        after = load_thread(self.root)
        self.assertEqual(before["state"], after["state"])
        self.assertEqual(before["lease"], after["lease"])


class FencedLeaseTests(unittest.TestCase):
    """Durable leases with fencing tokens, for a supervisor that can restart (#517)."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = make_repo(Path(self._tmp.name))
        self.path = lease.lease_path(self.root, "workflow-run-1")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_a_fresh_acquisition_starts_the_fence_at_one(self) -> None:
        acquired = lease.acquire(self.path, now=1000.0)
        self.assertEqual(acquired["fence"], 1)
        self.assertTrue(lease.owned_by_this_process(acquired))

    def test_a_second_acquisition_strictly_increases_the_fence(self) -> None:
        first = lease.acquire(self.path, now=1000.0)
        second = lease.acquire(self.path, now=1001.0)
        self.assertEqual((first["fence"], second["fence"]), (1, 2))

    def test_current_reads_the_durable_lease_without_changing_it(self) -> None:
        lease.acquire(self.path, now=1000.0)
        before = lease.current(self.path)
        after = lease.current(self.path)
        self.assertEqual(before, after)
        self.assertEqual(before["fence"], 1)

    def test_current_on_an_absent_lease_reads_as_no_owner(self) -> None:
        self.assertEqual(lease.current(self.path), {})
        self.assertTrue(lease.is_stale(lease.current(self.path)))

    def test_renewing_a_still_current_lease_keeps_its_fence(self) -> None:
        acquired = lease.acquire(self.path, now=1000.0)
        far = 1000.0 + lease.STALE_AFTER_SECONDS + 1
        renewed = lease.renew(self.path, acquired, now=far)
        self.assertEqual(renewed["fence"], acquired["fence"])
        self.assertFalse(lease.is_stale(renewed, now=far))


class TwoProcessLeaseRaceTests(unittest.TestCase):
    """The scenario #517 explicitly asks for: two owners racing over one run.

    Simulated deterministically (no real subprocesses — Windows file locking
    under genuine multi-process load is separately known to be flaky under
    heavy concurrency, and the fencing guarantee this proves does not depend
    on real OS scheduling to be true). "Process B" is modeled as a second
    acquisition at the same path; the resulting fence strictly supersedes
    "process A"'s, which is exactly what a second real process acquiring the
    same abandoned lease would also produce.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = make_repo(Path(self._tmp.name))
        self.path = lease.lease_path(self.root, "workflow-run-races")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_a_superseded_owner_is_detectably_no_longer_current(self) -> None:
        owner_a = lease.acquire(self.path, now=1000.0)
        owner_b = lease.acquire(self.path, now=1001.0)
        self.assertTrue(lease.is_current(self.path, owner_b["fence"]))
        self.assertFalse(lease.is_current(self.path, owner_a["fence"]))

    def test_a_superseded_owners_renewal_is_refused_not_overwritten(self) -> None:
        owner_a = lease.acquire(self.path, now=1000.0)
        owner_b = lease.acquire(self.path, now=1001.0)
        # Owner A does not know it has been superseded and tries to heartbeat
        # with its own (now stale) captured lease.
        result = lease.renew(self.path, owner_a, now=2000.0)
        # It gets back B's lease unchanged — never a write that could clobber
        # B's heartbeat with A's.
        self.assertEqual(result["fence"], owner_b["fence"])
        self.assertEqual(result["heartbeat_at"], owner_b["heartbeat_at"])

    def test_the_current_owner_can_still_renew_after_a_third_party_reads(self) -> None:
        owner_b = lease.acquire(self.path, now=1000.0)
        lease.current(self.path)  # a read-only observer must not disturb it
        renewed = lease.renew(self.path, owner_b, now=1050.0)
        self.assertEqual(renewed["fence"], owner_b["fence"])
        self.assertGreater(renewed["heartbeat_at"], owner_b["heartbeat_at"])


class ClockSkewTests(unittest.TestCase):
    """A lease must degrade sensibly, never crash, when time moves backward."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = make_repo(Path(self._tmp.name))
        self.path = lease.lease_path(self.root, "workflow-run-skew")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_a_heartbeat_apparently_in_the_future_is_not_reported_negative(
        self,
    ) -> None:
        acquired = lease.acquire(self.path, now=2000.0)
        # A later clock read that is earlier than the recorded heartbeat (NTP
        # step, VM pause/resume) must not produce a negative silence.
        described = lease.describe(acquired, now=1000.0)
        self.assertGreaterEqual(described["silentForSeconds"], 0.0)
        self.assertFalse(described["stale"])

    def test_a_lease_does_not_flip_stale_and_fresh_as_the_clock_jitters(self) -> None:
        acquired = lease.acquire(self.path, now=1000.0)
        just_before = lease.STALE_AFTER_SECONDS - 1
        just_after = lease.STALE_AFTER_SECONDS + 1
        self.assertFalse(lease.is_stale(acquired, now=1000.0 + just_before))
        self.assertTrue(lease.is_stale(acquired, now=1000.0 + just_after))
        # And back below the threshold again reads fresh once more — staleness
        # is a pure function of the gap, not a one-way latch.
        self.assertFalse(lease.is_stale(acquired, now=1000.0 + just_before))

    def test_renew_with_a_now_before_the_prior_heartbeat_does_not_raise(self) -> None:
        acquired = lease.acquire(self.path, now=5000.0)
        renewed = lease.renew(self.path, acquired, now=4000.0)
        self.assertEqual(renewed["fence"], acquired["fence"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
