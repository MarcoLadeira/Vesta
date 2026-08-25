"""#613 AC10: journal health is visible in doctor, and absence is not a fault.

Acceptance criterion 10 asks for database health, migration status and degraded
integrity to appear in doctor and diagnostics. This is the first production
code path that touches the journal at all -- until now nothing in ``opai/`` or
``opaihub/`` imported ``journal_store``.

The interesting assertions are about *restraint*. It would be easy to make
doctor shout about a journal that does not exist yet, and that would be worse
than saying nothing: nothing reads from the journal, so every project would
report ``attention`` for a condition that is entirely normal, and the field
would be trained into background noise before the day it matters.

So the tests pin both directions -- an absent or healthy journal leaves
readiness alone, a corrupt or incompatible one raises it -- and that doctor
survives a journal it cannot open at all.
"""

from __future__ import annotations

import json
import subprocess  # nosec B404 - fixed argv, throwaway test repo
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from opai import cli
from opaihub.journal_store import journal_path, open_store


def _repo(root: Path) -> Path:
    subprocess.run(  # nosec B603 B607 - fixed argv, throwaway test repo
        ["git", "init", "-q"], cwd=root, capture_output=True, check=False
    )
    return root


class _DoctorFixture(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = _repo(Path(self._tmp.name))

    def _doctor_payload(self, *, clean_integrations: bool = False) -> dict:
        """Run the real command and parse its real output.

        ``clean_integrations`` exists because of a mistake worth recording: the
        first version of the readiness test asserted ``attention`` on a bare
        temp repo, where ``missing`` already lists every client integration --
        so readiness was ``attention`` no matter what the journal said, and
        deleting the journal rule from ``cmd_doctor`` broke nothing. The test
        passed for a reason that had nothing to do with the change.

        Clearing only ``broken`` and ``missing`` leaves every other input real
        and makes the journal the single variable, so the assertion now depends
        on the wiring it claims to test.
        """

        import argparse
        import io
        from contextlib import redirect_stdout

        real_status = cli.project_status

        def patched(root, *args, **kwargs):
            status = real_status(root, *args, **kwargs)
            summary = dict(status["client_integrations"]["summary"])
            summary["broken"] = []
            summary["missing"] = []
            integrations = {**status["client_integrations"], "summary": summary}
            return {**status, "client_integrations": integrations}

        buffer = io.StringIO()
        with redirect_stdout(buffer):
            if clean_integrations:
                with mock.patch.object(cli, "project_status", patched):
                    code = cli.cmd_doctor(
                        argparse.Namespace(project=str(self.root), json=True)
                    )
            else:
                code = cli.cmd_doctor(
                    argparse.Namespace(project=str(self.root), json=True)
                )
        self.assertEqual(code, 0)
        return json.loads(buffer.getvalue())


class JournalDoctorPayloadTests(_DoctorFixture):
    def test_a_project_with_no_journal_reports_absent_and_available(self):
        payload = cli._journal_doctor(self.root)

        self.assertTrue(payload["available"])
        self.assertFalse(payload["present"])

    def test_a_healthy_journal_reports_complete_and_wal(self):
        open_store(self.root).close()

        payload = cli._journal_doctor(self.root)

        self.assertTrue(payload["present"])
        self.assertEqual(payload["integrity"]["state"], "complete")
        self.assertEqual(str(payload["journal_mode"]).lower(), "wal")

    def test_the_expected_store_version_is_reported_for_comparison(self):
        """A version alone says nothing; doctor must show what it expected."""

        open_store(self.root).close()

        payload = cli._journal_doctor(self.root)

        self.assertEqual(
            payload["expected_store_version"], payload["integrity"]["schema_version"]
        )

    def test_a_corrupt_journal_is_reported_rather_than_hidden(self):
        open_store(self.root).close()
        journal_path(self.root).write_bytes(b"this is not a database")

        payload = cli._journal_doctor(self.root)

        self.assertTrue(payload["present"])
        self.assertEqual(payload["integrity"]["state"], "corrupt")

    def test_the_payload_is_json_serialisable(self):
        """It is printed as JSON; anything unserialisable breaks the command."""

        open_store(self.root).close()

        json.dumps(cli._journal_doctor(self.root))

    def test_doctor_never_raises_when_the_store_cannot_be_read(self):
        """Doctor reports a problem; it must not become one."""

        with mock.patch(
            "opaihub.journal_store.store_health", side_effect=OSError("no disk")
        ):
            payload = cli._journal_doctor(self.root)

        self.assertFalse(payload["available"])
        self.assertEqual(payload["error_category"], "journal_unavailable")


class AbsenceIsNotAFaultTests(_DoctorFixture):
    """The restraint half: a field that cries wolf is a field nobody reads."""

    def test_a_missing_journal_does_not_need_attention(self):
        payload = cli._journal_doctor(self.root)

        self.assertFalse(cli._journal_needs_attention(payload))

    def test_a_healthy_journal_does_not_need_attention(self):
        open_store(self.root).close()

        self.assertFalse(cli._journal_needs_attention(cli._journal_doctor(self.root)))

    def test_a_degraded_journal_does_not_need_attention(self):
        """Degraded means some rows are unreadable, not that state is unknown.

        It is surfaced in the payload, but it does not escalate readiness --
        the store's own ``usable`` contract already treats degraded as
        actionable, and doctor must not contradict it.
        """

        degraded = {
            "available": True,
            "present": True,
            "integrity": {"state": "degraded", "first_invalid_sequence": 7},
        }

        self.assertFalse(cli._journal_needs_attention(degraded))

    def test_a_corrupt_journal_needs_attention(self):
        open_store(self.root).close()
        journal_path(self.root).write_bytes(b"not a database")

        self.assertTrue(cli._journal_needs_attention(cli._journal_doctor(self.root)))

    def test_an_incompatible_journal_needs_attention(self):
        """Written by a newer OPai: the user needs an upgrade, and must be told."""

        incompatible = {
            "available": True,
            "present": True,
            "integrity": {"state": "incompatible", "schema_version": 999},
        }

        self.assertTrue(cli._journal_needs_attention(incompatible))

    def test_an_unavailable_journal_module_does_not_need_attention(self):
        """If doctor could not look, it must not claim to have found a fault."""

        self.assertFalse(
            cli._journal_needs_attention(
                {"available": False, "error_category": "journal_unavailable"}
            )
        )

    def test_a_malformed_integrity_block_is_treated_as_needing_attention(self):
        """Fail closed: a present store whose health cannot be read is a fault."""

        self.assertTrue(
            cli._journal_needs_attention(
                {"available": True, "present": True, "integrity": "not-a-mapping"}
            )
        )


class TheHelpersAreActuallyWiredIntoDoctorTests(_DoctorFixture):
    """The gap the helper tests above leave open.

    Every test before this one exercises ``_journal_doctor`` and
    ``_journal_needs_attention`` directly. They would all still pass if the
    helpers were never called by ``cmd_doctor`` -- which is the whole point of
    the change. These run the real command and read its real output.
    """

    def test_the_doctor_payload_carries_the_journal_block(self):
        open_store(self.root).close()

        payload = self._doctor_payload()

        self.assertIn("runtime_journal", payload)
        self.assertEqual(payload["runtime_journal"]["integrity"]["state"], "complete")

    def test_a_healthy_journal_leaves_an_otherwise_clean_project_ready(self):
        """The baseline the next test needs, or that one proves nothing."""

        open_store(self.root).close()

        payload = self._doctor_payload(clean_integrations=True)

        self.assertEqual(payload["runtime_journal"]["integrity"]["state"], "complete")
        self.assertEqual(payload["readiness"], "ready")

    def test_a_corrupt_journal_alone_pushes_the_report_to_attention(self):
        """End to end: the readiness rule is connected, not merely defined.

        Paired with the test above, which establishes that this same project
        reads ``ready`` when the journal is healthy -- so ``attention`` here
        can only have come from the journal.
        """

        open_store(self.root).close()
        journal_path(self.root).write_bytes(b"not a database")

        payload = self._doctor_payload(clean_integrations=True)

        self.assertEqual(payload["runtime_journal"]["integrity"]["state"], "corrupt")
        self.assertEqual(payload["readiness"], "attention")

    def test_a_project_with_no_journal_stays_ready(self):
        """The restraint rule, proved through the command rather than the helper."""

        payload = self._doctor_payload(clean_integrations=True)

        self.assertFalse(payload["runtime_journal"]["present"])
        self.assertEqual(payload["readiness"], "ready")


if __name__ == "__main__":  # pragma: no cover - convenience
    unittest.main()


class MigrationVisibilityTests(_DoctorFixture):
    """#613 Stages 6-7 made observable, because a migration nobody can see the
    state of is one nobody can finish.

    The interesting assertion is the one about *not* answering. A full
    retirement verdict needs the legacy record to compare against, which doctor
    does not assemble -- so it stops at the facts rather than guessing. Saying
    "needs_legacy_comparison" is the honest answer to a question that has not
    been asked properly, and it is more useful than a confident wrong verdict.
    """

    def test_a_project_that_never_started_says_so(self):
        facts = cli._journal_migration(self.root)

        self.assertEqual(facts["retirement"], "not_started")
        self.assertEqual(facts["runs_recorded"], 0)

    def test_an_opened_journal_reports_it_cannot_judge_retirement_alone(self):
        open_store(self.root).close()

        facts = cli._journal_migration(self.root)

        self.assertEqual(facts["retirement"], "needs_legacy_comparison")

    def test_recorded_runs_are_counted(self):
        from opaihub.journal_runtime import record_admission

        for index in range(3):
            record_admission(
                self.root,
                task_id="task-a",
                run_id=f"run-{index}",
                task="t",
                now="2026-08-25T12:00:00+00:00",
            )

        self.assertEqual(cli._journal_migration(self.root)["runs_recorded"], 3)

    def test_an_unreconciled_operation_blocks_and_is_counted(self):
        """The one state doctor *can* judge on its own: work still unanswered."""

        from opaihub import idempotency

        open_store(self.root).close()
        idempotency.begin(
            self.root, idempotency.operation_key("github.pr", head="feat/x")
        )

        facts = cli._journal_migration(self.root)

        self.assertEqual(facts["unreconciled_operations"], 1)
        self.assertEqual(facts["retirement"], "blocked")

    def test_the_block_appears_in_the_full_doctor_payload(self):
        open_store(self.root).close()

        payload = self._doctor_payload()

        self.assertIn("migration", payload["runtime_journal"])
        self.assertEqual(
            payload["runtime_journal"]["migration"]["retirement"],
            "needs_legacy_comparison",
        )

    def test_migration_facts_never_raise(self):
        """Doctor reports a problem; it must not become one."""

        with mock.patch(
            "opaihub.journal_store.open_store", side_effect=OSError("gone")
        ):
            facts = cli._journal_migration(self.root)

        self.assertEqual(facts["retirement"], "not_started")
