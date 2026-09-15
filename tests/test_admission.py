"""Admission: a request enters the runtime exactly once (#295 gate 3).

Gate 3 is worded precisely: *"Duplicate active run: 0 for the same
admission/idempotency key."* Nothing implemented it. `handle_gui_message` minted
a fresh random `turn_id` per call, so the single-flight registry — which keys on
that id — could not recognise two submissions of the same task as one request.

The concrete cost: a double-click, a renderer replaying a pending send after
reconnecting, or a retry issued before the first reply arrived each started a
**second full run** — two provider calls, two charges, two sets of edits racing
over the same files.

The other half of the epic matters just as much: *"consistency while not
limiting user messages and interactions."* Asking the same thing twice on
purpose is a real second request, so these tests pin both directions — the
mechanical duplicate is caught, the deliberate repeat is not.
"""

from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path

from vestahub.admission import (
    ACCEPTED,
    DUPLICATE,
    Admission,
    admission_key,
    admit,
)
from vestahub.session_registry import DONE, SessionRegistry


class AdmissionKeyTests(unittest.TestCase):
    def _key(self, **kw):
        base = {"project_root": "/repo", "task": "fix the parser", "model": "auto"}
        base.update(kw)
        return admission_key(**base)

    def test_the_same_submission_keys_the_same(self) -> None:
        self.assertEqual(self._key(), self._key())

    def test_each_field_that_changes_the_work_changes_the_key(self) -> None:
        base = self._key()
        for field, value in (
            ("project_root", "/other-repo"),
            ("task", "fix the lexer"),
            ("model", "claude:opus"),
            ("mode", "full-auto"),
        ):
            with self.subTest(field=field):
                self.assertNotEqual(base, self._key(**{field: value}))

    def test_incidental_whitespace_does_not_split_one_request(self) -> None:
        # A paste and a retype of the same instruction must not become two runs.
        self.assertEqual(
            self._key(task="fix the parser"), self._key(task="  fix the parser\n")
        )

    def test_interior_text_is_never_normalised_away(self) -> None:
        # Whitespace *inside* the text can be meaningful (code, formatting), so
        # only the edges are stripped.
        self.assertNotEqual(
            self._key(task="a  b"),
            self._key(task="a b"),
        )

    def test_equivalent_spellings_of_one_repository_agree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "sub").mkdir()
            direct = admission_key(project_root=root, task="t")
            indirect = admission_key(project_root=root / "sub" / "..", task="t")
        self.assertEqual(direct, indirect)

    def test_the_key_carries_no_task_text(self) -> None:
        # Keys travel into snapshots and logs; a raw prompt must not ride along.
        key = self._key(task="my api key is sk-secret-value")
        self.assertNotIn("secret", key)
        self.assertTrue(key.startswith("adm_"))

    def test_an_unreadable_root_still_produces_a_stable_key(self) -> None:
        # Resolution can fail; that must degrade to a usable key, not an error.
        weird = "\x00not-a-real-path"
        self.assertEqual(
            admission_key(project_root=weird, task="t"),
            admission_key(project_root=weird, task="t"),
        )


class AdmitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = SessionRegistry()
        self.key = admission_key(project_root="/repo", task="ship it")

    def _admit(self, request_id: str) -> Admission:
        return admit(self.registry, key=self.key, request_id=request_id)

    def test_the_first_submission_is_accepted(self) -> None:
        result = self._admit("r1")
        self.assertEqual(result.decision, ACCEPTED)
        self.assertTrue(result.accepted)
        self.assertEqual(result.request_id, "r1")
        self.assertEqual(self.registry.active_count(), 1)

    def test_a_second_submission_while_the_first_runs_is_a_duplicate(self) -> None:
        # The double-click / reconnect-replay case, and the whole gate.
        self._admit("r1")
        result = self._admit("r2")
        self.assertEqual(result.decision, DUPLICATE)
        self.assertEqual(self.registry.active_count(), 1, "no second run may start")

    def test_a_duplicate_points_at_the_run_already_in_flight(self) -> None:
        # Never a dead end: the surface attaches to the live run so the user
        # sees their answer, rather than being told "already running".
        self._admit("r1")
        result = self._admit("r2")
        self.assertEqual(result.request_id, "r1")
        self.assertEqual(result.existing_request_id, "r1")

    def test_repeating_a_task_after_it_finishes_is_a_real_new_request(self) -> None:
        # "consistency while not limiting user messages and interactions":
        # asking the same thing again later must not be swallowed.
        self._admit("r1")
        self.registry.finish("r1", state=DONE)
        result = self._admit("r2")
        self.assertEqual(result.decision, ACCEPTED)
        self.assertEqual(result.request_id, "r2")

    def test_a_cancelled_run_frees_its_key_immediately(self) -> None:
        # Stop then resend is the most common recovery the user tries.
        self._admit("r1")
        self.registry.cancel("r1")
        self.assertTrue(self._admit("r2").accepted)

    def test_a_different_task_runs_concurrently(self) -> None:
        # Admission must not serialise unrelated work.
        self._admit("r1")
        other = admit(
            self.registry,
            key=admission_key(project_root="/repo", task="something else"),
            request_id="r2",
        )
        self.assertTrue(other.accepted)
        self.assertEqual(self.registry.active_count(), 2)

    def test_readmitting_the_same_request_id_is_not_a_duplicate(self) -> None:
        # Same id means the caller already knows it is one request — that is
        # the pre-existing supersede path, not a duplicate submission.
        self._admit("r1")
        result = self._admit("r1")
        self.assertTrue(result.accepted)

    def test_the_key_is_recorded_on_the_session(self) -> None:
        self._admit("r1")
        self.assertEqual(self.registry.get("r1").admission_key, self.key)
        self.assertEqual(self.registry.snapshot()[0]["admission_key"], self.key)


