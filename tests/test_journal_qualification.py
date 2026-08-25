"""#613 Stage 4: qualify the journal before anything reads from it.

Stage 4's instruction is short and strict: *"Reconstruct both ways against
golden and real captured fixtures. No switch until differences are understood
and classified."*

The switch it gates is Stage 5, where GUI, CLI and receipts start reading
journal projections instead of files. That is the moment a mistake stops being
a bookkeeping gap and becomes wrong answers shown to a user. So the tests here
are mostly about **refusing to qualify**, because a false pass is the only
outcome that actually costs something -- a false block just means more work.

Three properties carry most of the weight:

- an **empty** journal is ``insufficient_evidence``, never ``qualified``. This
  is the most dangerous false pass available: nothing to disagree with looks
  exactly like perfect agreement.
- a run **missing** from the journal blocks, while an **extra** one does not.
  Reading from the journal would lose the first; the second is usually the
  journal being better, since its choke point catches exits the legacy path
  never wrote down.
- a **disagreement about how a run ended** always blocks. That is the
  "individually plausible but mutually contradictory" state the issue opens by
  describing.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from opaihub import journal_qualification, journal_runtime
from opaihub.journal_qualification import (
    KIND_EXTRA,
    KIND_MISSING,
    KIND_UNTERMINATED,
    KIND_VERDICT,
    STATUS_BLOCKED,
    STATUS_INSUFFICIENT,
    STATUS_QUALIFIED,
    compare,
    qualify,
)
from opaihub.journal_runtime import EVENT_FINISHED, record_admission, record_terminal

NOW = "2026-08-25T12:00:00+00:00"


class _QualifyFixture(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def _finished_run(self, run_id: str, verdict: str = "completed") -> None:
        fence = record_admission(
            self.root,
            task_id="task-a",
            run_id=run_id,
            task="a task",
            now=NOW,
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

    def _open_run(self, run_id: str) -> None:
        """Admitted but never closed out."""

        record_admission(
            self.root, task_id="task-a", run_id=run_id, task="a task", now=NOW
        )


class AnEmptyJournalNeverQualifiesTests(_QualifyFixture):
    """The most dangerous false pass: nothing to disagree with looks perfect."""

    def test_a_project_with_no_journal_is_insufficient_not_qualified(self):
        report = qualify(self.root, {})

        self.assertEqual(report.status, STATUS_INSUFFICIENT)
        self.assertFalse(report.qualified)

    def test_a_journal_with_no_runs_is_insufficient(self):
        from opaihub.journal_store import open_store

        open_store(self.root).close()

        report = qualify(self.root, {})

        self.assertEqual(report.status, STATUS_INSUFFICIENT)

    def test_a_minimum_sample_can_be_demanded_before_a_cutover(self):
        self._finished_run("run-a")

        report = qualify(
            self.root,
            {"run-a": {"terminal_verdict": "completed"}},
            minimum_runs=10,
        )

        self.assertEqual(report.status, STATUS_INSUFFICIENT)
        self.assertIn("10 required", report.detail)

    def test_a_corrupt_journal_is_insufficient_not_blocked(self):
        """ "Blocked" would imply the differences were examined. They were not."""

        self._finished_run("run-a")
        from opaihub.journal_store import journal_path

        journal_path(self.root).write_bytes(b"not a database")

        report = qualify(self.root, {"run-a": {"terminal_verdict": "completed"}})

        self.assertEqual(report.status, STATUS_INSUFFICIENT)
        # Which branch catches it depends on whether the damage stops the
        # connection or only the integrity check, so the assertion is that it
        # explains itself -- not which of the two words it used.
        self.assertTrue(report.detail)
        self.assertFalse(report.qualified)


class AgreementQualifiesTests(_QualifyFixture):
    def test_matching_records_qualify(self):
        self._finished_run("run-a")

        report = qualify(self.root, {"run-a": {"terminal_verdict": "completed"}})

        self.assertEqual(report.status, STATUS_QUALIFIED)
        self.assertTrue(report.qualified)
        self.assertEqual(report.compared_runs, 1)
        self.assertEqual(report.blocking, ())

    def test_a_legacy_verdict_can_be_declared_equivalent(self):
        """Two words for one ending, claimed explicitly rather than assumed."""

        self._finished_run("run-a", verdict="completed")

        report = qualify(
            self.root,
            {"run-a": {"terminal_verdict": "success"}},
            verdict_equivalent={"success": "completed"},
        )

        self.assertEqual(report.status, STATUS_QUALIFIED)

    def test_without_the_equivalence_the_same_pair_blocks(self):
        """Teeth for the mapping: it must be doing real work."""

        self._finished_run("run-a", verdict="completed")

        report = qualify(self.root, {"run-a": {"terminal_verdict": "success"}})

        self.assertEqual(report.status, STATUS_BLOCKED)
        self.assertEqual(report.kind_counts().get(KIND_VERDICT), 1)


class RealDifferencesBlockTests(_QualifyFixture):
    def test_a_run_missing_from_the_journal_blocks(self):
        """Reading from the journal would lose it."""

        self._finished_run("run-a")

        report = qualify(
            self.root,
            {
                "run-a": {"terminal_verdict": "completed"},
                "run-ghost": {"terminal_verdict": "completed"},
            },
        )

        self.assertEqual(report.status, STATUS_BLOCKED)
        self.assertEqual(report.kind_counts().get(KIND_MISSING), 1)
        self.assertEqual(report.blocking[0].run_id, "run-ghost")

    def test_a_verdict_disagreement_blocks(self):
        self._finished_run("run-a", verdict="completed")

        report = qualify(self.root, {"run-a": {"terminal_verdict": "cancelled"}})

        self.assertEqual(report.status, STATUS_BLOCKED)
        difference = report.blocking[0]
        self.assertEqual(difference.kind, KIND_VERDICT)
        self.assertEqual(difference.journal, "completed")
        self.assertEqual(difference.legacy, "cancelled")

    def test_an_unterminated_run_blocks(self):
        """After a cutover it would read as still running, forever."""

        self._open_run("run-a")

        report = qualify(self.root, {"run-a": {"terminal_verdict": "completed"}})

        self.assertEqual(report.status, STATUS_BLOCKED)
        self.assertEqual(report.kind_counts().get(KIND_UNTERMINATED), 1)

    def test_every_blocking_kind_is_named_in_the_detail(self):
        """A caller must be able to act without parsing the difference list."""

        self._open_run("run-open")
        self._finished_run("run-a", verdict="completed")

        report = qualify(
            self.root,
            {
                "run-open": {"terminal_verdict": "completed"},
                "run-a": {"terminal_verdict": "cancelled"},
                "run-ghost": {"terminal_verdict": "completed"},
            },
        )

        for kind in (KIND_UNTERMINATED, KIND_VERDICT, KIND_MISSING):
            self.assertIn(kind, report.detail)


class ExtrasAreInformationalTests(_QualifyFixture):
    """The journal's choke point catches exits the legacy path never recorded."""

    def test_a_run_only_the_journal_knows_about_does_not_block(self):
        self._finished_run("run-a")
        self._finished_run("run-b")

        report = qualify(self.root, {"run-a": {"terminal_verdict": "completed"}})

        self.assertEqual(report.status, STATUS_QUALIFIED)
        self.assertEqual(report.kind_counts().get(KIND_EXTRA), 1)

    def test_an_extra_is_still_reported_even_though_it_does_not_block(self):
        """Accepted is not the same as hidden."""

        self._finished_run("run-a")
        self._finished_run("run-b")

        report = qualify(self.root, {"run-a": {"terminal_verdict": "completed"}})

        kinds = [item.kind for item in report.differences]
        self.assertIn(KIND_EXTRA, kinds)
        self.assertEqual(report.blocking, ())

    def test_acceptance_can_be_narrowed_but_the_report_still_lists_everything(self):
        self._finished_run("run-a")
        self._finished_run("run-b")

        report = qualify(
            self.root,
            {"run-a": {"terminal_verdict": "completed"}},
            accepted_kinds=(),
        )

        self.assertEqual(report.status, STATUS_BLOCKED)
        self.assertEqual(len(report.differences), 1)


