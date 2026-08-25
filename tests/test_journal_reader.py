"""#613 Stage 5: the journal is read from only when it has been qualified.

Stage 5 is the cutover. Stage 4's instruction gates it -- "no switch until
differences are understood and classified" -- so the interesting tests here are
the ones where the reader **refuses** to use the journal, because that is the
gate working.

A reader that silently preferred an unqualified journal would look like
progress and be the exact failure #613 exists to remove. Every test below is
either "did it refuse when it should" or "did it say where the answer came
from", and the second matters as much as the first: Stage 7 retires legacy
writes only once telemetry shows nothing reads them, and that telemetry is the
``source`` field.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from opaihub import journal_reader
from opaihub.journal_reader import (
    FALLBACK_NO_STORE,
    FALLBACK_UNKNOWN_RUN,
    FALLBACK_UNQUALIFIED,
    FALLBACK_UNUSABLE,
    SOURCE_ABSENT,
    SOURCE_JOURNAL,
    SOURCE_LEGACY,
    JournalReader,
    ReaderPolicy,
    legacy_runs_from,
    read_run,
)
from opaihub.journal_runtime import EVENT_FINISHED, record_admission, record_terminal
from opaihub.journal_store import journal_path

NOW = "2026-08-25T12:00:00+00:00"


class _ReaderFixture(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def _run(self, run_id: str, verdict: str = "completed") -> None:
        fence = record_admission(
            self.root, task_id="task-a", run_id=run_id, task="a task", now=NOW
        )
        record_terminal(
            self.root,
            run_id=run_id,
            event_type=EVENT_FINISHED,
            verdict=verdict,
            reason="ok",
            now=NOW,
            fence=fence,
        )


class TheGateRefusesUnqualifiedEvidenceTests(_ReaderFixture):
    """Every one of these is the Stage 4 gate doing its job."""

    def test_an_unqualified_journal_is_not_read_from(self):
        """A verdict mismatch blocks qualification, so reads stay on legacy."""

        self._run("run-a", verdict="completed")
        legacy = {"run-a": {"terminal_verdict": "cancelled", "note": "legacy"}}

        reader = JournalReader(self.root, legacy)

        self.assertFalse(reader.serving_from_journal)
        view = reader.read_run("run-a")
        self.assertEqual(view.source, SOURCE_LEGACY)
        self.assertEqual(view.fallback_reason, FALLBACK_UNQUALIFIED)
        self.assertEqual(view.state["note"], "legacy")

    def test_a_missing_store_falls_back_and_says_so(self):
        reader = JournalReader(self.root, {"run-a": {"terminal_verdict": "completed"}})

        view = reader.read_run("run-a")

        self.assertEqual(view.source, SOURCE_LEGACY)
        self.assertIn(view.fallback_reason, {FALLBACK_NO_STORE, FALLBACK_UNQUALIFIED})

    def test_a_corrupt_store_is_never_read_from(self):
        self._run("run-a")
        journal_path(self.root).write_bytes(b"not a database")

        reader = JournalReader(self.root, {"run-a": {"terminal_verdict": "completed"}})

        self.assertFalse(reader.serving_from_journal)
        self.assertIn(
            reader.read_run("run-a").fallback_reason,
            {FALLBACK_UNUSABLE, FALLBACK_NO_STORE},
        )

    def test_the_gate_can_be_disabled_but_only_deliberately(self):
        """Turning it off is a named act, not a default someone drifts into."""

        self._run("run-a", verdict="completed")
        legacy = {"run-a": {"terminal_verdict": "cancelled"}}

        blocked = JournalReader(self.root, legacy)
        ungated = JournalReader(
            self.root, legacy, policy=ReaderPolicy(require_qualification=False)
        )

        self.assertFalse(blocked.serving_from_journal)
        self.assertTrue(ungated.serving_from_journal)
        self.assertEqual(ungated.read_run("run-a").source, SOURCE_JOURNAL)


class AQualifiedJournalIsServedTests(_ReaderFixture):
    def test_a_matching_run_is_served_from_the_journal(self):
        self._run("run-a", verdict="completed")

        reader = JournalReader(self.root, {"run-a": {"terminal_verdict": "completed"}})

        view = reader.read_run("run-a")
        self.assertTrue(reader.serving_from_journal)
        self.assertEqual(view.source, SOURCE_JOURNAL)
        self.assertTrue(view.from_journal)
        self.assertEqual(view.state["terminal_verdict"], "completed")

    def test_a_pre_migration_run_still_reads_from_legacy(self):
        """Falling back is not failing: this run predates the journal."""

        self._run("run-a", verdict="completed")
        legacy = {
            "run-a": {"terminal_verdict": "completed"},
            "run-old": {"terminal_verdict": "completed", "note": "before migration"},
        }

        reader = JournalReader(
            self.root, legacy, policy=ReaderPolicy(require_qualification=False)
        )

        old = reader.read_run("run-old")
        self.assertEqual(old.source, SOURCE_LEGACY)
        self.assertEqual(old.fallback_reason, FALLBACK_UNKNOWN_RUN)
        self.assertEqual(old.state["note"], "before migration")

    def test_a_run_neither_side_has_is_absent_not_invented(self):
        self._run("run-a")

        view = JournalReader(
            self.root, {"run-a": {"terminal_verdict": "completed"}}
        ).read_run("run-nothing")

        self.assertEqual(view.source, SOURCE_ABSENT)
        self.assertFalse(view.found)
        self.assertIsNone(view.state)


class ListingLosesNothingTests(_ReaderFixture):
    """Reading only the journal's runs would make users' past disappear."""

    def test_read_all_returns_the_union_of_both_sides(self):
        self._run("run-a")
        self._run("run-b")
        legacy = {
            "run-a": {"terminal_verdict": "completed"},
            "run-old": {"terminal_verdict": "completed"},
        }

        views = JournalReader(
            self.root, legacy, policy=ReaderPolicy(require_qualification=False)
        ).read_all()

        self.assertEqual(sorted(views), ["run-a", "run-b", "run-old"])
        self.assertEqual(views["run-a"].source, SOURCE_JOURNAL)
        self.assertEqual(views["run-b"].source, SOURCE_JOURNAL)
        self.assertEqual(views["run-old"].source, SOURCE_LEGACY)

    def test_source_counts_are_the_telemetry_stage_7_needs(self):
        self._run("run-a")
        legacy = {
            "run-a": {"terminal_verdict": "completed"},
            "run-old": {"terminal_verdict": "completed"},
        }

        counts = JournalReader(
            self.root, legacy, policy=ReaderPolicy(require_qualification=False)
        ).source_counts()

        self.assertEqual(counts[SOURCE_JOURNAL], 1)
        self.assertEqual(counts[SOURCE_LEGACY], 1)

    def test_a_blocked_reader_reports_zero_journal_reads(self):
        """Stage 7's precondition is zero *legacy* reads, so this must be honest."""

        self._run("run-a", verdict="completed")
        counts = JournalReader(
            self.root, {"run-a": {"terminal_verdict": "cancelled"}}
        ).source_counts()

        self.assertEqual(counts[SOURCE_JOURNAL], 0)
        self.assertEqual(counts[SOURCE_LEGACY], 1)


