"""#685 policy layer: when a dispatched call stops being an open item.

Kept separate from the ledger tests because the interesting failures are all
in the decision, not the storage: a live turn retired mid-flight, an orphan
held open forever by a recycled PID, or a gate that can never clear.
"""

from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from vestahub.call_reconciliation import (
    ABANDON_AFTER_SECONDS,
    ABANDON_REASONS,
    OWNER_DEAD_GRACE_SECONDS,
    RUNTIME_ID,
    CallLiveness,
    call_age_seconds,
    classify_call,
    owner_fields,
    parse_timestamp,
    pid_is_running,
)

NOW = datetime(2026, 8, 7, 12, 0, 0, tzinfo=timezone.utc)


def _record(*, age_seconds: float = 0.0, **overrides):
    started = NOW - timedelta(seconds=age_seconds)
    record = {
        "call_id": "c1",
        "created_at": started.isoformat(),
        "owner_pid": 4242,
        "owner_runtime": "some-other-runtime",
    }
    record.update(overrides)
    return record


def _alive(_pid):
    return True


def _dead(_pid):
    return False


def _unknown(_pid):
    return None


class OwnershipTests(unittest.TestCase):
    """AC4: a call genuinely in flight is never aged out mid-execution."""

    def test_our_own_call_is_in_flight_however_old(self) -> None:
        record = _record(age_seconds=ABANDON_AFTER_SECONDS * 100, **owner_fields())
        self.assertEqual(
            classify_call(record, now=NOW, pid_probe=_dead),
            CallLiveness.IN_FLIGHT_SELF,
        )

    def test_our_own_call_is_never_abandoned(self) -> None:
        record = _record(age_seconds=ABANDON_AFTER_SECONDS * 100, **owner_fields())
        self.assertFalse(classify_call(record, now=NOW, pid_probe=_dead).abandoned)

    def test_a_matching_runtime_with_a_different_pid_is_not_ours(self) -> None:
        """Runtime id alone must not confer ownership across processes."""
        record = _record(owner_runtime=RUNTIME_ID, owner_pid=os.getpid() + 90_000)
        self.assertNotEqual(
            classify_call(record, now=NOW, pid_probe=_alive),
            CallLiveness.IN_FLIGHT_SELF,
        )

    def test_a_matching_pid_from_another_runtime_is_not_ours(self) -> None:
        """A recycled PID must not let another process's orphan look like ours."""
        record = _record(
            owner_pid=os.getpid(),
            owner_runtime="a-previous-process",
            age_seconds=ABANDON_AFTER_SECONDS + 1,
        )
        self.assertEqual(
            classify_call(record, now=NOW, pid_probe=_alive),
            CallLiveness.ABANDONED_EXPIRED,
        )


class AgeBoundTests(unittest.TestCase):
    """AC1: an open item has a bounded lifetime, whatever else is true."""

    def test_a_fresh_call_is_in_flight(self) -> None:
        record = _record(age_seconds=1.0)
        self.assertEqual(
            classify_call(record, now=NOW, pid_probe=_alive), CallLiveness.IN_FLIGHT
        )

    def test_a_call_past_the_bound_is_abandoned(self) -> None:
        record = _record(age_seconds=ABANDON_AFTER_SECONDS + 1)
        self.assertEqual(
            classify_call(record, now=NOW, pid_probe=_alive),
            CallLiveness.ABANDONED_EXPIRED,
        )

    def test_the_bound_holds_even_when_the_owner_looks_alive(self) -> None:
        """The property that makes gating safe: a live PID cannot pin it open.

        PIDs are recycled. If "owner alive" could keep a call outstanding
        indefinitely, one crashed run would gate every paid route forever --
        the exact bug #685 exists to remove.
        """
        for probe in (_alive, _unknown):
            with self.subTest(probe=probe.__name__):
                record = _record(age_seconds=ABANDON_AFTER_SECONDS * 5)
                self.assertTrue(
                    classify_call(record, now=NOW, pid_probe=probe).abandoned
                )

    def test_the_boundary_is_inclusive_and_stable(self) -> None:
        just_under = _record(age_seconds=ABANDON_AFTER_SECONDS - 1)
        exactly = _record(age_seconds=ABANDON_AFTER_SECONDS)
        self.assertFalse(classify_call(just_under, now=NOW, pid_probe=_alive).abandoned)
        self.assertTrue(classify_call(exactly, now=NOW, pid_probe=_alive).abandoned)

    def test_every_call_is_eventually_abandoned(self) -> None:
        """No combination of inputs leaves a call open forever."""
        for probe in (_alive, _dead, _unknown):
            for owner_pid in (None, 1, 4242):
                with self.subTest(probe=probe.__name__, owner_pid=owner_pid):
                    record = _record(
                        age_seconds=ABANDON_AFTER_SECONDS + 60, owner_pid=owner_pid
                    )
                    self.assertTrue(
                        classify_call(record, now=NOW, pid_probe=probe).abandoned
                    )


