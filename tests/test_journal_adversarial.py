"""#613: adversarial probes against the *assembled* journal stack.

Every other journal suite tests one module against inputs its author thought
of. This one tests the finished stack against inputs nobody thought of, which
is a different activity and finds different things. Four real defects came out
of these probes after all seven stages had shipped green:

* a negative or NaN cost, which would have disabled the budget ceiling it was
  meant to count toward;
* a non-mapping legacy record, which took the whole comparison down with an
  ``AttributeError`` instead of being reported as the difference it is;
* retirement returning ``ready`` against an empty legacy record, where every
  signal is vacuously true and the answer authorises deleting the only
  fallback;
* a blank ``run_id``, where two unrelated runs collapsed into one row and one
  of them vanished with no error raised anywhere.

Each of those now has a regression test beside the code it broke. What lives
*here* is the class of probe that found them: whole-stack invariants, checked
against hostile input, at the seams between modules where no single module's
tests were ever going to look.

The structural tests are deliberate ratchets. ``handle_gui_message`` having
exactly one return is not a style preference -- it is the entire reason Stage 3
can promise a terminal event on every exit, and a future edit that adds a
second return would quietly break that promise everywhere while every existing
test still passed. Asserting the shape is how that stays true.
"""

from __future__ import annotations

import argparse
import ast
import inspect
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any

from opai import cli
from opaihub import gui_pipeline, journal_reader, journal_retirement
from opaihub.journal_runtime import EVENT_FINISHED, record_admission, record_terminal
from opaihub.journal_store import append_event, journal_path, open_store

NOW = "2026-08-25T12:00:00+00:00"


