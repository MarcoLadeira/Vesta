"""#613 end to end: one run's whole life, through every layer that touches it.

Every other journal suite tests a layer. This one tests the *seams*, because
that is where an integration actually fails: each module can be individually
correct while the chain between them loses a run.

The path is the real one -- admission writes, the lifecycle advances, cost and
verification attach, the run ends, qualification compares against legacy, and
the reader serves it. A break anywhere in that chain shows up here as a run
that cannot be reconstructed, which is the failure #613 exists to remove
stated in one sentence.

Two things this file deliberately does that the unit suites do not:

**It crosses process boundaries mid-life.** A run is admitted in one
interpreter and finished in another, because a GUI that starts a turn and a
worker that finishes it are different programs and the seam between them is
exactly where state gets lost.

**It checks the acceptance criteria as criteria**, not as module behaviour.
"A run can be reconstructed after process termination at every lifecycle
boundary" is a statement about the whole system, and it is only true if every
layer holds at once.
"""

from __future__ import annotations

import subprocess  # nosec B404 - fixed argv, this test's own interpreter
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from opaihub import journal_qualification, journal_reader
from opaihub.journal_runtime import (
    EVENT_ADMITTED,
    EVENT_COSTED,
    EVENT_FINISHED,
    EVENT_STARTED,
    EVENT_VERIFIED,
    record_admission,
    record_event,
    record_run_cost,
    record_terminal,
    record_verification,
)
from opaihub.journal_store import (
    INTEGRITY_COMPLETE,
    check_integrity,
    open_store,
    read_events,
    rebuild_projection,
)

NOW = "2026-08-25T12:00:00+00:00"
_REPO = Path(__file__).resolve().parent.parent


def _run_child(root: Path, body: str) -> subprocess.CompletedProcess:
    script = textwrap.dedent("""
        import sys
        sys.path.insert(0, {repo!r})
        from pathlib import Path
        from opaihub.journal_runtime import (
            EVENT_FINISHED, EVENT_STARTED, record_admission, record_event,
            record_terminal,
        )
        root = Path({root!r})
        NOW = {now!r}
        {body}
    """).format(
        repo=str(_REPO),
        root=str(root),
        now=NOW,
        # dedent() runs before format(), so {body} lands at column 0 in the
        # generated script. Adding indentation here would leave line 1 at the
        # margin and the rest indented -- an IndentationError in the child.
        body=textwrap.dedent(body).strip(),
    )
    return subprocess.run(  # nosec B603 - fixed argv, this interpreter, temp dir
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=180
    )


