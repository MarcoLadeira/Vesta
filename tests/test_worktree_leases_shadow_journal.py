"""#613 Stage 3: worktree_leases' shadow journal, mirrored against the file it shadows.

Stage 1 named ``opaihub/worktree_leases.py`` JOURNAL_OWNED -- "leases: worktree
ownership" -- and this applies Stage 2's shadow-write + dual-read pattern to
it. The shape differs from Stage 2 in one way that simplifies the migration:
every lease save here is already a whole-record overwrite (``_save`` always
writes the complete ``lease.to_dict()``), so the mirrored event needs no
field-level reduce logic -- the latest event's payload *is* the projection.

Placing the shadow journal cost a real bug, caught by the *existing* test
suite rather than by inspection: a naive sibling-file layout put the
journal's own head-cache file (unconditionally named ``<name>.head.json``)
inside the same directory :func:`WorktreeManager.list` globs non-recursively
for ``*.json``, corrupting the legacy lease listing with a filename that is
not a lease id. These tests pin the fix (a dedicated ``journal/``
subdirectory) alongside the properties Stage 2 already established.
"""

from __future__ import annotations

import concurrent.futures
import json
import tempfile
import unittest
from pathlib import Path

from _helpers import make_repo

from opaihub.repository_safety import capture_repository_handle
from opaihub.worktree_leases import WorktreeManager


class _LeaseFixture(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.repo = self.base / "repo"
        self.repo.mkdir()
        make_repo(self.repo, files={"src/app.py": "print('ok')\n"}, commit=True)
        self.handle = capture_repository_handle(
            self.repo, task_id="task-a", run_id="run-a"
        )
        self.manager = WorktreeManager(self.repo, min_free_bytes=0)

    def _create(self, *, branch: str = "codex/task-a", target: Path | None = None):
        return self.manager.create(
            self.handle,
            task_id="task-a",
            run_id="run-a",
            owner="worker-a",
            branch=branch,
            target=target or self.base / "task-a",
            base="HEAD",
            planned_paths=("src/",),
        )


class ShadowMirrorsAcceptedTransitionsTests(_LeaseFixture):
    def test_creation_is_mirrored_and_the_shadow_agrees_with_the_file(self):
        lease = self._create()

        self.assertEqual(
            self.manager.shadow_journal_projection(lease.lease_id), lease.to_dict()
        )
        self.assertIsNone(self.manager.lease_contradiction_report(lease.lease_id))

    def test_heartbeat_is_mirrored_and_the_shadow_agrees_with_the_file(self):
        # expires_at truncates to whole seconds, so create() and heartbeat()
        # landing in the same real-world second (routine on a fast machine)
        # legitimately produce an identical timestamp -- that was flaky here,
        # not the mirroring under test. A manager with its own advancing
        # clock makes the renewal actually move without depending on how
        # fast the test runs.
        lease = self._create()
        clock = WorktreeManager(self.repo, min_free_bytes=0, now=lambda: 10_000.0)
        renewed = clock.heartbeat(lease.lease_id, owner="worker-a")

        self.assertNotEqual(renewed.expires_at, lease.expires_at)
        self.assertEqual(
            self.manager.shadow_journal_projection(lease.lease_id), renewed.to_dict()
        )
        self.assertIsNone(self.manager.lease_contradiction_report(lease.lease_id))

    def test_release_is_mirrored_and_the_shadow_agrees_with_the_file(self):
        lease = self._create()
        released = self.manager.release(lease.lease_id, owner="worker-a")

        self.assertEqual(released.state, "completed")
        self.assertEqual(
            self.manager.shadow_journal_projection(lease.lease_id), released.to_dict()
        )
        self.assertIsNone(self.manager.lease_contradiction_report(lease.lease_id))

    def test_cleanup_is_mirrored_through_every_intermediate_state(self):
        """cleanup() writes cleaning, then released -- both must be mirrored."""

        lease = self._create()
        result = self.manager.cleanup(lease.lease_id, owner="worker-a")

        self.assertEqual(result.state, "released")
        self.assertEqual(
            self.manager.shadow_journal_projection(lease.lease_id), result.to_dict()
        )
        self.assertIsNone(self.manager.lease_contradiction_report(lease.lease_id))

    def test_a_refused_operation_writes_neither_side(self):
        lease = self._create()
        before = self.manager.shadow_journal_projection(lease.lease_id)

        with self.assertRaises(Exception):
            self.manager.cleanup(lease.lease_id, owner="not-the-owner")

        self.assertEqual(self.manager.shadow_journal_projection(lease.lease_id), before)
        self.assertIsNone(self.manager.lease_contradiction_report(lease.lease_id))

    def test_the_journal_lives_outside_the_flat_lease_listing_directory(self):
        """Regression pin: a sibling-file layout corrupted list().

        The journal's own head-cache file is unconditionally named
        ``<name>.head.json`` (see ``run_journal.head_path``). A naive layout
        placed it beside the lease ``.json`` files, where
        :func:`WorktreeManager.list` globs non-recursively for ``*.json`` --
        so the head cache was listed as if it were a lease record and
        ``list()`` raised trying to parse its filename as a lease id. This
        pins the fix: journal files must never appear in that glob.
        """
        lease = self._create()
        self.manager.heartbeat(lease.lease_id, owner="worker-a")

        leases = self.manager.list()

        self.assertEqual([item.lease_id for item in leases], [lease.lease_id])

    def test_concurrent_heartbeats_leave_file_and_shadow_agreeing(self):
        lease = self._create()

        def beat(_: int):
            try:
                self.manager.heartbeat(lease.lease_id, owner="worker-a")
            except Exception:
                pass

        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(beat, range(12)))

        self.assertIsNone(self.manager.lease_contradiction_report(lease.lease_id))
        current = self.manager.load(lease.lease_id)
        self.assertEqual(
            self.manager.shadow_journal_projection(lease.lease_id),
            current.to_dict(),
        )