class DegradedIsServedButNeverSilentlyTests(_ReaderFixture):
    def test_a_degraded_store_still_serves_and_reports_it(self):
        from opaihub.journal_store import append_event, open_store

        self._run("run-a", verdict="completed")
        store = open_store(self.root)
        try:
            bad = append_event(
                store,
                event_type="noise",
                payload={"x": 1},
                occurred_at=NOW,
                recorded_at=NOW,
                producer="test",
                run_id="run-a",
            )
            store.execute(
                "UPDATE events SET payload = '{torn' WHERE sequence = ?", (bad,)
            )
        finally:
            store.close()

        reader = JournalReader(
            self.root,
            {"run-a": {"terminal_verdict": "completed"}},
            policy=ReaderPolicy(require_qualification=False),
        )
        view = reader.read_run("run-a")

        self.assertEqual(view.source, SOURCE_JOURNAL)
        self.assertEqual(view.integrity, "degraded")
        self.assertEqual(view.first_invalid_sequence, bad)


class DiagnosticsTests(_ReaderFixture):
    def test_the_diagnostics_explain_why_the_journal_was_or_was_not_used(self):
        self._run("run-a", verdict="completed")

        payload = JournalReader(
            self.root, {"run-a": {"terminal_verdict": "cancelled"}}
        ).diagnostics()

        json.loads(json.dumps(payload))
        self.assertFalse(payload["serving_from_journal"])
        self.assertEqual(payload["fallback_reason"], FALLBACK_UNQUALIFIED)
        self.assertEqual(payload["qualification"]["status"], "blocked")

    def test_a_reader_never_raises_when_the_store_cannot_be_opened(self):
        with mock.patch.object(
            journal_reader, "open_store", side_effect=OSError("gone")
        ):
            reader = JournalReader(self.root, {"run-a": {}})

        self.assertFalse(reader.serving_from_journal)
        self.assertEqual(reader.read_run("run-a").fallback_reason, FALLBACK_NO_STORE)


class LegacyAdapterTests(unittest.TestCase):
    """Normalising legacy shapes once, so two callers cannot disagree."""

    def test_a_sequence_of_records_becomes_a_run_keyed_mapping(self):
        out = legacy_runs_from(
            [
                {"run_id": "a", "terminal_verdict": "completed"},
                {"run_id": "b", "terminal_verdict": "cancelled"},
            ]
        )

        self.assertEqual(sorted(out), ["a", "b"])
        self.assertEqual(out["a"]["terminal_verdict"], "completed")

    def test_a_mapping_keeps_its_keys_as_run_ids(self):
        out = legacy_runs_from({"a": {"terminal_verdict": "completed"}})

        self.assertEqual(out["a"]["run_id"], "a")

    def test_a_verdict_callable_maps_a_foreign_vocabulary(self):
        out = legacy_runs_from(
            [{"run_id": "a", "status": "ok"}],
            verdict=lambda item: "completed" if item["status"] == "ok" else "failed",
        )

        self.assertEqual(out["a"]["terminal_verdict"], "completed")

    def test_records_without_a_run_id_are_dropped_rather_than_keyed_on_empty(self):
        """An empty key would collapse every anonymous record into one run."""

        out = legacy_runs_from([{"terminal_verdict": "completed"}, {"run_id": "a"}])

        self.assertEqual(list(out), ["a"])


class ConvenienceReadTests(_ReaderFixture):
    def test_the_one_shot_helper_matches_the_class(self):
        self._run("run-a", verdict="completed")
        legacy = {"run-a": {"terminal_verdict": "completed"}}

        direct = read_run(self.root, "run-a", legacy)
        via_class = JournalReader(self.root, legacy).read_run("run-a")

        self.assertEqual(direct.to_dict(), via_class.to_dict())


if __name__ == "__main__":  # pragma: no cover - convenience
    unittest.main()
