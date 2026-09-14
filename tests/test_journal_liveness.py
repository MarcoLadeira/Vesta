"""#818: telling a run that is being worked on from one whose process died.

The canonical journal held a lease for every admitted run and could not say
whether anyone was still holding it. ``unterminated_runs`` was explicit about
the gap and about whose job it was to close it:

    each row carries the owner and the heartbeat and lets the caller decide,
    because the caller can look at whether that process still exists and this
    module cannot.

The caller could not either. ``owner`` was the *surface* -- ``"gui"`` -- so
every run admitted by every Vesta process on the machine recorded the same
owner, and ``heartbeat_at`` was stamped once at acquisition and never again.

These tests pin the two halves of the fix that matter: the answers it now
gives, and -- more importantly -- the answers it still refuses to give.
"""

from __future__ import annotations

import os
import unittest
from datetime import datetime, timedelta, timezone

from opaihub import journal_liveness, owner_lease
from opaihub.journal_liveness import (
    ACTIONABLE,
    OWNED_HERE,
    OWNER_GONE,
    OWNER_STALE,
    OWNER_UNKNOWN,
    OWNER_UNVERIFIED,
    VERDICTS,
    may_be_alive,
    owner_liveness,
)

FOREIGN_BOOT = "0" * 32
ISO_START = "2026-09-08T10:00:00+00:00"
START = datetime(2026, 9, 8, 10, 0, 0, tzinfo=timezone.utc)


def _lease(**fields):
    return {"owner": "gui", **fields}


class ThisProcessRecognisesItsOwnWorkTests(unittest.TestCase):
    """The one case that is fully decidable, and why."""

    def test_our_own_pid_and_boot_id_is_owned_here(self):
        verdict = owner_liveness(
            _lease(owner_pid=os.getpid(), owner_boot=owner_lease.boot_id())
        )

        self.assertEqual(verdict, OWNED_HERE)

    def test_it_does_not_probe_the_process_table_for_its_own_work(self):
        """Our own identity is proof; asking the OS would be theatre."""

        def explode(pid):  # pragma: no cover - must never be called
            raise AssertionError("probed the process table for our own lease")

        verdict = owner_liveness(
            _lease(owner_pid=os.getpid(), owner_boot=owner_lease.boot_id()),
            is_pid_running=explode,
        )

        self.assertEqual(verdict, OWNED_HERE)

    def test_our_pid_with_a_foreign_boot_id_is_a_reused_pid_not_our_work(self):
        """The teeth of the boot id.

        Without the boot-id half, a recycled pid that happened to match ours
        would read as work this process is doing -- and Vesta would then decline
        to offer recovery for a run nobody is tending.
        """

        verdict = owner_liveness(
            _lease(owner_pid=os.getpid(), owner_boot=FOREIGN_BOOT),
            is_pid_running=lambda pid: True,
        )

        self.assertEqual(verdict, OWNER_UNVERIFIED)

    def test_our_boot_id_with_a_different_pid_is_not_our_work(self):
        verdict = owner_liveness(
            _lease(owner_pid=os.getpid() + 1, owner_boot=owner_lease.boot_id()),
            is_pid_running=lambda pid: False,
        )

        self.assertEqual(verdict, OWNER_GONE)


class AbsenceIsConclusiveAndPresenceIsNotTests(unittest.TestCase):
    """The asymmetry the whole module rests on.

    Pid reuse can make a dead process look alive. It cannot make a live one
    look dead. So "not running" may be stated and "running" may not.
    """

    def test_a_pid_that_is_not_running_is_reported_gone(self):
        verdict = owner_liveness(
            _lease(owner_pid=4242, owner_boot=FOREIGN_BOOT),
            is_pid_running=lambda pid: False,
        )

        self.assertEqual(verdict, OWNER_GONE)

    def test_a_running_pid_is_never_upgraded_to_alive(self):
        verdict = owner_liveness(
            _lease(owner_pid=4242, owner_boot=FOREIGN_BOOT),
            is_pid_running=lambda pid: True,
        )

        self.assertEqual(verdict, OWNER_UNVERIFIED)
        self.assertNotIn(verdict, ACTIONABLE)

    def test_an_unverified_owner_is_not_something_a_recovery_pass_may_act_on(self):
        """Acting on it would cancel work another Vesta is doing."""

        self.assertNotIn(OWNER_UNVERIFIED, ACTIONABLE)
        self.assertNotIn(OWNER_UNKNOWN, ACTIONABLE)
        self.assertEqual(set(ACTIONABLE), {OWNED_HERE, OWNER_GONE})