class _EndToEndFixture(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def _store(self):
        store = open_store(self.root)
        self.addCleanup(store.close)
        return store

    def _full_run(self, run_id: str = "run-a", verdict: str = "completed") -> int:
        """Admission through terminal, with cost and verification attached."""

        fence = record_admission(
            self.root,
            task_id="task-a",
            run_id=run_id,
            task="fix the failing login test",
            now=NOW,
            mode="implement",
            model="sonnet",
        )
        record_event(
            self.root,
            run_id=run_id,
            event_type=EVENT_STARTED,
            now=NOW,
            fence=fence,
        )
        record_run_cost(
            self.root,
            run_id=run_id,
            operation_key=f"{run_id}:account",
            amount_usd=0.42,
            measurement_kind="estimated",
            now=NOW,
            fence=fence,
            model="sonnet",
            tokens=1200,
        )
        record_verification(
            self.root,
            run_id=run_id,
            verdict="passed",
            policy_digest="a" * 64,
            manifest_digest="b" * 64,
            now=NOW,
            fence=fence,
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
        return fence


class OneRunsWholeLifeTests(_EndToEndFixture):
    def test_every_stage_of_a_run_lands_in_order(self):
        self._full_run()

        types = [row["event_type"] for row in read_events(self._store())]

        self.assertEqual(
            types,
            [
                EVENT_ADMITTED,
                EVENT_STARTED,
                EVENT_COSTED,
                EVENT_VERIFIED,
                EVENT_FINISHED,
            ],
        )

    def test_the_run_row_and_the_events_agree_about_the_ending(self):
        """Two readers of the same store must not see different endings."""

        self._full_run(verdict="completed")
        store = self._store()

        row = store.execute(
            "SELECT terminal_verdict FROM runs WHERE run_id = 'run-a'"
        ).fetchone()
        final_event = read_events(store)[-1]

        self.assertEqual(row["terminal_verdict"], "completed")
        self.assertEqual(final_event["payload"]["verdict"], "completed")

    def test_cost_and_evidence_are_attached_to_the_run(self):
        self._full_run()
        store = self._store()

        cost = store.execute(
            "SELECT amount, quantity FROM cost_events"
            " WHERE operation_key = 'run-a:account'"
        ).fetchone()
        artifact = store.execute(
            "SELECT kind, identity FROM artifacts WHERE identity = 'run-a'"
        ).fetchone()

        self.assertEqual(float(cost["amount"]), 0.42)
        self.assertEqual(float(cost["quantity"]), 1200.0)
        self.assertEqual(artifact["kind"], "verification_manifest")

    def test_the_store_is_complete_after_a_full_run(self):
        self._full_run()

        self.assertEqual(check_integrity(self._store()).state, INTEGRITY_COMPLETE)


class ReconstructionAcrossProcessesTests(_EndToEndFixture):
    """A GUI starts a turn and a worker finishes it: the seam that loses state."""

    def test_a_run_admitted_in_one_process_is_finished_in_another(self):
        first = _run_child(
            self.root,
            """
            fence = record_admission(
                root, task_id="task-a", run_id="run-a", task="t", now=NOW
            )
            record_event(
                root, run_id="run-a", event_type=EVENT_STARTED, now=NOW, fence=fence
            )
            print(fence)
            """,
        )
        self.assertEqual(first.returncode, 0, first.stderr[-500:])
        fence = int(first.stdout.strip())

        second = _run_child(
            self.root,
            f"""
            record_terminal(
                root, run_id="run-a", event_type=EVENT_FINISHED,
                verdict="completed", reason="ok", now=NOW, fence={fence},
            )
            """,
        )
        self.assertEqual(second.returncode, 0, second.stderr[-500:])

        store = self._store()
        self.assertEqual(
            store.execute(
                "SELECT terminal_verdict FROM runs WHERE run_id = 'run-a'"
            ).fetchone()[0],
            "completed",
        )
        self.assertEqual(
            [row["event_type"] for row in read_events(store)],
            [EVENT_ADMITTED, EVENT_STARTED, EVENT_FINISHED],
        )

    def test_a_process_that_dies_mid_run_leaves_a_reconstructible_history(self):
        """The first acceptance criterion, end to end rather than per-layer."""

        killed = _run_child(
            self.root,
            """
            import os
            fence = record_admission(
                root, task_id="task-a", run_id="run-a", task="t", now=NOW
            )
            record_event(
                root, run_id="run-a", event_type=EVENT_STARTED, now=NOW, fence=fence
            )
            os._exit(9)
            """,
        )
        self.assertEqual(killed.returncode, 9, killed.stderr[-500:])

        store = self._store()
        events = read_events(store)

        self.assertEqual(
            [row["event_type"] for row in events], [EVENT_ADMITTED, EVENT_STARTED]
        )
        self.assertTrue(all(row["readable"] for row in events))
        self.assertEqual(check_integrity(store).state, INTEGRITY_COMPLETE)
        # The run is recoverable as *unfinished*, which is the honest state --
        # not silently completed, and not lost.
        self.assertIsNone(
            store.execute(
                "SELECT terminal_verdict FROM runs WHERE run_id = 'run-a'"
            ).fetchone()[0]
        )


class QualificationToReadHandoffTests(_EndToEndFixture):
    """Stage 4 gates Stage 5: the two must agree about the same store."""

    def test_a_clean_run_qualifies_and_is_then_served_from_the_journal(self):
        self._full_run(verdict="completed")
        legacy = {"run-a": {"terminal_verdict": "completed"}}

        report = journal_qualification.qualify(self.root, legacy)
        reader = journal_reader.JournalReader(self.root, legacy)

        self.assertTrue(report.qualified)
        self.assertTrue(reader.serving_from_journal)
        self.assertEqual(reader.read_run("run-a").source, journal_reader.SOURCE_JOURNAL)

    def test_a_disagreement_blocks_qualification_and_the_reader_together(self):
        """The gate and the reader must not reach different conclusions."""

        self._full_run(verdict="completed")
        legacy = {"run-a": {"terminal_verdict": "cancelled"}}

        report = journal_qualification.qualify(self.root, legacy)
        reader = journal_reader.JournalReader(self.root, legacy)

        self.assertFalse(report.qualified)
        self.assertFalse(reader.serving_from_journal)
        self.assertEqual(
            reader.read_run("run-a").fallback_reason,
            journal_reader.FALLBACK_UNQUALIFIED,
        )

    def test_an_unfinished_run_blocks_the_cutover(self):
        """A run with no ending would read as running forever after a switch."""

        record_admission(self.root, task_id="task-a", run_id="run-a", task="t", now=NOW)
        legacy = {"run-a": {"terminal_verdict": "completed"}}

        reader = journal_reader.JournalReader(self.root, legacy)

        self.assertFalse(reader.serving_from_journal)
        self.assertEqual(reader.read_run("run-a").source, journal_reader.SOURCE_LEGACY)


class ProjectionsRebuildTheWholeLifeTests(_EndToEndFixture):
    """Acceptance criterion: delete a projection, rebuild, get the same bytes."""

    @staticmethod
    def _empty() -> dict[str, object]:
        return {"events": [], "cost_seen": False, "verified": False}

    @staticmethod
    def _reduce(projection, record):
        return {
            "events": [*projection["events"], record["event_type"]],
            "cost_seen": projection["cost_seen"]
            or record["event_type"] == EVENT_COSTED,
            "verified": projection["verified"]
            or record["event_type"] == EVENT_VERIFIED,
        }

    def test_a_rebuilt_projection_describes_the_whole_run(self):
        self._full_run()

        result = rebuild_projection(
            self._store(),
            projection_type="run_life",
            projection_version=1,
            reduce=self._reduce,
            empty=self._empty,
            now=NOW,
        )

        self.assertTrue(result.complete)
        self.assertTrue(result.payload["cost_seen"])
        self.assertTrue(result.payload["verified"])
        self.assertEqual(result.payload["events"][-1], EVENT_FINISHED)

    def test_two_rebuilds_of_a_full_run_are_byte_identical(self):
        self._full_run()
        store = self._store()

        def build():
            return rebuild_projection(
                store,
                projection_type="run_life",
                projection_version=1,
                reduce=self._reduce,
                empty=self._empty,
                now=NOW,
            )

        first = build()
        second = build()

        self.assertEqual(first.to_bytes(), second.to_bytes())


class MultipleRunsOfOneTaskTests(_EndToEndFixture):
    """A retry is a new run of the same task, and both must survive."""

    def test_two_attempts_of_one_task_are_both_reconstructible(self):
        self._full_run(run_id="run-a", verdict="failed")
        self._full_run(run_id="run-b", verdict="completed")

        store = self._store()
        rows = store.execute(
            "SELECT run_id, attempt, terminal_verdict FROM runs ORDER BY attempt"
        ).fetchall()

        self.assertEqual([row["attempt"] for row in rows], [1, 2])
        self.assertEqual(
            [row["terminal_verdict"] for row in rows], ["failed", "completed"]
        )
        self.assertEqual(
            store.execute("SELECT COUNT(*) FROM tasks").fetchone()[0],
            1,
            "two attempts, one task",
        )

    def test_each_attempt_keeps_its_own_cost(self):
        """Attributing both attempts to one cost row would understate spend."""

        self._full_run(run_id="run-a")
        self._full_run(run_id="run-b")

        total = (
            self._store()
            .execute("SELECT COUNT(*), SUM(amount) FROM cost_events")
            .fetchone()
        )

        self.assertEqual(total[0], 2)
        self.assertAlmostEqual(float(total[1]), 0.84, places=6)


if __name__ == "__main__":  # pragma: no cover - convenience
    unittest.main()


class ExistingInstallationsMigrateWithoutLossTests(_EndToEndFixture):
    """Acceptance criterion 7, which the other suites do not cover.

    "Existing installations migrate without losing task, cost, approval or
    evidence references."

    The upgrade path is the one users actually experience: Vesta has been
    running for months with files, a release adds the journal, and from that
    moment some runs are journalled and older ones never will be. If the switch
    dropped the older ones, users would open the app to find their history
    truncated at the upgrade -- a failure they would notice immediately and
    could not undo.

    So the test is deliberately shaped like an upgrade rather than a fresh
    install: legacy history first, journal second, then read everything.
    """

    def test_history_from_before_the_journal_survives_the_upgrade(self):
        # Months of history, recorded before the journal existed.
        legacy = {
            f"old-{index}": {
                "terminal_verdict": "completed",
                "cost_usd": 0.10 * index,
                "evidence_ref": f"manifest-{index}",
                "created_at": "2020-01-01T00:00:00+00:00",
            }
            for index in range(1, 6)
        }

        # The upgrade lands and new runs start being journalled.
        self._full_run(run_id="new-1", verdict="completed")
        legacy["new-1"] = {
            "terminal_verdict": "completed",
            "created_at": "2099-01-01T00:00:00+00:00",
        }

        views = journal_reader.JournalReader(self.root, legacy).read_all()

        self.assertEqual(len(views), 6, "no run may vanish at the upgrade boundary")
        for index in range(1, 6):
            view = views[f"old-{index}"]
            self.assertTrue(view.found)
            self.assertEqual(view.state["evidence_ref"], f"manifest-{index}")
            self.assertEqual(view.state["cost_usd"], 0.10 * index)

    def test_the_new_run_is_journalled_while_old_ones_stay_on_legacy(self):
        """Mixed sources are the normal state during a migration, not a fault.

        The timestamps matter and this test is why they exist. Without them,
        every pre-journal run classifies as ``missing_from_journal`` and blocks
        the cutover permanently -- a real installation with months of history
        could never qualify. The fresh-install tests all passed because they
        had no history to predate anything.
        """

        legacy = {
            "old-1": {"terminal_verdict": "completed", "created_at": "2020-01-01"},
            "new-1": {"terminal_verdict": "completed", "created_at": "2099-01-01"},
        }
        self._full_run(run_id="new-1", verdict="completed")

        reader = journal_reader.JournalReader(self.root, legacy)
        counts = reader.source_counts()

        self.assertEqual(counts[journal_reader.SOURCE_JOURNAL], 1)
        self.assertEqual(counts[journal_reader.SOURCE_LEGACY], 1)
        self.assertEqual(
            reader.read_run("old-1").fallback_reason,
            journal_reader.FALLBACK_UNKNOWN_RUN,
            "a pre-migration run is unknown to the journal, not an error",
        )

    def test_a_fresh_install_with_no_legacy_history_is_not_treated_as_loss(self):
        """The other end of the same rule: nothing to migrate is not a failure."""

        self._full_run(run_id="new-1", verdict="completed")

        views = journal_reader.JournalReader(
            self.root, {"new-1": {"terminal_verdict": "completed"}}
        ).read_all()

        self.assertEqual(len(views), 1)
        self.assertEqual(views["new-1"].source, journal_reader.SOURCE_JOURNAL)

    def test_cost_references_survive_when_the_journal_is_not_yet_qualified(self):
        """The most likely real state: journal present, not yet trusted.

        Reads must still return every legacy field, because falling back is
        supposed to be lossless -- a fallback that dropped cost or evidence
        references would turn a cautious gate into data loss.
        """

        self._full_run(run_id="new-1", verdict="completed")
        legacy = {
            "new-1": {"terminal_verdict": "cancelled"},  # forces a mismatch
            "old-1": {
                "terminal_verdict": "completed",
                "cost_usd": 1.25,
                "approval_ref": "approval-7",
            },
        }

        reader = journal_reader.JournalReader(self.root, legacy)

        self.assertFalse(reader.serving_from_journal)
        old = reader.read_run("old-1")
        self.assertEqual(old.state["cost_usd"], 1.25)
        self.assertEqual(old.state["approval_ref"], "approval-7")


class AllSevenStagesTogetherTests(_EndToEndFixture):
    """The whole of #613 in one test, because the stages only matter together.

    Each stage has its own suite and each passes in isolation. That is not the
    same as the migration working: Stage 4 gates Stage 5, Stage 5's telemetry
    feeds Stage 7, and a break in any link leaves a chain that is individually
    correct and collectively useless.

    This drives a realistic sample through all seven and asserts the thing the
    issue is actually for -- that at the end, the legacy record is genuinely no
    longer needed. It is the only test here that can fail because two stages
    disagree rather than because one is wrong.
    """

    SAMPLE = 25

    def _migrated_installation(self) -> dict[str, dict[str, str]]:
        legacy: dict[str, dict[str, str]] = {}
        for index in range(self.SAMPLE):
            run_id = f"run-{index}"
            self._full_run(run_id=run_id, verdict="completed")
            legacy[run_id] = {
                "terminal_verdict": "completed",
                "created_at": "2099-01-01T00:00:00+00:00",
            }
        return legacy

    def test_the_full_migration_reaches_a_state_where_legacy_is_not_needed(self):
        from opaihub import idempotency, journal_operations, journal_retirement

        legacy = self._migrated_installation()

        # Stage 6: an external effect through the choke point every one uses.
        key = idempotency.operation_key("github.pr", head="feat/x", base="main")
        idempotency.begin(self.root, key)
        idempotency.complete(self.root, key, {"id": 4242})

        # Stage 4: the journal agrees with the legacy record.
        report = journal_qualification.qualify(self.root, legacy, minimum_runs=20)
        self.assertTrue(report.qualified, report.detail)

        # Stage 5: reads come from the journal, and nothing falls back.
        reader = journal_reader.JournalReader(self.root, legacy)
        counts = reader.source_counts()
        self.assertTrue(reader.serving_from_journal)
        self.assertEqual(counts[journal_reader.SOURCE_JOURNAL], self.SAMPLE)
        self.assertEqual(counts[journal_reader.SOURCE_LEGACY], 0)

        # Stage 6: nothing is left unanswered.
        self.assertEqual(
            journal_operations.operation_summary(self.root)["unreconciled"], 0
        )

        # Stage 7: and only now may the legacy writes go.
        retirement = journal_retirement.assess(self.root, legacy, minimum_runs=20)
        self.assertTrue(retirement.ready, retirement.detail)
        self.assertFalse(
            journal_retirement.legacy_writes_required(
                self.root, legacy, minimum_runs=20
            )
        )

    def test_one_regression_anywhere_closes_the_gate_again(self):
        """The chain is only as strong as its weakest link, and must act like it.

        A single unreconciled operation -- one PR whose outcome nobody
        confirmed -- takes a fully migrated installation back to requiring
        legacy writes. That is the correct behaviour and the reason the gate is
        recomputed rather than remembered.
        """

        from opaihub import idempotency, journal_retirement

        legacy = self._migrated_installation()
        self.assertFalse(
            journal_retirement.legacy_writes_required(
                self.root, legacy, minimum_runs=20
            )
        )

        idempotency.begin(
            self.root, idempotency.operation_key("github.pr", head="unanswered")
        )

        self.assertTrue(
            journal_retirement.legacy_writes_required(
                self.root, legacy, minimum_runs=20
            ),
            "one unanswered effect must re-close the gate",
        )

    def test_the_integrity_of_the_whole_history_survives_the_full_run(self):
        self._migrated_installation()

        from opaihub.journal_store import INTEGRITY_COMPLETE, check_integrity

        store = self._store()
        report = check_integrity(store)
        events = read_events(store)

        self.assertEqual(report.state, INTEGRITY_COMPLETE)
        self.assertEqual(len(events), self.SAMPLE * 5, "five events per run")
        self.assertTrue(all(row["readable"] for row in events))