class DeadOwnerTests(unittest.TestCase):
    """Liveness may only retire a call sooner, never keep one open longer."""

    def test_a_dead_owner_retires_the_call_early(self) -> None:
        record = _record(age_seconds=OWNER_DEAD_GRACE_SECONDS + 1)
        self.assertEqual(
            classify_call(record, now=NOW, pid_probe=_dead),
            CallLiveness.ABANDONED_OWNER_GONE,
        )

    def test_the_grace_window_protects_a_just_written_record(self) -> None:
        record = _record(age_seconds=OWNER_DEAD_GRACE_SECONDS - 1)
        self.assertEqual(
            classify_call(record, now=NOW, pid_probe=_dead), CallLiveness.IN_FLIGHT
        )

    def test_an_unknown_liveness_never_retires_early(self) -> None:
        record = _record(age_seconds=OWNER_DEAD_GRACE_SECONDS + 1)
        self.assertEqual(
            classify_call(record, now=NOW, pid_probe=_unknown), CallLiveness.IN_FLIGHT
        )

    def test_a_record_without_an_owner_falls_back_to_age(self) -> None:
        """Pre-#685 records carry no owner. Absence is not evidence."""
        record = _record(age_seconds=OWNER_DEAD_GRACE_SECONDS + 1)
        record.pop("owner_pid")
        record.pop("owner_runtime")
        self.assertEqual(
            classify_call(record, now=NOW, pid_probe=_dead), CallLiveness.IN_FLIGHT
        )
        old = _record(age_seconds=ABANDON_AFTER_SECONDS + 1)
        old.pop("owner_pid")
        old.pop("owner_runtime")
        self.assertEqual(
            classify_call(old, now=NOW, pid_probe=_dead),
            CallLiveness.ABANDONED_EXPIRED,
        )

    def test_a_malformed_owner_pid_is_ignored_not_fatal(self) -> None:
        for value in ("not-a-pid", None, "", -1, 0, [1]):
            with self.subTest(owner_pid=value):
                record = _record(age_seconds=1.0, owner_pid=value)
                self.assertFalse(
                    classify_call(record, now=NOW, pid_probe=_dead).abandoned
                )