class _SlowCheckRegistry(SessionRegistry):
    """A registry whose key lookup is slow, to force the dangerous interleaving.

    The window between "is this key free?" and "start the run" is what makes a
    check-then-act admission unsafe. On a fast in-memory path that window is
    sub-microsecond and threads almost never interleave inside it, so a plain
    N-thread race test passes against a *deliberately broken* implementation and
    proves nothing. Widening the check makes the hazard deterministic: it is the
    same window a real pipeline opens whenever the scheduler preempts it.

    Verified against a check-then-act implementation with the same 5ms delay:
    4 concurrent submissions started 4 runs. `claim()` starts exactly 1, because
    the delay happens *inside* the lock it never releases.
    """

    check_delay = 0.005

    def _active_by_key_locked(self, admission_key: str):
        time.sleep(self.check_delay)
        return super()._active_by_key_locked(admission_key)


class ConcurrencyTests(unittest.TestCase):
    """The case a check-then-act implementation would miss."""

    def test_simultaneous_submissions_produce_exactly_one_run(self) -> None:
        # A double-click *is* two near-simultaneous submissions. If admission
        # checked and then started as two steps, both threads could observe an
        # idle key and both launch a paid run. This is the regression that
        # justifies `claim()` living inside the registry's lock.
        registry = _SlowCheckRegistry()
        key = admission_key(project_root="/repo", task="expensive task")
        start = threading.Barrier(12)
        results: list[Admission] = []
        lock = threading.Lock()

        def submit(n: int) -> None:
            start.wait(timeout=10)
            outcome = admit(registry, key=key, request_id=f"r{n}")
            with lock:
                results.append(outcome)

        threads = [threading.Thread(target=submit, args=(i,)) for i in range(12)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)

        self.assertEqual(len(results), 12)
        accepted = [r for r in results if r.accepted]
        self.assertEqual(len(accepted), 1, "exactly one submission may start a run")
        self.assertEqual(registry.active_count(), 1)
        # Every loser points at the one winner, so no caller is left stranded.
        winner = accepted[0].request_id
        for result in results:
            if result.duplicate:
                self.assertEqual(result.request_id, winner)

    def test_distinct_tasks_are_never_serialised_by_admission(self) -> None:
        registry = SessionRegistry()
        start = threading.Barrier(8)

        def submit(n: int) -> None:
            start.wait(timeout=10)
            admit(
                registry,
                key=admission_key(project_root="/repo", task=f"task {n}"),
                request_id=f"r{n}",
            )

        threads = [threading.Thread(target=submit, args=(i,)) for i in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)
        self.assertEqual(registry.active_count(), 8)


class RegistryClaimTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = SessionRegistry()

    def test_claim_returns_none_when_it_started_the_run(self) -> None:
        self.assertIsNone(self.registry.claim("k1", "r1"))

    def test_claim_returns_the_live_request_id_when_it_did_not(self) -> None:
        self.registry.claim("k1", "r1")
        self.assertEqual(self.registry.claim("k1", "r2"), "r1")

    def test_an_empty_key_never_deduplicates(self) -> None:
        # A caller that supplies no key gets the old behaviour, not an
        # accidental collision with every other keyless run.
        self.assertIsNone(self.registry.claim("", "r1"))
        self.assertIsNone(self.registry.claim("", "r2"))
        self.assertEqual(self.registry.active_count(), 2)

    def test_active_by_key_ignores_finished_runs(self) -> None:
        self.registry.claim("k1", "r1")
        self.assertIsNotNone(self.registry.active_by_key("k1"))
        self.registry.finish("r1", state=DONE)
        self.assertIsNone(self.registry.active_by_key("k1"))

    def test_start_without_a_key_leaves_it_unset(self) -> None:
        self.registry.start("r1", "pipeline")
        self.assertIsNone(self.registry.get("r1").admission_key)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