class CompareIsPureTests(unittest.TestCase):
    """The classifier alone, so its rules are readable without a database."""

    def test_disjoint_sets_produce_one_difference_each_way(self):
        differences = compare({"a": {"terminal_verdict": "completed"}}, {"b": {}})

        kinds = sorted(item.kind for item in differences)
        self.assertEqual(kinds, [KIND_EXTRA, KIND_MISSING])

    def test_identical_records_produce_nothing(self):
        differences = compare(
            {"a": {"terminal_verdict": "completed"}},
            {"a": {"terminal_verdict": "completed"}},
        )

        self.assertEqual(differences, [])

    def test_a_legacy_run_with_no_verdict_is_not_a_mismatch(self):
        """An empty legacy verdict says nothing; it must not read as a conflict."""

        differences = compare(
            {"a": {"terminal_verdict": "completed"}}, {"a": {"terminal_verdict": ""}}
        )

        self.assertEqual(differences, [])

    def test_the_results_are_ordered_so_two_runs_agree(self):
        """A report that reorders itself cannot be diffed between runs."""

        journal = {"c": {"terminal_verdict": "x"}, "a": {"terminal_verdict": "x"}}
        legacy = {"b": {}, "d": {}}

        first = [item.run_id for item in compare(journal, legacy)]
        second = [item.run_id for item in compare(journal, legacy)]

        self.assertEqual(first, second)
        self.assertEqual(first, sorted(first, key=lambda r: (r not in legacy, r)))


