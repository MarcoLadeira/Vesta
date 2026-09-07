"""#818: telling a run that is being worked on from one whose process died.

The canonical journal held a lease for every admitted run and could not say
whether anyone was still holding it. ``unterminated_runs`` was explicit about
the gap and about whose job it was to close it:

    each row carries the owner and the heartbeat and lets the caller decide,
    because the caller can look at whether that process still exists and this
    module cannot.

The caller could not either. ``owner`` was the *surface* -- ``"gui"`` -- so
every run admitted by every OPai process on the machine recorded the same
owner, and ``heartbeat_at`` was stamped once at acquisition and never again.

These tests pin the two halves of the fix that matter: the answers it now
gives, and -- more importantly -- the answers it still refuses to give.
"""

from __future__ import annotations

import os
import unittest

from opaihub import journal_liveness, owner_lease
from opaihub.journal_liveness import (
    ACTIONABLE,
    OWNED_HERE,
    OWNER_GONE,
    OWNER_UNKNOWN,
    OWNER_UNVERIFIED,
    VERDICTS,
    owner_liveness,
)

FOREIGN_BOOT = "0" * 32


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
        would read as work this process is doing -- and OPai would then decline
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
        """Acting on it would cancel work another OPai is doing."""

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


if __name__ == "__main__":  # pragma: no cover - convenience
    unittest.main()