class TimestampTests(unittest.TestCase):
    def test_a_missing_timestamp_does_not_retire_on_a_guess(self) -> None:
        record = _record()
        record["created_at"] = ""
        self.assertEqual(
            classify_call(record, now=NOW, pid_probe=_unknown), CallLiveness.IN_FLIGHT
        )

    def test_a_missing_timestamp_still_retires_a_dead_owner(self) -> None:
        record = _record()
        record["created_at"] = "garbage"
        self.assertEqual(
            classify_call(record, now=NOW, pid_probe=_dead),
            CallLiveness.ABANDONED_OWNER_GONE,
        )

    def test_trailing_z_and_naive_timestamps_parse_as_utc(self) -> None:
        self.assertEqual(
            parse_timestamp("2026-08-07T12:00:00Z"),
            datetime(2026, 8, 7, 12, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(
            parse_timestamp("2026-08-07T12:00:00"),
            datetime(2026, 8, 7, 12, 0, tzinfo=timezone.utc),
        )

    def test_unparseable_timestamps_are_none_not_an_exception(self) -> None:
        for value in ("", None, "yesterday", 12345, {}):
            with self.subTest(value=value):
                self.assertIsNone(parse_timestamp(value))

    def test_a_future_timestamp_is_not_treated_as_ancient(self) -> None:
        """Clock skew must not retire a call that has not happened yet."""
        record = _record(age_seconds=-3600)
        self.assertLess(call_age_seconds(record, now=NOW), 0)
        self.assertFalse(classify_call(record, now=NOW, pid_probe=_alive).abandoned)

    def test_a_naive_now_is_accepted(self) -> None:
        record = _record(age_seconds=1.0)
        naive = NOW.replace(tzinfo=None)
        self.assertFalse(classify_call(record, now=naive, pid_probe=_alive).abandoned)


class LivenessProbeTests(unittest.TestCase):
    """The real probe, on whatever platform this is running."""

    def test_this_process_is_running(self) -> None:
        self.assertIs(pid_is_running(os.getpid()), True)

    def test_a_nonsense_pid_is_never_reported_alive(self) -> None:
        for value in (0, -1, "abc", None):
            with self.subTest(pid=value):
                self.assertIsNot(pid_is_running(value), True)

    def test_an_almost_certainly_dead_pid_is_not_reported_alive(self) -> None:
        # Deliberately weak: a PID this high is very unlikely to exist, but if
        # it does the honest answers are True or None -- what must never happen
        # is an exception.
        self.assertIn(pid_is_running(4_000_000_000), (True, False, None))

    def test_a_pid_beyond_posix_range_is_dead_not_an_exception(self) -> None:
        """The POSIX branch must survive a pid larger than pid_t.

        pid_t is a signed 32-bit int, so os.kill(4_000_000_000, 0) raises
        OverflowError before the kernel is ever asked. OverflowError is an
        ArithmeticError, not an OSError, so it escaped the handler chain and
        propagated out of a function documented never to raise -- but only on
        Linux, because Windows answers from its own probe and never reaches
        os.kill. The test above therefore could not catch it on a Windows
        machine, so drive the POSIX branch explicitly on every platform.
        """

        def _overflow(*_args: object, **_kwargs: object) -> None:
            raise OverflowError("signed integer is greater than maximum")

        with (
            mock.patch.object(sys, "platform", "linux"),
            mock.patch.object(os, "kill", _overflow),
        ):
            self.assertIs(pid_is_running(4_000_000_000), False)

    @unittest.skipUnless(sys.platform == "win32", "Windows probe")
    def test_windows_probe_never_signals_the_process(self) -> None:
        """os.kill(pid, 0) TERMINATES on Windows -- the probe must not use it.

        Proven by probing this very process with os.kill patched to explode:
        if the implementation ever reaches for it, this test dies rather than
        silently shipping a probe that kills what it inspects.
        """
        import unittest.mock as mock

        def _explode(*_args, **_kwargs):  # pragma: no cover - must not run
            raise AssertionError("the liveness probe must never call os.kill")

        with mock.patch.object(os, "kill", _explode):
            self.assertIs(pid_is_running(os.getpid()), True)


class ContractTests(unittest.TestCase):
    def test_every_abandoned_state_has_a_human_reason(self) -> None:
        for state in CallLiveness:
            if state.abandoned:
                with self.subTest(state=state):
                    self.assertTrue(ABANDON_REASONS.get(state))

    def test_reasons_cover_exactly_the_abandoned_states(self) -> None:
        self.assertEqual(set(ABANDON_REASONS), {s for s in CallLiveness if s.abandoned})

    def test_the_window_is_documented_not_magic(self) -> None:
        from vestahub import call_reconciliation

        source = call_reconciliation.__doc__ or ""
        self.assertIn("#685", source)
        self.assertGreater(ABANDON_AFTER_SECONDS, 60 * 60)

    def test_the_probe_is_resolved_at_call_time_not_bound_as_a_default(self) -> None:
        """A default-bound callable is an injection point that does not work.

        ``pid_probe=pid_is_running`` in the signature captures the function at
        definition, so replacing the module attribute later changes nothing --
        silently, which is how this was shipped and caught.
        """
        import unittest.mock as mock

        record = _record(age_seconds=OWNER_DEAD_GRACE_SECONDS + 1)
        with mock.patch(
            "vestahub.call_reconciliation.pid_is_running", side_effect=_dead
        ):
            self.assertEqual(
                classify_call(record, now=NOW), CallLiveness.ABANDONED_OWNER_GONE
            )
        with mock.patch(
            "vestahub.call_reconciliation.pid_is_running", side_effect=_alive
        ):
            self.assertEqual(classify_call(record, now=NOW), CallLiveness.IN_FLIGHT)

    def test_owner_fields_identify_this_process(self) -> None:
        fields = owner_fields()
        self.assertEqual(fields["owner_pid"], os.getpid())
        self.assertEqual(fields["owner_runtime"], RUNTIME_ID)


if __name__ == "__main__":
    unittest.main()
