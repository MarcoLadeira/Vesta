"""#613: the journal is reachable from the command line.

Requirement 12 asks for backup and recovery. Doctor answers "is there anything
to recover from"; this is how a person actually recovers. Shipping the recovery
path reachable only from Python would have repeated the mistake this migration
already made once, where a fully correct and fully tested reader was never
wired into anything that runs.

The tests that matter most are about ``restore``, because it is the only
destructive command in the group. It must refuse without ``--yes``, must refuse
a backup it cannot verify, and must keep the journal it replaces -- a recovery
that destroys the last good copy is a second incident, not a recovery.
"""

from __future__ import annotations

import argparse
import dataclasses
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from opai import cli
from opaihub import background_runs, journal_backup
from opaihub.journal_store import journal_path, open_store


class _JournalCommandFixture(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def _finished_runs(self, count: int) -> None:
        for index in range(count):
            run = background_runs.enqueue_automation(
                self.root, workflow_id="bug_fix", task=f"task {index}"
            )
            background_runs._save_run(
                self.root,
                dataclasses.replace(run, run_state="completed", status="done"),
            )

    def _run(self, command: str, **kwargs) -> tuple[int, str]:
        namespace = argparse.Namespace(
            journal_command=command,
            project=str(self.root),
            json=kwargs.pop("as_json", False),
            **kwargs,
        )
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = cli.cmd_journal(namespace)
        return code, buffer.getvalue()

    def _run_ids(self) -> set[str]:
        store = open_store(self.root)
        self.addCleanup(store.close)
        return {row[0] for row in store.execute("SELECT run_id FROM runs")}


class StatusTests(_JournalCommandFixture):
    def test_a_project_with_no_journal_says_so_without_failing(self):
        code, output = self._run("status")

        self.assertEqual(code, 0)
        self.assertIn("not started", output)

    def test_status_reports_the_real_migration_progress(self):
        self._finished_runs(25)

        code, output = self._run("status")

        self.assertEqual(code, 0)
        self.assertIn("runs recorded:  25", output)
        self.assertIn("compared:       25", output)
        self.assertIn("ready", output)

    def test_status_names_the_blockers_when_it_is_not_ready(self):
        """A verdict without reasons is not actionable."""

        self._finished_runs(2)

        _, output = self._run("status")

        self.assertIn("blocked", output)
        self.assertIn("insufficient_sample", output)

    def test_status_json_is_parseable(self):
        self._finished_runs(1)

        code, output = self._run("status", as_json=True)

        self.assertEqual(code, 0)
        payload = json.loads(output)
        self.assertIn("journal", payload)

    def test_status_survives_a_corrupt_journal(self):
        """Status runs when things are broken; that is when it is asked for."""

        self._finished_runs(1)
        journal_path(self.root).write_bytes(b"not a database")

        code, output = self._run("status")

        self.assertEqual(code, 0)
        self.assertIn("corrupt", output)


class BackupTests(_JournalCommandFixture):
    def test_backing_up_without_a_journal_fails_clearly(self):
        code, output = self._run("backup", keep=5)

        self.assertEqual(code, 1)
        self.assertIn("nothing to back up", output)

    def test_a_backup_is_taken_and_described(self):
        self._finished_runs(3)

        code, output = self._run("backup", keep=5)

        self.assertEqual(code, 0)
        self.assertIn("runs: 3", output)
        self.assertEqual(len(journal_backup.list_backups(self.root)), 1)

    def test_backing_up_prunes_to_the_requested_count(self):
        self._finished_runs(1)
        for _ in range(4):
            self._run("backup", keep=99)

        self._run("backup", keep=2)

        self.assertEqual(len(journal_backup.list_backups(self.root)), 2)

    def test_listing_backups_shows_nothing_when_there_are_none(self):
        code, output = self._run("backups")

        self.assertEqual(code, 0)
        self.assertIn("no backups", output)

    def test_listing_backups_shows_what_was_taken(self):
        self._finished_runs(2)
        self._run("backup", keep=5)

        code, output = self._run("backups")

        self.assertEqual(code, 0)
        self.assertIn("runs=2", output)


class RestoreTests(_JournalCommandFixture):
    """The only destructive command in the group."""

    def _backup(self) -> Path:
        record = journal_backup.create_backup(self.root)
        self.assertIsNotNone(record)
        return record.path

    def test_restore_refuses_without_confirmation(self):
        self._finished_runs(2)
        backup = self._backup()

        code, output = self._run("restore", backup=str(backup), yes=False)

        self.assertEqual(code, 2)
        self.assertIn("--yes", output)

    def test_refusing_leaves_the_journal_untouched(self):
        self._finished_runs(2)
        backup = self._backup()
        before = self._run_ids()

        self._run("restore", backup=str(backup), yes=False)

        self.assertEqual(self._run_ids(), before)

    def test_a_confirmed_restore_recovers_the_runs(self):
        self._finished_runs(3)
        backup = self._backup()
        store = open_store(self.root)
        before = {row[0] for row in store.execute("SELECT run_id FROM runs")}
        # Closed rather than left to teardown: on Windows an open handle blocks
        # the replace, and the point of this test is the recovery, not the
        # locking behaviour that has its own test below.
        store.close()
        journal_path(self.root).write_bytes(b"not a database")

        code, _ = self._run("restore", backup=str(backup), yes=True)

        self.assertEqual(code, 0)
        self.assertEqual(self._run_ids(), before)

    def test_a_restore_keeps_the_journal_it_replaced(self):
        """A recovery that destroys the last good copy is a second incident."""

        self._finished_runs(2)
        backup = self._backup()

        code, output = self._run("restore", backup=str(backup), yes=True)

        self.assertEqual(code, 0)
        self.assertIn("replaced journal was kept", output)

    def test_restoring_a_missing_backup_fails_with_a_reason(self):
        self._finished_runs(1)

        code, output = self._run(
            "restore", backup=str(self.root / "nope.sqlite3"), yes=True
        )

        self.assertEqual(code, 1)
        self.assertIn("backup_missing", output)

    def test_restoring_a_tampered_backup_is_refused(self):
        self._finished_runs(2)
        backup = self._backup()
        with open(backup, "r+b") as handle:
            handle.seek(backup.stat().st_size // 2)
            handle.write(b"\x00\x00\x00\x00")

        code, output = self._run("restore", backup=str(backup), yes=True)

        self.assertEqual(code, 1)
        self.assertIn("digest_mismatch", output)

    def test_restore_json_reports_the_refusal_too(self):
        code, output = self._run(
            "restore", backup=str(self.root / "nope.sqlite3"), yes=True, as_json=True
        )

        self.assertEqual(code, 1)
        self.assertFalse(json.loads(output)["ok"])


class TheCommandIsRegisteredTests(unittest.TestCase):
    """The helper tests above would all pass if nothing called cmd_journal."""

    def _parse(self, argv: list[str]) -> argparse.Namespace:
        parser = cli.build_parser()
        return parser.parse_args(argv)

    def test_journal_status_is_reachable_from_argv(self):
        args = self._parse(["journal", "status"])

        self.assertIs(args.func, cli.cmd_journal)
        self.assertEqual(args.journal_command, "status")

    def test_journal_backup_is_reachable_from_argv(self):
        args = self._parse(["journal", "backup", "--keep", "3"])

        self.assertIs(args.func, cli.cmd_journal)
        self.assertEqual(args.keep, 3)

    def test_journal_restore_requires_a_backup_argument(self):
        args = self._parse(["journal", "restore", "some/path.sqlite3", "--yes"])

        self.assertIs(args.func, cli.cmd_journal)
        self.assertEqual(args.backup, "some/path.sqlite3")
        self.assertTrue(args.yes)

    def test_restore_does_not_default_to_yes(self):
        """The confirmation must be opt-in, or it is not a confirmation."""

        args = self._parse(["journal", "restore", "some/path.sqlite3"])

        self.assertFalse(args.yes)


if __name__ == "__main__":  # pragma: no cover - convenience
    unittest.main()


class AJournalInUseIsNotABadBackupTests(_JournalCommandFixture):
    """Reporting the wrong cause is how a good backup gets thrown away.

    On Windows an open handle blocks replacing the journal, so a restore
    attempted while OPai is running cannot proceed. Refusing is correct --
    nothing is overwritten -- but the first version reported it as
    ``backup_unreadable``, which points the user at the one file that is
    actually fine and is still their only copy.
    """

    def test_the_refusal_names_the_journal_not_the_backup(self):
        self._finished_runs(2)
        record = journal_backup.create_backup(self.root)
        held = open_store(self.root)
        self.addCleanup(held.close)

        report = journal_backup.restore_backup(self.root, record.path)

        if report.ok:  # pragma: no cover - POSIX allows replacing an open file
            self.skipTest("this platform permits replacing an open database")
        self.assertEqual(report.reason, journal_backup.REFUSE_IN_USE)
        self.assertIn("close OPai", report.detail)

    def test_nothing_is_lost_when_the_restore_is_refused(self):
        self._finished_runs(2)
        record = journal_backup.create_backup(self.root)
        held = open_store(self.root)
        self.addCleanup(held.close)
        before = {row[0] for row in held.execute("SELECT run_id FROM runs")}

        journal_backup.restore_backup(self.root, record.path)

        self.assertEqual(
            {row[0] for row in held.execute("SELECT run_id FROM runs")}, before
        )
