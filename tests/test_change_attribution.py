"""Versioned, immutable mutation evidence for issue #620."""

from __future__ import annotations

import unittest
from dataclasses import FrozenInstanceError

from opaihub.change_attribution import (
    ChangeSet,
    GitEntry,
    IndexEntry,
    MutationOperation,
    PathAttribution,
    PathIdentity,
    RepositorySnapshot,
    WorktreeEntry,
)


def _identity(path: str, object_id: str) -> PathIdentity:
    return PathIdentity(
        path=path,
        filesystem_alias=path.casefold(),
        head=GitEntry(mode="100644", object_id="1" * 40),
        index=(IndexEntry(stage=0, mode="100644", object_id="2" * 40),),
        worktree=WorktreeEntry(
            kind="file",
            mode="100644",
            object_id=object_id,
            size=7,
            binary=False,
        ),
    )


def _snapshot(*paths: PathIdentity, status: str = "available") -> RepositorySnapshot:
    return RepositorySnapshot(
        repository_id="repository-1",
        worktree_id="worktree-1",
        worktree_root="C:/repo",
        handle_id="handle-1",
        branch="main",
        head_sha="a" * 40,
        index_fingerprint="b" * 64,
        worktree_fingerprint="c" * 64,
        captured_at="2026-08-15T12:00:00+00:00",
        paths=paths,
        probe_status=status,
        unavailable_reason=(
            "repository_identity_unavailable" if status != "available" else ""
        ),
    )