class WhatItRefusesToAnswerTests(unittest.TestCase):
    def test_a_lease_with_no_process_recorded_is_unknown(self):
        """Every lease written before #818 looks like this."""

        self.assertEqual(owner_liveness(_lease()), OWNER_UNKNOWN)

    def test_a_platform_that_will_not_say_yields_unknown(self):
        verdict = owner_liveness(
            _lease(owner_pid=4242, owner_boot=FOREIGN_BOOT),
            is_pid_running=lambda pid: None,
        )

        self.assertEqual(verdict, OWNER_UNKNOWN)

    def test_a_probe_that_raises_yields_unknown_rather_than_propagating(self):
        """This is read after a crash, which is the worst time to raise."""

        def explode(pid):
            raise OSError("no process table today")

        verdict = owner_liveness(
            _lease(owner_pid=4242, owner_boot=FOREIGN_BOOT), is_pid_running=explode
        )

        self.assertEqual(verdict, OWNER_UNKNOWN)

    def test_a_pid_that_is_not_a_pid_is_unknown(self):
        for value in (0, -1, "", None, "gui", True, 1.5e400):
            with self.subTest(value=value):
                self.assertEqual(
                    owner_liveness(
                        _lease(owner_pid=value, owner_boot=FOREIGN_BOOT),
                        is_pid_running=lambda pid: True,
                    ),
                    OWNER_UNKNOWN,
                )


class MayBeAliveAnswersTheRecoveryQuestionTests(unittest.TestCase):
    """The predicate a recovery pass uses, which is not simply "not gone".

    A recovery pass is about to write a terminal verdict. The asymmetry it
    needs is different from `owner_liveness`'s: failing to reconcile a dead
    run is a nuisance, and reconciling a live one is a false record.
    """

    def test_a_lease_with_no_process_recorded_is_not_alive(self):
        """Absence of evidence, not evidence of life.

        Every lease written before #818 looks like this. Counting them as
        possibly-alive would strand every pre-migration run in `running`
        forever -- the ghost state the recovery sweep exists to clear.
        """

        self.assertFalse(may_be_alive(_lease()))
        self.assertFalse(may_be_alive(_lease(owner_boot=FOREIGN_BOOT)))
        self.assertFalse(may_be_alive(_lease(owner_pid=0)))

    def test_a_process_that_is_gone_is_not_alive(self):
        self.assertFalse(
            may_be_alive(
                _lease(owner_pid=4242, owner_boot=FOREIGN_BOOT),
                is_pid_running=lambda pid: False,
            )
        )

    def test_a_process_that_is_running_may_be_alive(self):
        self.assertTrue(
            may_be_alive(
                _lease(owner_pid=4242, owner_boot=FOREIGN_BOOT),
                is_pid_running=lambda pid: True,
            )
        )

    def test_our_own_work_may_be_alive(self):
        self.assertTrue(
            may_be_alive(
                _lease(owner_pid=os.getpid(), owner_boot=owner_lease.boot_id())
            )
        )

    def test_a_platform_that_will_not_say_counts_as_possibly_alive(self):
        """A recorded owner Vesta cannot read about is not a licence to
        declare it dead -- unlike a lease with no owner at all."""

        self.assertTrue(
            may_be_alive(
                _lease(owner_pid=4242, owner_boot=FOREIGN_BOOT),
                is_pid_running=lambda pid: None,
            )
        )

    def test_only_a_proven_death_makes_it_false(self):
        """Stated as the invariant, so a future edit has to break it visibly."""

        for answer in (True, None):
            with self.subTest(is_pid_running=answer):
                self.assertTrue(
                    may_be_alive(
                        _lease(owner_pid=4242, owner_boot=FOREIGN_BOOT),
                        is_pid_running=lambda pid, a=answer: a,
                    )
                )