class _StackFixture(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def _migrate(self, count: int, *, verdict: str = "completed") -> dict[str, Any]:
        """``count`` runs present and agreeing in both records."""

        legacy: dict[str, Any] = {}
        for index in range(count):
            run_id = f"run-{index}"
            fence = record_admission(
                self.root, task_id="t", run_id=run_id, task="x", now=NOW
            )
            record_terminal(
                self.root,
                run_id=run_id,
                event_type=EVENT_FINISHED,
                verdict=verdict,
                reason="",
                now=NOW,
                fence=fence,
            )
            legacy[run_id] = {
                "terminal_verdict": verdict,
                "created_at": "2099-01-01T00:00:00+00:00",
            }
        return legacy

    def _verdict(self, run_id: str) -> str | None:
        store = open_store(self.root)
        self.addCleanup(store.close)
        row = store.execute(
            "SELECT terminal_verdict FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        return row[0] if row else None

    def _ending(self, status: str, reason: str, identity: Any) -> None:
        token = gui_pipeline._JOURNAL_RUN.set(identity)
        try:
            gui_pipeline._record_turn_ending(self.root, status, reason)
        finally:
            gui_pipeline._JOURNAL_RUN.reset(token)


class TheWrapperShapeIsTheGuaranteeTests(unittest.TestCase):
    """Stage 3's promise is structural, so it is asserted structurally.

    ``_handle_gui_message`` has more than a dozen returns spread through nested
    closures. The wrapper exists because one exit can be reasoned about and
    fourteen cannot. If someone later adds a second return to the wrapper --
    an early-out for some special case, say -- that path stops recording a
    terminal event, every run taking it silently loses its ending, and not one
    existing test fails. Hence a test about syntax.
    """

    def _wrapper(self) -> ast.FunctionDef:
        tree = ast.parse(inspect.getsource(gui_pipeline.handle_gui_message))
        node = tree.body[0]
        if not isinstance(node, ast.FunctionDef):
            self.fail(f"handle_gui_message parsed as {type(node).__name__}")
        return node

    def test_the_wrapper_has_exactly_one_return(self):
        returns = [n for n in ast.walk(self._wrapper()) if isinstance(n, ast.Return)]

        self.assertEqual(
            len(returns),
            1,
            "a second exit would skip the terminal event on that path",
        )

    def test_the_wrapper_catches_base_exception(self):
        """A cancelled turn raises KeyboardInterrupt, not Exception."""

        caught = {
            ast.unparse(handler.type)
            for node in ast.walk(self._wrapper())
            if isinstance(node, ast.Try)
            for handler in node.handlers
            if handler.type is not None
        }

        self.assertIn("BaseException", caught)

    def test_the_context_var_is_always_reset(self):
        """Otherwise a turn leaks its run into whatever runs next on the thread."""

        tries = [n for n in ast.walk(self._wrapper()) if isinstance(n, ast.Try)]

        self.assertTrue(
            any(node.finalbody for node in tries),
            "without a finally the ContextVar survives an exception",
        )


class TheTurnEndingSurvivesAnythingTests(_StackFixture):
    """It runs after the answer is already produced, so it may never raise."""

    def test_no_identity_is_not_an_error(self):
        self._ending("completed", "", None)

    def test_a_malformed_identity_is_not_an_error(self):
        for bad in ("not-a-mapping", {}, {"run_id": ""}, {"run_id": None}, []):
            with self.subTest(identity=bad):
                self._ending("completed", "x", bad)

    def test_an_unrecognised_status_still_closes_the_run(self):
        """An unmapped status must not leave a run open forever."""

        fence = record_admission(self.root, task_id="t", run_id="r", task="x", now=NOW)

        self._ending("some-status-nobody-defined", "", {"run_id": "r", "fence": fence})

        self.assertTrue(self._verdict("r"))

    def test_a_late_second_terminal_cannot_overwrite_a_settled_verdict(self):
        fence = record_admission(self.root, task_id="t", run_id="r", task="x", now=NOW)
        identity = {"run_id": "r", "fence": fence}

        self._ending("completed", "ok", identity)
        self._ending("failed", "late", identity)

        self.assertEqual(self._verdict("r"), "completed")

    def test_a_stale_fence_cannot_close_a_run_it_no_longer_owns(self):
        fence = record_admission(self.root, task_id="t", run_id="r", task="x", now=NOW)

        self._ending("failed", "stale", {"run_id": "r", "fence": (fence or 1) - 1})

        self.assertFalse(self._verdict("r"))


class TheReaderNeverLosesARunTests(_StackFixture):
    """Falling back is acceptable; dropping a run never is."""

    def test_a_legacy_only_run_survives_read_all(self):
        legacy = self._migrate(3)
        legacy["legacy-only"] = {
            "terminal_verdict": "completed",
            "created_at": "2099-01-01T00:00:00+00:00",
        }

        views = journal_reader.JournalReader(self.root, legacy).read_all()

        self.assertIn("legacy-only", views)

    def test_a_corrupt_journal_still_serves_every_legacy_run(self):
        legacy = self._migrate(4)
        journal_path(self.root).write_bytes(b"not a database")

        views = journal_reader.JournalReader(self.root, legacy).read_all()

        self.assertEqual(set(views), set(legacy))
        self.assertFalse(any(view.from_journal for view in views.values()))

    def test_one_disagreement_stops_the_whole_reader(self):
        """Partial trust is not a state the reader is allowed to be in."""

        legacy = self._migrate(10)
        legacy["run-3"]["terminal_verdict"] = "cancelled"

        reader = journal_reader.JournalReader(self.root, legacy)

        self.assertFalse(reader.serving_from_journal)
        self.assertFalse(any(v.from_journal for v in reader.read_all().values()))

    def test_the_source_counts_account_for_every_view(self):
        """Telemetry that does not add up cannot be used to decide retirement."""

        legacy = self._migrate(6)
        reader = journal_reader.JournalReader(self.root, legacy)

        views = reader.read_all()

        self.assertEqual(sum(reader.source_counts().values()), len(views))

    def test_compared_runs_never_exceeds_either_side(self):
        legacy = self._migrate(5)
        legacy["extra"] = {
            "terminal_verdict": "completed",
            "created_at": "2099-01-01T00:00:00+00:00",
        }

        compared = journal_reader.JournalReader(self.root, legacy).compared_runs()

        self.assertLessEqual(compared, len(legacy))
        self.assertEqual(compared, 5)


class DoctorReportsProblemsWithoutBecomingOneTests(_StackFixture):
    """Diagnostics run when things are already broken. That is their whole job."""

    def test_garbage_in_the_journal_path_is_reported_not_raised(self):
        path = journal_path(self.root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x00\x01garbage")

        payload = cli._journal_doctor(self.root)

        json.dumps(payload)
        self.assertNotEqual(payload.get("integrity", {}).get("state"), "complete")

    def test_the_json_output_stays_parseable_with_a_broken_journal(self):
        self._migrate(2)
        journal_path(self.root).write_bytes(b"not a database")

        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = cli.cmd_doctor(argparse.Namespace(project=str(self.root), json=True))

        payload = json.loads(buffer.getvalue())
        self.assertEqual(code, 0)
        self.assertIn("runtime_journal", payload)


class RetirementEvidenceHoldsUpTests(_StackFixture):
    """The one irreversible decision in the whole migration."""

    def test_a_degraded_store_is_never_enough_to_retire_on(self):
        """The fallback matters most exactly when the journal is imperfect."""

        legacy = self._migrate(25)
        store = open_store(self.root)
        try:
            bad = append_event(
                store,
                event_type="noise",
                payload={"x": 1},
                occurred_at=NOW,
                recorded_at=NOW,
                producer="t",
                run_id="run-0",
            )
            store.execute(
                "UPDATE events SET payload = '{torn' WHERE sequence = ?", (bad,)
            )
        finally:
            store.close()

        self.assertFalse(
            journal_retirement.assess(self.root, legacy, minimum_runs=20).ready
        )

    def test_unmigrated_legacy_runs_block_retirement(self):
        """50 runs the journal never saw is not a finished migration."""

        legacy = self._migrate(25)
        for index in range(50):
            legacy[f"old-{index}"] = {
                "terminal_verdict": "completed",
                "created_at": "1999-01-01T00:00:00+00:00",
            }

        self.assertFalse(
            journal_retirement.assess(self.root, legacy, minimum_runs=20).ready
        )

    def test_the_report_survives_a_json_round_trip_intact(self):
        """It is the record of an irreversible decision; it has to serialise."""

        legacy = self._migrate(25)

        payload = json.loads(
            json.dumps(
                journal_retirement.assess(self.root, legacy, minimum_runs=20).to_dict()
            )
        )

        self.assertTrue(payload["ready"])
        self.assertEqual(payload["compared_runs"], 25)


class IdentityIsNeverAmbiguousTests(_StackFixture):
    """Two runs sharing one identity is silent loss, which is the whole point."""

    def test_a_blank_run_id_never_reaches_the_store(self):
        record_admission(self.root, task_id="task-a", run_id="", task="first", now=NOW)
        record_admission(self.root, task_id="task-b", run_id="", task="second", now=NOW)

        store = open_store(self.root)
        self.addCleanup(store.close)

        self.assertEqual(store.execute("SELECT COUNT(*) FROM runs").fetchone()[0], 0)

    def test_two_tasks_can_share_neither_a_run_id_nor_a_row(self):
        record_admission(self.root, task_id="task-a", run_id="dup", task="a", now=NOW)
        record_admission(self.root, task_id="task-b", run_id="dup", task="b", now=NOW)

        store = open_store(self.root)
        self.addCleanup(store.close)
        rows = store.execute("SELECT task_id FROM runs WHERE run_id='dup'").fetchall()

        self.assertEqual(len(rows), 1, "one run id must mean one run")

    def test_a_terminal_for_a_run_that_was_never_admitted_is_refused(self):
        open_store(self.root).close()

        recorded = record_terminal(
            self.root,
            run_id="never-admitted",
            event_type=EVENT_FINISHED,
            verdict="completed",
            reason="",
            now=NOW,
            fence=1,
        )

        self.assertFalse(recorded)


if __name__ == "__main__":  # pragma: no cover - convenience
    unittest.main()
