"""#613 functional requirement 12: backup and recovery.

The health half of requirement 12 shipped with doctor; this is the half that
had no implementation behind it, and the reason ``test_journal_fault_injection``
carried a deliberate skip for the "unavailable backup" fault the issue lists.

The tests are weighted the way the risk is. Taking a backup has one interesting
property -- that it captures what was actually committed, including the WAL --
and restoring one has several, because restore is the operation that destroys
the thing it is replacing. So most of what follows is about refusing: a
mismatched digest, a corrupt file, a database from a newer Vesta, and the
security case the issue calls out by name, where a journal copied from another
machine must not bring its approvals with it.

That last one is worth being blunt about. An approval is a person saying yes,
once, to a specific thing, in a specific place. A file that arrived from
somewhere else is not that person. Dropping it costs one prompt; honouring it
costs the entire point of ever asking.
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from vestahub import journal_backup
from vestahub.journal_backup import (
    REFUSE_DIGEST,
    REFUSE_INCOMPATIBLE,
    REFUSE_MISSING,
    REFUSE_UNREADABLE,
    RESTORE_REFUSED,
    backup_health,
    create_backup,
    latest_backup,
    list_backups,
    prune_backups,
    restore_backup,
)
from vestahub.journal_runtime import EVENT_FINISHED, record_admission, record_terminal
from vestahub.journal_store import (
    SCHEMA_VERSION,
    compatibility_version,
    journal_path,
    open_store,
)

NOW = "2026-08-26T12:00:00+00:00"


class _BackupFixture(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def _runs(self, count: int, *, prefix: str = "run") -> list[str]:
        ids = []
        for index in range(count):
            run_id = f"{prefix}-{index}"
            fence = record_admission(
                self.root, task_id="task-a", run_id=run_id, task="a task", now=NOW
            )
            record_terminal(
                self.root,
                run_id=run_id,
                event_type=EVENT_FINISHED,
                verdict="completed",
                reason="ok",
                now=NOW,
                fence=fence,
            )
            ids.append(run_id)
        return ids

    def _run_ids(self) -> set[str]:
        store = open_store(self.root)
        self.addCleanup(store.close)
        return {row[0] for row in store.execute("SELECT run_id FROM runs")}


class TakingABackupTests(_BackupFixture):
    def test_a_project_with_no_journal_has_nothing_to_back_up(self):
        self.assertIsNone(create_backup(self.root))

    def test_a_backup_captures_the_runs_that_were_committed(self):
        self._runs(3)

        record = create_backup(self.root)

        self.assertIsNotNone(record)
        self.assertEqual(record.row_counts["runs"], 3)

    def test_a_backup_captures_work_still_living_in_the_wal(self):
        """The reason this uses the online backup API and not a file copy.

        In WAL mode a recent commit is in the ``-wal`` file, not the database.
        A backup taken by copying ``journal.sqlite3`` alone would be missing
        precisely the most recent work -- and would look perfectly valid.
        """

        self._runs(5)
        # A connection held open is what keeps commits in the -wal file rather
        # than checkpointed into the database, and a concurrent reader like
        # this is exactly the state a backup has to cope with.
        holder = open_store(self.root)
        self.addCleanup(holder.close)
        holder.execute("SELECT COUNT(*) FROM runs").fetchone()
        wal = Path(str(journal_path(self.root)) + "-wal")
        self.assertTrue(wal.exists(), "no WAL to test")

        record = create_backup(self.root)

        self.assertEqual(record.row_counts["runs"], 5)

    def test_the_manifest_describes_the_backup_without_opening_it(self):
        self._runs(2)

        record = create_backup(self.root)

        manifest = record.path.with_suffix(
            record.path.suffix + journal_backup.MANIFEST_SUFFIX
        )
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        self.assertEqual(payload["digest"], record.digest)
        self.assertEqual(payload["schema_version"], compatibility_version())

    def test_two_backups_in_the_same_second_do_not_collide(self):
        self._runs(1)

        first = create_backup(self.root, now=NOW)
        second = create_backup(self.root, now=NOW)

        self.assertNotEqual(first.path, second.path)
        self.assertEqual(len(list_backups(self.root)), 2)

    def test_an_unverifiable_backup_is_deleted_rather_than_left_to_be_found(self):
        """A backup that cannot be read is worse than none: it will be trusted."""

        self._runs(1)
        directory = journal_backup.backup_dir(self.root)

        with mock.patch.object(journal_backup, "_verify", return_value=None):
            self.assertIsNone(create_backup(self.root))

        leftovers = list(directory.glob("*.sqlite3")) if directory.is_dir() else []
        self.assertEqual(leftovers, [])

    def test_a_failed_backup_leaves_no_partial_file(self):
        self._runs(1)
        directory = journal_backup.backup_dir(self.root)

        # sqlite3.Connection is an immutable type -- its methods cannot be
        # patched -- so the failure is injected through a proxy instead.
        real = open_store(self.root)
        self.addCleanup(real.close)

        class _FailingBackup:
            def __getattr__(self, name):
                return getattr(real, name)

            def backup(self, *args, **kwargs):
                raise sqlite3.Error("disk full")

            def close(self):
                return None

        with mock.patch.object(
            journal_backup.journal_store, "open_store", return_value=_FailingBackup()
        ):
            self.assertIsNone(create_backup(self.root))

        leftovers = list(directory.glob("*.sqlite3")) if directory.is_dir() else []
        self.assertEqual(leftovers, [])

    def test_backing_up_a_corrupt_journal_returns_nothing(self):
        self._runs(1)
        journal_path(self.root).write_bytes(b"not a database")

        self.assertIsNone(create_backup(self.root))


class ListingAndPruningTests(_BackupFixture):
    def test_backups_are_listed_newest_first(self):
        self._runs(1)
        create_backup(self.root, now="2026-08-01T00:00:00+00:00")
        create_backup(self.root, now="2026-08-26T00:00:00+00:00")

        records = list_backups(self.root)

        self.assertEqual(records[0].created_at, "2026-08-26T00:00:00+00:00")

    def test_the_latest_backup_is_the_newest_one(self):
        self._runs(1)
        create_backup(self.root, now="2026-08-01T00:00:00+00:00")
        newest = create_backup(self.root, now="2026-08-26T00:00:00+00:00")

        self.assertEqual(latest_backup(self.root).path, newest.path)

    def test_one_unreadable_manifest_does_not_hide_the_others(self):
        self._runs(1)
        create_backup(self.root, now="2026-08-01T00:00:00+00:00")
        good = create_backup(self.root, now="2026-08-26T00:00:00+00:00")
        broken = journal_backup.backup_dir(self.root) / "junk.manifest.json"
        broken.write_text("{not json", encoding="utf-8")

        records = list_backups(self.root)

        self.assertIn(good.path, [record.path for record in records])

    def test_pruning_keeps_the_newest_and_removes_the_rest(self):
        self._runs(1)
        for day in range(1, 6):
            create_backup(self.root, now=f"2026-08-0{day}T00:00:00+00:00")

        removed = prune_backups(self.root, keep=2)

        self.assertEqual(len(removed), 3)
        self.assertEqual(len(list_backups(self.root)), 2)

    def test_pruning_removes_the_manifest_with_the_database(self):
        """A manifest without its database would advertise a backup that is gone."""

        self._runs(1)
        for day in range(1, 4):
            create_backup(self.root, now=f"2026-08-0{day}T00:00:00+00:00")

        prune_backups(self.root, keep=1)

        directory = journal_backup.backup_dir(self.root)
        self.assertEqual(len(list(directory.glob("*.manifest.json"))), 1)

    def test_pruning_never_removes_everything(self):
        self._runs(1)
        create_backup(self.root)

        prune_backups(self.root, keep=0)

        self.assertEqual(len(list_backups(self.root)), 1)


class RestoringTests(_BackupFixture):
    def test_a_restore_brings_back_the_runs_that_were_lost(self):
        self._runs(3, prefix="old")
        record = create_backup(self.root)
        journal_path(self.root).write_bytes(b"not a database")

        report = restore_backup(self.root, record.path)

        self.assertTrue(report.ok, report.detail)
        self.assertEqual(self._run_ids(), {"old-0", "old-1", "old-2"})

    def test_the_journal_being_replaced_is_itself_backed_up_first(self):
        """Restore must never be the step that destroys the last good copy."""

        self._runs(2, prefix="old")
        record = create_backup(self.root)
        self._runs(2, prefix="new")

        report = restore_backup(self.root, record.path)

        self.assertTrue(report.replaced_backup, "the replaced journal was not kept")
        self.assertTrue(Path(report.replaced_backup).exists())

    def test_stale_wal_frames_do_not_survive_a_restore(self):
        """Otherwise the old database reappears over the restored one."""

        self._runs(3, prefix="old")
        record = create_backup(self.root)
        self._runs(4, prefix="new")

        restore_backup(self.root, record.path)

        self.assertEqual(self._run_ids(), {"old-0", "old-1", "old-2"})

    def test_a_missing_backup_is_refused_by_name(self):
        report = restore_backup(self.root, self.root / "nope.sqlite3")

        self.assertEqual(report.status, RESTORE_REFUSED)
        self.assertEqual(report.reason, REFUSE_MISSING)

    def test_a_tampered_backup_is_refused(self):
        """Silent corruption, an interrupted copy and tampering look identical."""

        self._runs(2)
        record = create_backup(self.root)
        with open(record.path, "r+b") as handle:
            handle.seek(record.size_bytes // 2)
            handle.write(b"\x00\x00\x00\x00")

        report = restore_backup(self.root, record.path)

        self.assertEqual(report.reason, REFUSE_DIGEST)

    def test_a_corrupt_backup_with_no_manifest_is_refused(self):
        self._runs(1)
        record = create_backup(self.root)
        record.path.with_suffix(
            record.path.suffix + journal_backup.MANIFEST_SUFFIX
        ).unlink()
        record.path.write_bytes(b"not a database")

        report = restore_backup(self.root, record.path)

        self.assertEqual(report.status, RESTORE_REFUSED)
        self.assertEqual(report.reason, REFUSE_UNREADABLE)

    def test_a_backup_from_a_newer_vesta_is_incompatible_not_corrupt(self):
        """Different failures, different remedies: upgrade, not recover."""

        self._runs(1)
        record = create_backup(self.root)
        connection = sqlite3.connect(str(record.path))
        try:
            connection.execute(
                "UPDATE schema_meta SET value = ? WHERE key = 'schema_version'",
                (str(SCHEMA_VERSION + 99),),
            )
            connection.commit()
        finally:
            connection.close()
        manifest = record.path.with_suffix(
            record.path.suffix + journal_backup.MANIFEST_SUFFIX
        )
        manifest.unlink()  # the digest would otherwise catch it first

        report = restore_backup(self.root, record.path)

        self.assertEqual(report.reason, REFUSE_INCOMPATIBLE)

    def test_a_refused_restore_leaves_the_current_journal_alone(self):
        """The whole point: a refusal must cost nothing."""

        self._runs(2, prefix="live")

        restore_backup(self.root, self.root / "nope.sqlite3")

        self.assertEqual(self._run_ids(), {"live-0", "live-1"})

    def test_the_report_is_json_serialisable(self):
        report = restore_backup(self.root, self.root / "nope.sqlite3")

        json.loads(json.dumps(report.to_dict()))


class AForeignDatabaseDoesNotGrantAuthorityTests(_BackupFixture):
    """The security requirement the issue states twice, in different words.

    "Database copy from another user/device must not automatically grant
    authority", and "corruption recovery cannot silently regenerate keys,
    approvals, budgets or policy". Approvals are the concrete case: a restore
    that carried them across would let anyone who can drop a file into a
    directory manufacture consent that a person never gave.
    """

    def _approval(self, root: Path, fingerprint: str) -> None:
        store = open_store(root)
        try:
            store.execute(
                "INSERT INTO approvals(fingerprint, actor, nonce, issued_at) "
                "VALUES (?, ?, ?, ?)",
                (fingerprint, "someone-else", f"nonce-{fingerprint}", NOW),
            )
            store.commit()
        finally:
            store.close()

    def test_approvals_from_another_project_are_dropped(self):
        other = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: None)
        record_admission(other, task_id="t", run_id="r", task="x", now=NOW)
        self._approval(other, "granted-elsewhere")
        foreign = create_backup(other)

        report = restore_backup(self.root, foreign.path)

        self.assertTrue(report.ok, report.detail)
        self.assertEqual(report.approvals_dropped, 1)
        store = open_store(self.root)
        self.addCleanup(store.close)
        self.assertEqual(
            store.execute("SELECT COUNT(*) FROM approvals").fetchone()[0], 0
        )

    def test_the_drop_is_reported_rather_than_silent(self):
        """Otherwise it is indistinguishable from a bug when the prompt returns."""

        other = Path(tempfile.mkdtemp())
        record_admission(other, task_id="t", run_id="r", task="x", now=NOW)
        self._approval(other, "granted-elsewhere")
        foreign = create_backup(other)

        report = restore_backup(self.root, foreign.path)

        self.assertIn("foreign approval", report.detail)

    def test_a_projects_own_approvals_survive_its_own_backup(self):
        """Teeth the other way, or this is just deleting approvals."""

        record_admission(self.root, task_id="t", run_id="r", task="x", now=NOW)
        self._approval(self.root, "granted-here")
        own = create_backup(self.root)
        journal_path(self.root).write_bytes(b"not a database")

        report = restore_backup(self.root, own.path)

        self.assertTrue(report.ok, report.detail)
        self.assertEqual(report.approvals_dropped, 0)
        store = open_store(self.root)
        self.addCleanup(store.close)
        self.assertEqual(
            store.execute("SELECT COUNT(*) FROM approvals").fetchone()[0], 1
        )

    def test_runs_and_costs_from_a_foreign_backup_are_kept(self):
        """Only authority is refused. Evidence is still evidence."""

        other = Path(tempfile.mkdtemp())
        fence = record_admission(
            other, task_id="t", run_id="foreign-run", task="x", now=NOW
        )
        record_terminal(
            other,
            run_id="foreign-run",
            event_type=EVENT_FINISHED,
            verdict="completed",
            reason="",
            now=NOW,
            fence=fence,
        )
        foreign = create_backup(other)

        restore_backup(self.root, foreign.path)

        self.assertIn("foreign-run", self._run_ids())


class BackupHealthTests(_BackupFixture):
    """Requirement 12 asks for this in doctor, where absence must not shout."""

    def test_a_project_with_no_backups_is_reported_not_escalated(self):
        facts = backup_health(self.root)

        self.assertTrue(facts["available"])
        self.assertEqual(facts["backups"], 0)
        self.assertFalse(facts["usable"])

    def test_a_backup_is_counted_and_dated(self):
        self._runs(1)
        create_backup(self.root, now=NOW)

        facts = backup_health(self.root)

        self.assertEqual(facts["backups"], 1)
        self.assertEqual(facts["latest"], NOW)
        self.assertTrue(facts["usable"])

    def test_health_never_raises(self):
        with mock.patch.object(
            journal_backup, "list_backups", side_effect=OSError("gone")
        ):
            facts = backup_health(self.root)

        self.assertFalse(facts["available"])

    def test_the_facts_are_json_serialisable(self):
        self._runs(1)
        create_backup(self.root)

        json.dumps(backup_health(self.root))


class TheUnavailableBackupFaultTests(_BackupFixture):
    """The fault the issue lists, which could not be injected until now."""

    def test_recovery_from_an_empty_backup_directory_is_a_named_refusal(self):
        self._runs(2)
        journal_path(self.root).write_bytes(b"not a database")

        self.assertIsNone(latest_backup(self.root))

    def test_a_backup_directory_that_disappeared_lists_as_empty(self):
        self._runs(1)
        create_backup(self.root)
        for path in journal_backup.backup_dir(self.root).iterdir():
            path.unlink()

        self.assertEqual(list_backups(self.root), [])

    def test_a_manifest_whose_database_vanished_is_not_offered(self):
        """Offering a backup that is not there wastes the one chance to recover."""

        self._runs(1)
        record = create_backup(self.root)
        record.path.unlink()

        self.assertEqual(list_backups(self.root), [])


if __name__ == "__main__":  # pragma: no cover - convenience
    unittest.main()


class TheContractHoldsUnderHostilePathsTests(_BackupFixture):
    """``create_backup`` promises None, never an exception.

    That promise is load-bearing: a caller who could not rely on it would wrap
    every call in a bare ``except``, and would then swallow the real failures
    along with the silly ones. Found by an adversarial pass -- ``exist_ok=True``
    covers "already a directory" but not "already a file", so a backup
    directory occupied by a file raised ``FileExistsError`` straight through.
    """

    def _occupy(self) -> None:
        directory = journal_backup.backup_dir(self.root)
        directory.parent.mkdir(parents=True, exist_ok=True)
        directory.write_bytes(b"not a directory")

    def test_a_backup_directory_occupied_by_a_file_returns_none(self):
        self._runs(1)
        self._occupy()

        self.assertIsNone(create_backup(self.root))

    def test_listing_survives_a_backup_directory_that_is_a_file(self):
        self._occupy()

        self.assertEqual(list_backups(self.root), [])

    def test_health_survives_a_backup_directory_that_is_a_file(self):
        self._occupy()

        facts = backup_health(self.root)

        self.assertEqual(facts["backups"], 0)

    def test_restoring_from_a_directory_is_refused_not_raised(self):
        self._runs(1)
        create_backup(self.root)

        report = restore_backup(self.root, journal_backup.backup_dir(self.root))

        self.assertFalse(report.ok)