class AnOwnerThatStoppedRespondingIsNotTheSameAsOneThatLeftTests(unittest.TestCase):
    """#818: `heartbeat_at` was written at acquisition and by nothing else.

    So a reader could tell that a lease was *held* and never whether anyone was
    still holding it -- which is precisely the difference "cancelled is
    impossible while owned controllable work is still alive" turns on.

    The rule that keeps this honest is that only a heartbeat which *stopped*
    counts. A heartbeat that never moved proves nothing: background runs and
    CLI runs do not beat, and judging them by a clock they never wound would
    report every one of them as dead.
    """

    def _lease(self, *, acquired, heartbeat, pid=4242):
        return {
            "owner": "gui",
            "owner_pid": pid,
            "owner_boot": FOREIGN_BOOT,
            "lease_acquired_at": acquired,
            "lease_heartbeat_at": heartbeat,
        }

    def _verdict(self, lease, *, at, running=True):
        return owner_liveness(
            lease, is_pid_running=lambda pid: running, this_pid=-1, now=at
        )

    def test_a_lease_that_was_never_beaten_is_never_called_stale(self):
        """A surface that does not beat must not be judged by the clock."""

        lease = self._lease(acquired=ISO_START, heartbeat=ISO_START)

        verdict = self._verdict(lease, at=START + timedelta(hours=3))

        self.assertEqual(verdict, OWNER_UNVERIFIED)

    def test_a_recently_beaten_lease_is_not_stale(self):
        lease = self._lease(acquired=ISO_START, heartbeat="2026-09-08T10:05:00+00:00")

        verdict = self._verdict(lease, at=START + timedelta(minutes=5, seconds=10))

        self.assertEqual(verdict, OWNER_UNVERIFIED)

    def test_a_lease_that_was_beaten_and_went_quiet_is_stale(self):
        lease = self._lease(acquired=ISO_START, heartbeat="2026-09-08T10:05:00+00:00")

        verdict = self._verdict(lease, at=START + timedelta(minutes=8))

        self.assertEqual(verdict, OWNER_STALE)

    def test_a_process_that_is_gone_outranks_a_quiet_heartbeat(self):
        """ "Gone" is the more precise answer, so it wins."""

        lease = self._lease(acquired=ISO_START, heartbeat="2026-09-08T10:05:00+00:00")

        verdict = self._verdict(lease, at=START + timedelta(minutes=8), running=False)

        self.assertEqual(verdict, OWNER_GONE)

    def test_our_own_run_is_never_called_stale(self):
        """We are the process reading this; we are plainly alive."""

        lease = {
            "owner": "gui",
            "owner_pid": os.getpid(),
            "owner_boot": owner_lease.boot_id(),
            "lease_acquired_at": ISO_START,
            "lease_heartbeat_at": "2026-09-08T10:05:00+00:00",
        }

        verdict = owner_liveness(lease, now=START + timedelta(days=1))

        self.assertEqual(verdict, OWNED_HERE)

    def test_the_window_is_owner_leases_own(self):
        """One definition of "stale" in the codebase, not two."""

        lease = self._lease(acquired=ISO_START, heartbeat="2026-09-08T10:05:00+00:00")
        just_inside = START + timedelta(
            minutes=5, seconds=owner_lease.STALE_AFTER_SECONDS - 1
        )
        just_outside = START + timedelta(
            minutes=5, seconds=owner_lease.STALE_AFTER_SECONDS + 1
        )

        self.assertEqual(self._verdict(lease, at=just_inside), OWNER_UNVERIFIED)
        self.assertEqual(self._verdict(lease, at=just_outside), OWNER_STALE)

    def test_unreadable_timestamps_are_not_stale(self):
        for acquired, heartbeat in (
            ("", ""),
            (ISO_START, "not a date"),
            ("not a date", ISO_START),
            (None, None),
        ):
            with self.subTest(acquired=acquired, heartbeat=heartbeat):
                lease = self._lease(acquired=acquired, heartbeat=heartbeat)
                self.assertNotEqual(
                    self._verdict(lease, at=START + timedelta(days=1)), OWNER_STALE
                )

    def test_a_recently_stale_owner_is_reported_but_never_acted_on(self):
        """A wedged process may be mid-provider-call. Writing a terminal
        verdict over it is the defect this module exists to stop.

        ``now`` is passed explicitly, and that is the point rather than
        tidiness. The lease carries fixed dates, so reading the wall clock
        made the answer depend on how long ago those dates were -- and once
        `may_be_alive` gained an upper bound on unconfirmable leases, this
        started failing purely because the fixture had aged past it. The claim
        being made is about a *recently* stale owner, so the test has to say
        when "recently" is.
        """

        lease = self._lease(acquired=ISO_START, heartbeat="2026-09-08T10:05:00+00:00")

        self.assertNotIn(OWNER_STALE, ACTIONABLE)
        self.assertTrue(
            may_be_alive(
                lease,
                is_pid_running=lambda pid: True,
                this_pid=-1,
                now=START + timedelta(minutes=10),
            )
        )

    def test_an_owner_stale_since_this_morning_is_recoverable(self):
        """The other half, and the reason the bound exists.

        Without it a stale-but-unconfirmable lease was skipped by recovery
        forever, and the run sat in `running` with no way out.
        """

        lease = self._lease(acquired=ISO_START, heartbeat="2026-09-08T10:05:00+00:00")

        self.assertFalse(
            may_be_alive(
                lease,
                is_pid_running=lambda pid: True,
                this_pid=-1,
                now=START + timedelta(days=1),
            )
        )