class ContradictionReportIsExactTests(_LeaseFixture):
    def test_an_out_of_band_file_write_is_reported(self):
        """The scenario #613 exists for: something wrote the file directly."""
        lease = self._create()
        path = self.manager._lease_path(lease.lease_id)
        tampered = {**lease.to_dict(), "owner": "someone-else", "state": "released"}
        path.write_text(json.dumps(tampered), encoding="utf-8")

        report = self.manager.lease_contradiction_report(lease.lease_id)

        self.assertIsNotNone(report)
        self.assertIn("owner", report["mismatched_fields"])
        self.assertIn("state", report["mismatched_fields"])
        self.assertEqual(report["legacy"], tampered)

    def test_a_never_created_lease_agrees_as_both_empty(self):
        self.assertIsNone(self.manager.lease_contradiction_report("never-created"))
        self.assertEqual(self.manager.shadow_journal_projection("never-created"), {})


class ReplayDeterminismTests(_LeaseFixture):
    """#613's own acceptance criterion: repeated rebuilds must agree."""

    def test_replay_agrees_with_itself_and_with_load(self):
        from opaihub import run_journal, shadow_journal, worktree_leases

        lease = self._create()
        self.manager.heartbeat(lease.lease_id, owner="worker-a")
        self.manager.release(lease.lease_id, owner="worker-a")

        # The journal mechanics moved to opaihub.shadow_journal, which this
        # module and owner_lease each hand-rolled separately first.
        journal_path = shadow_journal.journal_path_for(
            self.manager._lease_path(lease.lease_id)
        )

        def replay() -> dict[str, object]:
            return run_journal.replay(
                journal_path,
                reduce=shadow_journal._reduce,
                empty=shadow_journal._empty,
                validate=shadow_journal._validator(worktree_leases._valid_lease_record),
            )

        first = replay()
        second = replay()

        self.assertEqual(first, second)
        self.assertEqual(first, self.manager.shadow_journal_projection(lease.lease_id))


if __name__ == "__main__":  # pragma: no cover - convenience
    unittest.main()
