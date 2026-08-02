"""Crash-safe and concurrent audit checkpoint persistence (#440)."""

from __future__ import annotations

import json
import multiprocessing
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from opaihub import audit
from opaihub.audit import (
    audit_path,
    checkpoint_path,
    read_audit,
    record_audit_event,
    recover_audit_checkpoint,
    verify_chain,
)


def _record_audit_event_in_worker(
    project_root: str,
    ready: object,
    start: object,
    outcomes: object,
    worker_id: int,
) -> None:
    """Append one event after all spawned writers are ready."""

    try:
        ready.put("ready")
        if not start.wait(timeout=20):
            raise TimeoutError("timed out waiting to start concurrent audit writes")
        entry = record_audit_event(
            Path(project_root), "concurrent_audit_write", worker_id=worker_id
        )
        outcomes.put({"ok": True, "sequence": entry["seq"]})
    except BaseException as exc:
        outcomes.put({"ok": False, "error": repr(exc)})


class AuditPersistenceTests(unittest.TestCase):
    def test_concurrent_writers_preserve_the_log_chain_and_checkpoint_head(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            context = multiprocessing.get_context("spawn")
            ready = context.Queue()
            start = context.Event()
            outcomes = context.Queue()
            workers = [
                context.Process(
                    target=_record_audit_event_in_worker,
                    args=(str(root), ready, start, outcomes, worker_id),
                )
                for worker_id in range(4)
            ]
            try:
                for worker in workers:
                    worker.start()
                for _ in workers:
                    self.assertEqual(ready.get(timeout=30), "ready")
                start.set()
                worker_outcomes = [outcomes.get(timeout=30) for _ in workers]
            finally:
                start.set()
                for worker in workers:
                    worker.join(timeout=30)

            self.assertTrue(all(not worker.is_alive() for worker in workers))
            self.assertTrue(all(outcome["ok"] for outcome in worker_outcomes))
            events = read_audit(root)
            self.assertEqual([event["seq"] for event in events], [1, 2, 3, 4])
            self.assertTrue(verify_chain(root)["ok"])
            checkpoint = json.loads(checkpoint_path(root).read_text(encoding="utf-8"))
            self.assertEqual(checkpoint["length"], 4)
            self.assertEqual(checkpoint["head_hash"], events[-1]["entry_hash"])

    def test_recovery_rebuilds_a_stale_checkpoint_from_a_valid_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = record_audit_event(root, "policy_allow", note="first")
            with mock.patch.object(
                audit,
                "atomic_write_text",
                side_effect=OSError("interrupted checkpoint"),
            ):
                with self.assertRaisesRegex(OSError, "interrupted checkpoint"):
                    record_audit_event(root, "policy_deny", note="second")

            self.assertEqual(len(read_audit(root)), 2)
            self.assertFalse(verify_chain(root)["ok"])
            recovered = recover_audit_checkpoint(root)
            self.assertTrue(recovered["ok"])
            self.assertTrue(recovered["recovered"])
            self.assertTrue(verify_chain(root)["ok"])
            checkpoint = json.loads(checkpoint_path(root).read_text(encoding="utf-8"))
            self.assertEqual(checkpoint["length"], 2)
            self.assertEqual(
                checkpoint["head_hash"], read_audit(root)[-1]["entry_hash"]
            )
            self.assertNotEqual(first["entry_hash"], checkpoint["head_hash"])

    def test_recovery_refuses_to_rewrite_a_checkpoint_for_a_tampered_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record_audit_event(root, "policy_allow", note="first")
            audit_path(root).write_text(
                '{"seq": 2, "event_type": "forged"}\n', encoding="utf-8"
            )

            recovered = recover_audit_checkpoint(root)

            self.assertFalse(recovered["ok"])
            self.assertFalse(recovered["recovered"])
            self.assertIn("mismatch", recovered["reason"])