class ChangeSetSchemaTests(unittest.TestCase):
    def test_round_trip_keeps_every_identity_dimension(self) -> None:
        before = PathIdentity(
            path="src/link",
            filesystem_alias="src/link",
            head=GitEntry(mode="120000", object_id="1" * 40),
            index=(
                IndexEntry(stage=1, mode="120000", object_id="2" * 40),
                IndexEntry(stage=2, mode="120000", object_id="3" * 40),
                IndexEntry(stage=3, mode="120000", object_id="4" * 40),
            ),
            worktree=WorktreeEntry(
                kind="symlink",
                mode="120000",
                object_id="5" * 40,
                size=11,
                binary=None,
                link_target_bytes_b64="dGFyZ2V0LW9uZQ==",
            ),
        )
        after = PathIdentity(
            path="src/link",
            filesystem_alias="src/link",
            head=before.head,
            index=before.index,
            worktree=WorktreeEntry(
                kind="symlink",
                mode="120000",
                object_id="6" * 40,
                size=12,
                binary=None,
                link_target_bytes_b64="dGFyZ2V0LXR3bw==",
            ),
        )
        baseline = _snapshot(before)
        terminal = _snapshot(after)
        operation = MutationOperation(
            operation_id="operation-1",
            task_id="task-1",
            run_id="run-1",
            repository_id="repository-1",
            worktree_id="worktree-1",
            kind="write_file",
            paths=("src/link",),
            intent_at="2026-08-15T12:00:01+00:00",
            before=(before,),
            before_snapshot_digest=baseline.digest,
            status="succeeded",
            observed_at="2026-08-15T12:00:02+00:00",
            after=(after,),
            after_snapshot_digest=terminal.digest,
        )
        attribution = PathAttribution(
            path="src/link",
            classification="overlapping",
            operation_ids=("operation-1",),
            before_identity_digest=before.digest,
            after_identity_digest=after.digest,
            confidence="high",
            reasons=("pre_existing_user_change", "opai_operation_observed"),
        )
        change_set = ChangeSet(
            task_id="task-1",
            run_id="run-1",
            repository_id="repository-1",
            worktree_id="worktree-1",
            baseline=baseline,
            created_at="2026-08-15T12:00:00+00:00",
            operations=(operation,),
            terminal_snapshot=terminal,
            attributions=(attribution,),
            status="completed",
            terminal_at="2026-08-15T12:00:03+00:00",
        )

        payload = change_set.to_dict()
        restored = ChangeSet.from_dict(payload)

        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["change_set_id"], change_set.change_set_id)
        self.assertEqual(payload["baseline"]["paths"][0]["path"], "src/link")
        self.assertEqual(
            [entry["stage"] for entry in payload["baseline"]["paths"][0]["index"]],
            [1, 2, 3],
        )
        self.assertEqual(
            payload["terminal_snapshot"]["paths"][0]["worktree"]["object_id"],
            "6" * 40,
        )
        self.assertEqual(restored, change_set)
        self.assertEqual(restored.digest, change_set.digest)

    def test_path_order_does_not_change_snapshot_or_change_set_identity(self) -> None:
        app = _identity("src/app.py", "3" * 40)
        guide = _identity("docs/guide.md", "4" * 40)

        first = ChangeSet(
            task_id="task-1",
            run_id="run-1",
            repository_id="repository-1",
            worktree_id="worktree-1",
            baseline=_snapshot(app, guide),
            created_at="2026-08-15T12:00:00+00:00",
        )
        second = ChangeSet(
            task_id="task-1",
            run_id="run-1",
            repository_id="repository-1",
            worktree_id="worktree-1",
            baseline=_snapshot(guide, app),
            created_at="2026-08-15T12:00:00+00:00",
        )

        self.assertEqual(first.baseline.paths, second.baseline.paths)
        self.assertEqual(first.change_set_id, second.change_set_id)

    def test_mismatched_operation_binding_is_rejected(self) -> None:
        identity = _identity("src/app.py", "3" * 40)
        baseline = _snapshot(identity)
        operation = MutationOperation(
            operation_id="operation-1",
            task_id="another-task",
            run_id="run-1",
            repository_id="repository-1",
            worktree_id="worktree-1",
            kind="write_file",
            paths=("src/app.py",),
            intent_at="2026-08-15T12:00:01+00:00",
            before=(identity,),
            before_snapshot_digest=baseline.digest,
        )

        with self.assertRaisesRegex(ValueError, "task_id"):
            ChangeSet(
                task_id="task-1",
                run_id="run-1",
                repository_id="repository-1",
                worktree_id="worktree-1",
                baseline=baseline,
                created_at="2026-08-15T12:00:00+00:00",
                operations=(operation,),
            )

    def test_failed_probe_is_explicit_evidence_not_an_empty_clean_snapshot(
        self,
    ) -> None:
        unavailable = _snapshot(status="unavailable")
        clean = _snapshot()

        self.assertFalse(unavailable.complete)
        self.assertEqual(
            unavailable.to_dict()["unavailable_reason"],
            "repository_identity_unavailable",
        )
        self.assertNotEqual(unavailable.digest, clean.digest)
        with self.assertRaisesRegex(ValueError, "unavailable_reason"):
            RepositorySnapshot(
                repository_id="repository-1",
                worktree_id="worktree-1",
                worktree_root="C:/repo",
                handle_id="handle-1",
                branch="main",
                head_sha="a" * 40,
                index_fingerprint="b" * 64,
                worktree_fingerprint="c" * 64,
                captured_at="2026-08-15T12:00:00+00:00",
                probe_status="unavailable",
            )

    def test_terminal_evidence_is_immutable_and_requires_a_terminal_snapshot(
        self,
    ) -> None:
        identity = _identity("src/app.py", "3" * 40)
        baseline = _snapshot(identity)

        with self.assertRaisesRegex(ValueError, "terminal_snapshot"):
            ChangeSet(
                task_id="task-1",
                run_id="run-1",
                repository_id="repository-1",
                worktree_id="worktree-1",
                baseline=baseline,
                created_at="2026-08-15T12:00:00+00:00",
                status="completed",
                terminal_at="2026-08-15T12:00:01+00:00",
            )

        active = ChangeSet(
            task_id="task-1",
            run_id="run-1",
            repository_id="repository-1",
            worktree_id="worktree-1",
            baseline=baseline,
            created_at="2026-08-15T12:00:00+00:00",
        )
        with self.assertRaises(FrozenInstanceError):
            active.status = "completed"  # type: ignore[misc]

    def test_deserialization_rejects_a_forged_change_set_id(self) -> None:
        change_set = ChangeSet(
            task_id="task-1",
            run_id="run-1",
            repository_id="repository-1",
            worktree_id="worktree-1",
            baseline=_snapshot(),
            created_at="2026-08-15T12:00:00+00:00",
        )
        payload = change_set.to_dict()
        payload["change_set_id"] = "changeset-forged"

        with self.assertRaisesRegex(ValueError, "change_set_id"):
            ChangeSet.from_dict(payload)

    def test_opai_only_requires_a_successful_contiguous_transition(self) -> None:
        before = _identity("src/app.py", "2" * 40)
        after = _identity("src/app.py", "3" * 40)
        baseline = _snapshot(before)
        terminal = _snapshot(after)
        failed = MutationOperation(
            operation_id="operation-1",
            task_id="task-1",
            run_id="run-1",
            repository_id="repository-1",
            worktree_id="worktree-1",
            kind="write_file",
            paths=("src/app.py",),
            intent_at="2026-08-15T12:00:01+00:00",
            before=(before,),
            before_snapshot_digest=baseline.digest,
            status="failed",
            observed_at="2026-08-15T12:00:02+00:00",
            after=(after,),
            after_snapshot_digest=terminal.digest,
            error_code="WRITE_FAILED",
        )
        claimed = PathAttribution(
            path="src/app.py",
            classification="opai_only",
            operation_ids=("operation-1",),
            before_identity_digest=before.digest,
            after_identity_digest=after.digest,
            confidence="high",
            reasons=("opai_operation_observed",),
        )

        with self.assertRaisesRegex(ValueError, "successful producer chain"):
            ChangeSet(
                task_id="task-1",
                run_id="run-1",
                repository_id="repository-1",
                worktree_id="worktree-1",
                baseline=baseline,
                created_at="2026-08-15T12:00:00+00:00",
                operations=(failed,),
                terminal_snapshot=terminal,
                attributions=(claimed,),
                status="completed",
                terminal_at="2026-08-15T12:00:03+00:00",
            )

    def test_terminal_change_set_rejects_an_unresolved_intent(self) -> None:
        identity = _identity("src/app.py", "2" * 40)
        snapshot = _snapshot(identity)
        intent = MutationOperation(
            operation_id="operation-1",
            task_id="task-1",
            run_id="run-1",
            repository_id="repository-1",
            worktree_id="worktree-1",
            kind="write_file",
            paths=("src/app.py",),
            intent_at="2026-08-15T12:00:01+00:00",
            before=(identity,),
            before_snapshot_digest=snapshot.digest,
        )

        with self.assertRaisesRegex(ValueError, "unresolved mutation intent"):
            ChangeSet(
                task_id="task-1",
                run_id="run-1",
                repository_id="repository-1",
                worktree_id="worktree-1",
                baseline=snapshot,
                created_at="2026-08-15T12:00:00+00:00",
                operations=(intent,),
                terminal_snapshot=snapshot,
                status="completed",
                terminal_at="2026-08-15T12:00:03+00:00",
            )

    def test_operation_must_bind_the_change_set_worktree_and_known_snapshots(
        self,
    ) -> None:
        identity = _identity("src/app.py", "2" * 40)
        snapshot = _snapshot(identity)
        foreign = MutationOperation(
            operation_id="operation-1",
            task_id="task-1",
            run_id="run-1",
            repository_id="repository-1",
            worktree_id="foreign-worktree",
            kind="write_file",
            paths=("src/app.py",),
            intent_at="2026-08-15T12:00:01+00:00",
            before=(identity,),
            before_snapshot_digest="f" * 64,
        )

        with self.assertRaisesRegex(ValueError, "worktree_id"):
            ChangeSet(
                task_id="task-1",
                run_id="run-1",
                repository_id="repository-1",
                worktree_id="worktree-1",
                baseline=snapshot,
                created_at="2026-08-15T12:00:00+00:00",
                operations=(foreign,),
            )

        forged_snapshot = MutationOperation(
            operation_id="operation-2",
            task_id="task-1",
            run_id="run-1",
            repository_id="repository-1",
            worktree_id="worktree-1",
            kind="write_file",
            paths=("src/app.py",),
            intent_at="2026-08-15T12:00:01+00:00",
            before=(identity,),
            before_snapshot_digest="f" * 64,
        )
        with self.assertRaisesRegex(ValueError, "unknown before snapshot"):
            ChangeSet(
                task_id="task-1",
                run_id="run-1",
                repository_id="repository-1",
                worktree_id="worktree-1",
                baseline=snapshot,
                created_at="2026-08-15T12:00:00+00:00",
                operations=(forged_snapshot,),
            )

    def test_contentless_file_is_not_complete_identity_evidence(self) -> None:
        with self.assertRaisesRegex(ValueError, "content identity"):
            WorktreeEntry(kind="file")

    def test_backslash_filename_is_not_rewritten_as_a_directory_separator(self) -> None:
        backslash = _identity(r"src\name.py", "3" * 40)
        slash = _identity("src/name.py", "3" * 40)

        self.assertEqual(backslash.path, r"src\name.py")
        self.assertNotEqual(backslash.path_bytes_hex, slash.path_bytes_hex)

    def test_snapshot_rejects_filesystem_alias_collisions(self) -> None:
        upper = PathIdentity(
            path="src/App.py",
            head=None,
            index=(),
            worktree=WorktreeEntry(kind="missing"),
            filesystem_alias="src/app.py",
        )
        lower = PathIdentity(
            path="src/app.py",
            head=None,
            index=(),
            worktree=WorktreeEntry(kind="missing"),
            filesystem_alias="src/app.py",
        )

        with self.assertRaisesRegex(ValueError, "filesystem alias"):
            _snapshot(upper, lower)

    def test_available_snapshot_requires_probed_filesystem_aliases(self) -> None:
        identity = PathIdentity(
            path="src/app.py",
            head=None,
            index=(),
            worktree=WorktreeEntry(kind="missing"),
        )

        with self.assertRaisesRegex(ValueError, "probed filesystem alias"):
            _snapshot(identity)


if __name__ == "__main__":
    unittest.main()