class TheVocabularyIsClosedTests(unittest.TestCase):
    """One authority for these words, which is the point of the epic."""

    def test_every_verdict_has_a_sentence(self):
        for verdict in VERDICTS:
            with self.subTest(verdict=verdict):
                self.assertTrue(journal_liveness.describe(verdict))

    def test_the_sentences_are_all_different(self):
        sentences = {journal_liveness.describe(v) for v in VERDICTS}

        self.assertEqual(len(sentences), len(VERDICTS))

    def test_an_unrecognised_verdict_describes_as_unknown(self):
        """A surface must not be able to invent a state by misspelling one."""

        self.assertEqual(
            journal_liveness.describe("definitely_fine"),
            journal_liveness.describe(OWNER_UNKNOWN),
        )

    def test_no_sentence_claims_a_run_is_alive(self):
        """`owner_unverified` must not read as reassurance."""

        self.assertNotIn("running now", journal_liveness.describe(OWNER_UNVERIFIED))
        self.assertIn("may", journal_liveness.describe(OWNER_UNVERIFIED))


class AnUnconfirmableRunIsNotStrandedForeverTests(unittest.TestCase):
    """A guard against lying must not become a way to lose work.

    ``may_be_alive`` is the question recovery asks, and both ``OWNER_STALE``
    and ``OWNER_UNVERIFIED`` counted as possibly-alive with no upper bound. So
    a run whose owning pid was reused by an unrelated process -- ordinary on
    Windows, where pids cycle and restart low after a reboot -- read
    unverified forever, recovery skipped it every single time, and it sat in
    ``running`` with no way out.

    That is exactly the ghost state #613 opens by describing, reintroduced by
    the guard written to prevent it. The asymmetry still holds -- writing a
    terminal verdict onto live work is worse than leaving a dead run around --
    but "leave it around" has to mean *for a while*, not *for ever*.
    """

    def lease(self, *, pid: int, boot: str, heard: float) -> dict:
        moment = (datetime.now(timezone.utc) - timedelta(seconds=heard)).isoformat()
        return {
            "owner_pid": pid,
            "owner_boot": boot,
            "lease_heartbeat_at": moment,
            "lease_acquired_at": moment,
        }

    def test_a_run_heard_from_recently_is_left_alone(self):
        lease = self.lease(pid=os.getpid(), boot="another-interpreter", heard=60)

        self.assertTrue(journal_liveness.may_be_alive(lease))

    def test_a_run_nobody_has_heard_from_all_day_is_recoverable(self):
        lease = self.lease(
            pid=os.getpid(),
            boot="another-interpreter",
            heard=journal_liveness.ABANDONED_AFTER_SECONDS + 60,
        )

        self.assertFalse(
            journal_liveness.may_be_alive(lease),
            "an unconfirmable run must not be stranded permanently",
        )

    def test_the_bound_is_generous_enough_not_to_steal_live_work(self):
        """Hours, not minutes. A long provider call must survive it."""

        self.assertGreaterEqual(
            journal_liveness.ABANDONED_AFTER_SECONDS,
            60 * 60,
            "a bound this tight would reconcile work that is still running",
        )

    def test_a_lease_this_process_holds_is_never_reclaimed_by_the_clock(self):
        """Knowing beats timing out."""

        lease = self.lease(
            pid=os.getpid(),
            boot=owner_lease.boot_id(),
            heard=journal_liveness.ABANDONED_AFTER_SECONDS * 10,
        )

        self.assertEqual(
            journal_liveness.owner_liveness(lease), journal_liveness.OWNED_HERE
        )
        self.assertTrue(journal_liveness.may_be_alive(lease))

    def test_a_lease_with_no_timestamps_is_not_judged_by_the_clock(self):
        """Absence of a date is not evidence of abandonment."""

        lease = {"owner_pid": os.getpid(), "owner_boot": "another-interpreter"}

        self.assertTrue(journal_liveness.may_be_alive(lease))

    def test_a_run_that_never_beat_is_dated_by_when_it_was_acquired(self):
        """The shape recovery actually meets, and the one teeth-testing found.

        Background runs and CLI runs do not stamp a heartbeat -- only the GUI
        pipeline beats one. So for exactly the runs a recovery pass cares most
        about there is no heartbeat to age, and dating a lease by the
        heartbeat alone would leave them unjudgeable forever: the ghost state
        returning by the back door.

        Acquisition is a real signal. It is the moment somebody was
        demonstrably there.
        """

        long_ago = (
            datetime.now(timezone.utc)
            - timedelta(seconds=journal_liveness.ABANDONED_AFTER_SECONDS + 60)
        ).isoformat()
        lease = {
            "owner_pid": os.getpid(),
            "owner_boot": "another-interpreter",
            "lease_acquired_at": long_ago,
            # No heartbeat. This surface never stamps one.
            "lease_heartbeat_at": "",
        }

        self.assertFalse(
            journal_liveness.may_be_alive(lease),
            "a run that never beat must still be datable by its acquisition",
        )

    def test_a_run_that_never_beat_but_started_recently_is_left_alone(self):
        recent = (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat()
        lease = {
            "owner_pid": os.getpid(),
            "owner_boot": "another-interpreter",
            "lease_acquired_at": recent,
            "lease_heartbeat_at": "",
        }

        self.assertTrue(journal_liveness.may_be_alive(lease))

    def test_a_dead_owner_is_still_recovered_immediately(self):
        """The bound must not delay the case that was already answerable."""

        lease = self.lease(pid=999_999, boot="another-interpreter", heard=1)

        self.assertFalse(journal_liveness.may_be_alive(lease))


if __name__ == "__main__":  # pragma: no cover - convenience
    unittest.main()


class TheHeartbeatInvariantIsLoadBearingTests(unittest.TestCase):
    """A surface that never beats must not read as "stopped responding".

    `_stopped_responding` decides whether anyone was ever tending a lease by
    asking whether the heartbeat has moved past the acquisition. Most surfaces
    never beat one -- only the GUI pipeline does -- so that check is the only
    thing keeping every CLI and background run from being reported as wedged.

    It holds because `acquire_lease` stamps both columns from a *single* value.
    Two reads of the clock would differ by microseconds, which is enough to
    make every lease in the database look tended, then stale, then stopped.
    Flagged in review as working by construction rather than by design, so it
    is pinned here: this fails loudly if the coupling is ever broken.
    """

    def test_a_freshly_acquired_lease_reports_no_heartbeat_movement(self):
        import tempfile
        from pathlib import Path

        from opaihub import journal_store

        root = Path(tempfile.mkdtemp())
        store = journal_store.open_store(root)
        try:
            store.execute(
                "INSERT INTO tasks(task_id, origin_surface, origin_session,"
                " created_at, requested_outcome, schema_version, updated_at)"
                " VALUES ('t', 'cli', '', ?, 'x', 1, ?)",
                (ISO_START, ISO_START),
            )
            store.execute(
                "INSERT INTO runs(run_id, task_id, attempt, desired_state,"
                " observed_state, created_at, updated_at)"
                " VALUES ('r', 't', 1, 'queued', 'queued', ?, ?)",
                (ISO_START, ISO_START),
            )
            store.commit()
            journal_store.acquire_lease(
                store, run_id="r", owner="cli", now=ISO_START, owner_pid=4242
            )
            row = store.execute(
                "SELECT acquired_at, heartbeat_at FROM leases WHERE run_id = 'r'"
            ).fetchone()
        finally:
            store.close()

        self.assertEqual(
            row["acquired_at"],
            row["heartbeat_at"],
            "acquire_lease read the clock twice; every never-beating lease "
            "will now be reported as having stopped responding",
        )

    def test_such_a_lease_is_never_called_stale(self):
        """The consequence, stated as the behaviour rather than the storage."""

        lease = {
            "owner_pid": 4242,
            "owner_boot": "another-interpreter",
            "lease_acquired_at": ISO_START,
            "lease_heartbeat_at": ISO_START,
        }

        verdict = owner_liveness(
            lease,
            is_pid_running=lambda pid: True,
            this_pid=-1,
            now=START + timedelta(days=1),
        )

        self.assertNotEqual(
            verdict,
            OWNER_STALE,
            "a run whose surface never beats is not a run that stopped",
        )


class AProcessIdMustActuallyBeOneTests(unittest.TestCase):
    """`positive_pid` decides what gets probed for liveness.

    Found while fixing a mypy error that a `# type: ignore` was silently not
    suppressing. `int(True)` is 1, and pid 1 exists on every system Vesta runs
    on -- so a lease carrying a boolean would have been probed as a live
    process and reported as one. Exactly the meaningless question with a
    meaningful-looking answer the function's own docstring exists to prevent.
    """

    def test_a_boolean_is_not_a_process_id(self):
        from opaihub.call_reconciliation import positive_pid as _positive_pid

        self.assertIsNone(
            _positive_pid(True),
            "True converts to pid 1, which exists everywhere and would be "
            "probed as a live owner",
        )
        self.assertIsNone(_positive_pid(False))

    def test_a_float_is_not_truncated_into_a_process_id(self):
        """Truncating 2.9 to pid 2 invents an identity nobody recorded."""

        from opaihub.call_reconciliation import positive_pid as _positive_pid

        self.assertIsNone(_positive_pid(2.9))

    def test_real_process_ids_still_work(self):
        from opaihub.call_reconciliation import positive_pid as _positive_pid

        self.assertEqual(_positive_pid(4242), 4242)
        self.assertEqual(_positive_pid("4242"), 4242)

    def test_impossible_ids_are_absent_rather_than_stored(self):
        from opaihub.call_reconciliation import positive_pid as _positive_pid

        for value in (0, -1, None, object(), float("inf")):
            with self.subTest(value=value):
                self.assertIsNone(_positive_pid(value))

    def test_every_caller_agrees_on_what_a_process_id_is(self):
        """There were three validators and they disagreed.

        ``pid_is_running(True)`` probed pid 1, which always exists, and
        ``journal_liveness`` truncated 2.9 to pid 2. Now there is one, and
        every place that asks gets its answer.
        """

        from opaihub import call_reconciliation, journal_liveness
        from opaihub.call_reconciliation import positive_pid as _positive_pid

        for value in (True, False, 2.9, 0, -1, None, float("inf"), "x"):
            with self.subTest(value=value):
                self.assertIsNone(_positive_pid(value))
                self.assertIsNone(call_reconciliation.pid_is_running(value))
                self.assertEqual(
                    journal_liveness.owner_liveness({"owner_pid": value}),
                    journal_liveness.OWNER_UNKNOWN,
                )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