class TheReportIsSerialisableTests(_QualifyFixture):
    def test_the_report_round_trips_through_json(self):
        """It goes into diagnostics output, so it has to survive that."""

        self._finished_run("run-a")

        payload = qualify(
            self.root, {"run-a": {"terminal_verdict": "cancelled"}}
        ).to_dict()

        json.loads(json.dumps(payload))
        self.assertEqual(payload["report"], "opai-journal-qualification")
        self.assertEqual(payload["blocking_count"], 1)

    def test_qualification_never_raises_on_an_unopenable_journal(self):
        with mock.patch.object(
            journal_qualification, "open_store", side_effect=OSError("gone")
        ):
            report = qualify(self.root, {})

        self.assertEqual(report.status, STATUS_INSUFFICIENT)


class Stage3CostAndVerificationTests(_QualifyFixture):
    """The two Stage 3 surfaces this PR added, checked where they land."""

    def test_a_cost_is_attributed_once_and_only_once(self):
        fence = record_admission(
            self.root, task_id="task-a", run_id="run-a", task="t", now=NOW
        )

        first = journal_runtime.record_run_cost(
            self.root,
            run_id="run-a",
            operation_key="op-1",
            amount_usd=0.42,
            measurement_kind="actual",
            now=NOW,
            fence=fence,
            model="sonnet",
        )
        second = journal_runtime.record_run_cost(
            self.root,
            run_id="run-a",
            operation_key="op-1",
            amount_usd=0.42,
            measurement_kind="actual",
            now=NOW,
            fence=fence,
            model="sonnet",
        )

        self.assertTrue(first)
        self.assertFalse(second, "a duplicate is refused, not raised")

        from opaihub.journal_store import open_store

        store = open_store(self.root)
        self.addCleanup(store.close)
        total = store.execute("SELECT SUM(amount) FROM cost_events").fetchone()[0]
        self.assertEqual(float(total), 0.42)

    def test_a_verification_records_the_policy_it_was_judged_under(self):
        """A verdict without its policy cannot be audited later."""

        fence = record_admission(
            self.root, task_id="task-a", run_id="run-a", task="t", now=NOW
        )

        self.assertTrue(
            journal_runtime.record_verification(
                self.root,
                run_id="run-a",
                verdict="passed",
                policy_digest="a" * 64,
                manifest_digest="b" * 64,
                now=NOW,
                fence=fence,
            )
        )

        from opaihub.journal_store import open_store, read_events

        store = open_store(self.root)
        self.addCleanup(store.close)
        verified = [
            row
            for row in read_events(store)
            if row["event_type"] == journal_runtime.EVENT_VERIFIED
        ]
        self.assertEqual(len(verified), 1)
        self.assertEqual(verified[0]["payload"]["policy_digest"], "a" * 64)
        artifacts = store.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0]
        self.assertEqual(artifacts, 1)

    def test_a_stale_fence_cannot_record_a_verification(self):
        stale = record_admission(
            self.root, task_id="task-a", run_id="run-a", task="t", now=NOW
        )
        record_admission(self.root, task_id="task-a", run_id="run-a", task="t", now=NOW)

        self.assertFalse(
            journal_runtime.record_verification(
                self.root,
                run_id="run-a",
                verdict="passed",
                policy_digest="a" * 64,
                manifest_digest="b" * 64,
                now=NOW,
                fence=stale,
            )
        )


if __name__ == "__main__":  # pragma: no cover - convenience
    unittest.main()
