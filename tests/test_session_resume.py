"""Session continuity (#313): durable, explicit, privacy-safe workspace resume."""

from __future__ import annotations

import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import fields
from pathlib import Path
from unittest import mock

from _helpers import FakeStreamingRunner, isolated_home, make_repo

from vesta import gui_recents, gui_web
from vesta.gui_web import boot_payload
from vestahub.app_scaffold import scaffold_app
from vestahub.build_loop import run_build_request
from vestahub.checkpoints import (
    create_run_checkpoint,
    finalize_run_checkpoint,
    load_run_checkpoint,
)
from vestahub.gui_pipeline import handle_gui_message
from vestahub.workflow_state import (
    WorkflowState,
    load_workflow_state,
    relink_workflow_checkpoint,
    save_workflow_state,
)


def _require_workflow_continuity_fields(test: unittest.TestCase) -> None:
    names = {item.name for item in fields(WorkflowState)}
    test.assertIn(
        "checkpoint_id",
        names,
        "WorkflowState must link the active run checkpoint for resume",
    )
    test.assertIn(
        "plan_steps",
        names,
        "WorkflowState must preserve the active plan for resume",
    )


class _ThreadAPI(unittest.TestCase):
    """Make missing public contracts fail as assertions, not import errors."""

    def _callable(self, name: str):
        value = getattr(gui_recents, name, None)
        self.assertTrue(
            callable(value),
            f"vesta.gui_recents.{name} is required for resumable threads",
        )
        return value

    def _constant(self, name: str) -> int:
        value = getattr(gui_recents, name, None)
        self.assertIsInstance(
            value,
            int,
            f"vesta.gui_recents.{name} must define the persisted-thread bound",
        )
        self.assertGreater(value, 0)
        return value

    def _save(self, root: Path, **overrides):
        payload = {
            "task_id": "task-313",
            "mode": "safe-auto",
            "messages": [
                {
                    "role": "user",
                    "text": "continue yesterday's implementation",
                    "status": "complete",
                    "timestamp": "2026-07-13T08:00:00+00:00",
                },
                {
                    "role": "assistant",
                    "text": "The focused tests are ready.",
                    "status": "complete",
                    "timestamp": "2026-07-13T08:01:00+00:00",
                },
            ],
            "checkpoint_id": "cp-313",
            "plan": [
                {"step": "Persist the thread", "status": "in_progress"},
            ],
            "changed_files": ["vesta/gui_web.py"],
        }
        payload.update(overrides)
        return self._callable("save_thread")(root, **payload)


class ThreadPersistenceTests(_ThreadAPI):
    def test_thread_path_uses_repository_bounded_state_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bounded = root / "private-state"
            with mock.patch("vestahub.state.state_dir", return_value=bounded):
                path = self._callable("thread_path")(root)

        self.assertEqual(path, bounded / "gui" / "thread.json")

    def test_gui_state_symlink_escape_blocks_thread_and_workflow_write_and_delete(self):
        from vestahub.workflow_state import clear_workflow_state

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            outside = Path(tmp) / "outside"
            root.mkdir()
            outside.mkdir()
            (root / ".vestahub").mkdir()
            gui_link = root / ".vestahub" / "gui"
            try:
                gui_link.symlink_to(outside, target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"directory symlinks unavailable: {exc}")
            (outside / "thread.json").write_text("outside-thread", encoding="utf-8")
            (outside / "workflow.json").write_text("outside-workflow", encoding="utf-8")

            with self.assertRaises((OSError, RuntimeError, ValueError)):
                self._save(root)
            with self.assertRaises((OSError, RuntimeError, ValueError)):
                save_workflow_state(root, WorkflowState(task_id="escape"))
            with self.assertRaises((OSError, RuntimeError, ValueError)):
                gui_recents.clear_thread(root)
            with self.assertRaises((OSError, RuntimeError, ValueError)):
                clear_workflow_state(root)
            failed_clear = gui_web.start_fresh_payload(root)

            self.assertFalse(failed_clear["ok"])
            self.assertEqual(failed_clear["error"]["code"], "SESSION_CLEAR_FAILED")
            self.assertEqual(
                (outside / "thread.json").read_text(encoding="utf-8"),
                "outside-thread",
            )
            self.assertEqual(
                (outside / "workflow.json").read_text(encoding="utf-8"),
                "outside-workflow",
            )

    def test_thread_roundtrip_is_versioned_workspace_local_and_bounded(self):
        max_messages = self._constant("MAX_THREAD_MESSAGES")
        max_text_chars = self._constant("MAX_THREAD_TEXT_CHARS")
        load_thread = self._callable("load_thread")
        thread_path = self._callable("thread_path")

        messages = [
            {
                "role": "user" if index % 2 == 0 else "assistant",
                "text": f"message-{index}-" + ("x" * (max_text_chars + 20)),
                "status": "complete",
                "timestamp": f"2026-07-13T08:{index % 60:02d}:00+00:00",
            }
            for index in range(max_messages + 5)
        ]

        with tempfile.TemporaryDirectory() as tmp:
            root_a = Path(tmp) / "workspace-a"
            root_b = Path(tmp) / "workspace-b"
            root_a.mkdir()
            root_b.mkdir()
            self._save(root_a, messages=messages)

            saved_path = thread_path(root_a)
            restored = load_thread(root_a)
            unrelated = load_thread(root_b)

        self.assertEqual(
            saved_path,
            root_a.resolve() / ".vestahub" / "gui" / "thread.json",
        )
        self.assertEqual(
            restored["schema_version"],
            getattr(gui_recents, "THREAD_SCHEMA_VERSION"),
        )
        self.assertEqual(restored["task_id"], "task-313")
        self.assertLessEqual(len(restored["messages"]), max_messages)
        self.assertTrue(restored["messages"][-1]["text"].startswith("message-"))
        self.assertTrue(
            all(len(item["text"]) <= max_text_chars for item in restored["messages"])
        )
        self.assertFalse(unrelated)

    def test_persisted_thread_redacts_secrets_and_whitelists_message_fields(self):
        secret = "sk-abcdefghijklmnopqrstuvwxyz123456"
        openai_project_key = "sk-" + "proj-" + ("A1_" * 18)
        anthropic_key = "sk-" + "ant-api03-" + ("B2_" * 18)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._save(
                root,
                messages=[
                    {
                        "role": "system",
                        "text": f"hidden provider system prompt {secret}",
                        "status": "complete",
                        "timestamp": "2026-07-13T08:00:00+00:00",
                    },
                    {
                        "role": "user",
                        "text": (
                            f"rotate token={secret}; openai={openai_project_key}; "
                            f"anthropic={anthropic_key}"
                        ),
                        "status": "complete",
                        "timestamp": "2026-07-13T08:01:00+00:00",
                        "provider_trace": f"raw trace {secret}",
                        "tool_output": "private source code",
                        "error": "raw provider error",
                    },
                    {
                        "role": "assistant",
                        "text": "I redacted it.",
                        "status": "complete",
                        "timestamp": "2026-07-13T08:02:00+00:00",
                    },
                ],
            )
            path = self._callable("thread_path")(root)
            raw = path.read_text(encoding="utf-8")
            restored = self._callable("load_thread")(root)

        self.assertNotIn(secret, raw)
        self.assertNotIn(openai_project_key, raw)
        self.assertNotIn(anthropic_key, raw)
        self.assertNotIn("hidden provider system prompt", raw)
        self.assertNotIn("provider_trace", raw)
        self.assertNotIn("private source code", raw)
        self.assertNotIn("raw provider error", raw)
        self.assertEqual(
            [message["role"] for message in restored["messages"]],
            ["user", "assistant"],
        )
        self.assertIn("[REDACTED", restored["messages"][0]["text"])
        allowed = {"role", "text", "status", "timestamp"}
        self.assertTrue(
            all(set(message) <= allowed for message in restored["messages"])
        )

    def test_legacy_v1_thread_loads_without_eager_migration_then_writes_v2(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = self._callable("thread_path")(root)
            path.parent.mkdir(parents=True, exist_ok=True)
            legacy = {
                "schema_version": 1,
                "task_id": "legacy-task",
                "mode": "ask",
                "messages": [
                    {
                        "role": "user",
                        "text": "legacy question",
                        "status": "complete",
                        "timestamp": "2026-07-13T08:00:00+00:00",
                    },
                    {
                        "role": "assistant",
                        "text": "legacy answer",
                        "status": "complete",
                        "timestamp": "2026-07-13T08:01:00+00:00",
                        "presentation": {
                            "schema_version": 1,
                            "run": {"state": "completed", "label": "Injected"},
                        },
                    },
                ],
            }
            original = json.dumps(legacy, sort_keys=True)
            path.write_text(original, encoding="utf-8")

            restored = self._callable("load_thread")(root)

            self.assertEqual(restored["schema_version"], 1)
            self.assertNotIn("presentation", restored["messages"][-1])
            self.assertEqual(path.read_text(encoding="utf-8"), original)
            gui_recents.begin_thread_turn(
                root, request_id="next-turn", text="continue", mode="ask"
            )
            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8"))["schema_version"], 2
            )

    def test_assistant_presentation_is_closed_redacted_bounded_and_provider_safe(self):
        # A deliberately fake key -- the alphabet -- used to prove redaction.
        secret = (
            "sk-history-secret-abcdefghijklmnopqrstuvwxyz"  # pragma: allowlist secret
        )
        presentation = {
            "schema_version": 1,
            "run": {
                "state": "partial",
                "label": "Partial",
                "category": "warning",
                "reason": f"Needs review token={secret}",
                "reason_code": "verification_unverified",
                "next_action": "Run focused tests",
                "automatic_retry": False,
                "retry_reason": "manual_review",
                "answer_conflicts": False,
                "provider_output": "must not persist",
            },
            "evidence": {
                "verification": {"applicable": True, "verdict": "unverified"},
                "delivery": {"applicable": True, "verdict": "delivered"},
                "economics": {"integrity": "reconciled"},
                "authority": {"mutating": True},
                "diagnostic_codes": [f"diag-{index}" for index in range(80)],
                "receipt": {"raw": "must not persist"},
            },
            "tests": {"status": "failed", "passed": 2, "failed": 1, "skipped": 0},
            "changes": {
                "summary": {
                    "files": 80,
                    "additions": 100,
                    "deletions": 10,
                    "pending": 80,
                    "approved": 0,
                    "rejected": 0,
                    "risky": 0,
                    "truncated": True,
                    "hunks": "must not persist",
                },
                "files": [
                    {
                        "path": f"src/file-{index}.py",
                        "decision": "pending",
                        "additions": 2,
                        "deletions": 1,
                        "risky": False,
                        "risk_reasons": [],
                        "untracked": False,
                        "sensitive": False,
                        "hunks": [{"lines": ["private source"]}],
                        "source": "private source",
                    }
                    for index in range(80)
                ],
            },
            "approval": {
                "kind": "command",
                "state": "needs_command_approval",
                "question": "May I run the check?",
                "command": f"tool --token={secret}",
                "reason": "one-time approval",
                "files": [f"src/file-{index}.py" for index in range(80)],
                "background_active": False,
                "objective": "must not persist",
            },
            "activity": [
                {
                    "phase": "testing",
                    "status": "failed" if index == 39 else "complete",
                    "message": f"Structured step {index}",
                    "next_action": "Review evidence",
                    "metadata": {"output": "must not persist"},
                }
                for index in range(40)
            ],
            "tool_trace": [{"output": "private source"}],
            "prompt": "must not persist",
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._save(
                root,
                messages=[
                    {
                        "role": "user",
                        "text": "question",
                        "presentation": presentation,
                    },
                    {
                        "role": "assistant",
                        "text": "answer",
                        "presentation": presentation,
                    },
                ],
            )
            restored = self._callable("load_thread")(root)
            raw = self._callable("thread_path")(root).read_text(encoding="utf-8")
            provider_context = gui_recents.normalize_resume_execution_context(restored)

        self.assertNotIn("presentation", restored["messages"][0])
        saved = restored["messages"][1]["presentation"]
        self.assertEqual(saved["schema_version"], 1)
        self.assertLessEqual(
            len(saved.get("changes", {}).get("files", [])),
            gui_recents.MAX_PRESENTATION_CHANGE_FILES,
        )
        self.assertLessEqual(
            len(saved.get("activity", [])), gui_recents.MAX_PRESENTATION_ACTIVITY_ROWS
        )
        self.assertLessEqual(
            len(json.dumps(saved, ensure_ascii=False).encode("utf-8")),
            gui_recents.MAX_PRESENTATION_BYTES,
        )
        self.assertNotIn(secret, raw)
        for forbidden in (
            "provider_output",
            "tool_trace",
            '"prompt"',
            '"objective"',
            '"receipt"',
            '"hunks"',
            '"source"',
            "private source",
        ):
            self.assertNotIn(forbidden, raw)
        self.assertTrue(
            all(
                set(message) == {"role", "text", "status", "timestamp"}
                for message in provider_context["messages"]
            )
        )

    def test_invalid_or_future_presentation_is_discarded_and_total_is_byte_bounded(
        self,
    ):
        valid = {
            "schema_version": 1,
            "run": {
                "state": "partial",
                "label": "Partial",
                "reason": "x" * 500,
            },
            "activity": [
                {"phase": "testing", "message": "y" * 400}
                for _ in range(gui_recents.MAX_PRESENTATION_ACTIVITY_ROWS)
            ],
        }
        messages = [
            {
                "role": "assistant",
                "text": f"answer {index}",
                "presentation": valid,
            }
            for index in range(gui_recents.MAX_THREAD_MESSAGES)
        ]
        messages.append(
            {
                "role": "assistant",
                "text": "future",
                "presentation": {"schema_version": 2, "run": {"state": "failed"}},
            }
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._save(root, messages=messages)
            restored = self._callable("load_thread")(root)

        self.assertNotIn("presentation", restored["messages"][-1])
        total = sum(
            len(json.dumps(item["presentation"], ensure_ascii=False).encode("utf-8"))
            for item in restored["messages"]
            if "presentation" in item
        )
        self.assertLessEqual(total, gui_recents.MAX_THREAD_PRESENTATION_BYTES)

    def test_start_fresh_is_explicit_scoped_and_preserves_checkpoint_evidence(self):
        start_fresh = getattr(gui_web, "start_fresh_payload", None)
        self.assertTrue(
            callable(start_fresh),
            "vesta.gui_web.start_fresh_payload must back the explicit fresh-start action",
        )
        load_thread = self._callable("load_thread")
        with tempfile.TemporaryDirectory() as tmp:
            root_a = Path(tmp) / "workspace-a"
            root_b = Path(tmp) / "workspace-b"
            root_a.mkdir()
            root_b.mkdir()
            make_repo(root_a, commit=True)
            make_repo(root_b, commit=True)
            self._save(root_a)
            self._save(root_b, task_id="other-task")
            save_workflow_state(
                root_a,
                WorkflowState(
                    task_id="task-313",
                    mode="implement",
                    phase="testing",
                    message="Old task still active",
                ),
            )
            checkpoint = create_run_checkpoint(
                root_a,
                task="resume this work",
                task_id="task-313",
                edit_capable=True,
                mode="implement",
                model="hermetic",
                checkpoint_id="cp-313",
                read_budget=False,
            )

            with isolated_home():
                fresh_payload = start_fresh(root_a)

            self.assertFalse(load_thread(root_a))
            self.assertEqual(load_thread(root_b)["task_id"], "other-task")
            self.assertEqual(load_workflow_state(root_a), WorkflowState())
            self.assertTrue(
                load_run_checkpoint(root_a, checkpoint.checkpoint_id).checkpoint_id
            )

        self.assertFalse(fresh_payload["resume"]["available"])
        self.assertTrue(fresh_payload["ok"])

    def test_clear_history_preserves_the_current_running_conversation(self):
        with tempfile.TemporaryDirectory() as tmp, isolated_home():
            root = make_repo(Path(tmp), commit=True)
            gui_recents.begin_thread_turn(
                root, request_id="old", text="Old conversation", mode="ask"
            )
            gui_recents.finish_thread_turn(
                root,
                request_id="old",
                answer="Old answer",
                status="complete",
                task_id="old",
            )
            gui_recents.clear_thread(root)
            gui_recents.add_recent(root, "Old conversation")
            gui_recents.begin_thread_turn(
                root,
                request_id="current",
                text="Keep this work",
                mode="safe-auto",
            )
            gui_recents.add_recent(root, "Keep this work")
            workflow = WorkflowState(
                task_id="current",
                phase="testing",
                message="Work is still running",
            )
            save_workflow_state(root, workflow)

            result = gui_web.clear_history_payload(root)

            thread = gui_recents.load_thread(root)
            self.assertTrue(result["ok"])
            self.assertEqual(thread["active_request_id"], "current")
            self.assertEqual(thread["state"], "running")
            self.assertEqual(load_workflow_state(root), workflow)
            self.assertEqual(gui_recents.load_recents(root), [])
            self.assertEqual(
                [item["title"] for item in gui_recents.list_conversations(root)],
                ["Keep this work"],
            )
            self.assertEqual(
                [item["title"] for item in result["conversations"]],
                ["Keep this work"],
            )

    def test_clear_failures_raise_and_start_fresh_reports_recoverable_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._save(root)
            save_workflow_state(
                root,
                WorkflowState(
                    task_id="task-313",
                    checkpoint_id="cp-313",
                    phase="testing",
                ),
            )
            with mock.patch(
                "pathlib.Path.unlink", side_effect=PermissionError("denied")
            ):
                with self.assertRaises(PermissionError):
                    gui_recents.clear_thread(root)
                with self.assertRaises(PermissionError):
                    from vestahub.workflow_state import clear_workflow_state

                    clear_workflow_state(root)

            with mock.patch(
                "vesta.gui_recents.clear_thread",
                side_effect=PermissionError("denied"),
            ):
                failed = gui_web.start_fresh_payload(root)

            self.assertFalse(failed["ok"])
            self.assertEqual(failed["error"]["code"], "SESSION_CLEAR_FAILED")
            self.assertTrue(failed["error"]["recoveryActions"])
            self.assertTrue(failed["resume"]["available"])
            self.assertTrue(gui_recents.load_thread(root))

    def test_corrupt_or_future_thread_state_recovers_to_no_resume(self):
        thread_path = self._callable("thread_path")
        load_thread = self._callable("load_thread")
        with tempfile.TemporaryDirectory() as tmp, isolated_home():
            root = make_repo(Path(tmp), commit=True)
            path = thread_path(root)
            path.parent.mkdir(parents=True, exist_ok=True)
            corrupt_values = (
                "{not-json",
                json.dumps({"schema_version": 999, "messages": []}),
                json.dumps(
                    {
                        "schema_version": getattr(
                            gui_recents, "THREAD_SCHEMA_VERSION", 1
                        ),
                        "task_id": "task-313",
                        "messages": "not-a-list",
                    }
                ),
            )
            for value in corrupt_values:
                with self.subTest(value=value[:20]):
                    path.write_text(value, encoding="utf-8")
                    self.assertFalse(load_thread(root))
                    resume = boot_payload(root).get("resume")
                    self.assertIsInstance(resume, dict)
                    self.assertFalse(resume["available"])


class WorkflowContinuityTests(unittest.TestCase):
    def test_checkpoint_relink_never_overwrites_a_different_active_workflow(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), commit=True)
            save_workflow_state(
                root,
                WorkflowState(
                    task_id="new-turn",
                    checkpoint_id="new-checkpoint",
                    phase="running",
                    changed_files=("new.py",),
                ),
            )

            preserved = relink_workflow_checkpoint(
                root,
                checkpoint_id="old-checkpoint",
                changed_files=("old.py",),
            )

            self.assertEqual(preserved.checkpoint_id, "new-checkpoint")
            self.assertEqual(preserved.changed_files, ("new.py",))
            self.assertEqual(load_workflow_state(root).checkpoint_id, "new-checkpoint")

    def test_workflow_roundtrip_includes_active_plan_and_checkpoint_link(self):
        _require_workflow_continuity_fields(self)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = WorkflowState(
                task_id="task-313",
                mode="implement",
                phase="testing",
                message="Running focused tests",
                checkpoint_id="cp-313",
                plan_steps=("Persist thread", "Restore explicit resume choice"),
            )
            save_workflow_state(root, state)
            restored = load_workflow_state(root)

        self.assertEqual(restored.checkpoint_id, "cp-313")
        self.assertEqual(
            restored.plan_steps,
            ("Persist thread", "Restore explicit resume choice"),
        )

    def test_workflow_free_text_is_redacted_and_bounded_before_persistence(self):
        openai_project_key = "sk-" + "proj-" + ("C3_" * 18)
        anthropic_key = "sk-" + "ant-api03-" + ("D4_" * 18)
        oversized = "x" * 10_000
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            save_workflow_state(
                root,
                WorkflowState(
                    task_id="task-313",
                    checkpoint_id="cp-313",
                    message=f"{openai_project_key} {oversized}",
                    blocker=f"blocked by {anthropic_key}",
                    next_actions=(f"rotate {openai_project_key}",),
                    plan_steps=(f"remove {anthropic_key} {oversized}",),
                    history=(
                        {
                            "message": f"history {openai_project_key}",
                            "metadata": {"detail": anthropic_key},
                        },
                    ),
                    provider={"diagnostic": anthropic_key},
                ),
            )
            path = root / ".vestahub" / "gui" / "workflow.json"
            raw = path.read_text(encoding="utf-8")
            restored = load_workflow_state(root)

        self.assertNotIn(openai_project_key, raw)
        self.assertNotIn(anthropic_key, raw)
        self.assertIn("[REDACTED", raw)
        self.assertLessEqual(len(restored.message), 2_000)
        self.assertLessEqual(len(restored.plan_steps[0]), 500)
        self.assertNotIn(openai_project_key, json.dumps(restored.to_dict()))
        self.assertNotIn(anthropic_key, json.dumps(restored.to_dict()))

    def test_workflow_concurrent_writers_are_atomic_and_keep_linked_fields_together(
        self,
    ):
        workers = 12
        writes_per_worker = 40

        def writer(root: Path, worker: int) -> list[str]:
            errors: list[str] = []
            for iteration in range(writes_per_worker):
                suffix = f"{worker}-{iteration}"
                try:
                    save_workflow_state(
                        root,
                        WorkflowState(
                            task_id=f"task-{suffix}",
                            checkpoint_id=f"cp-{suffix}",
                            plan_steps=(f"plan-{suffix}",),
                        ),
                    )
                    loaded = load_workflow_state(root)
                except Exception as exc:  # noqa: BLE001 - regression captures races
                    errors.append(type(exc).__name__)
                    continue
                if loaded.task_id:
                    linked = loaded.task_id.removeprefix("task-")
                    if loaded.checkpoint_id != f"cp-{linked}" or loaded.plan_steps != (
                        f"plan-{linked}",
                    ):
                        errors.append("lost-linkage")
            return errors

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with ThreadPoolExecutor(max_workers=workers) as pool:
                errors = [
                    error
                    for batch in pool.map(
                        lambda index: writer(root, index), range(workers)
                    )
                    for error in batch
                ]
            final = load_workflow_state(root)

        self.assertEqual(errors, [])
        suffix = final.task_id.removeprefix("task-")
        self.assertEqual(final.checkpoint_id, f"cp-{suffix}")
        self.assertEqual(final.plan_steps, (f"plan-{suffix}",))


class ThreadLifecycleTests(unittest.TestCase):
    def test_incomplete_rollback_persists_honest_remaining_file_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            gui_web._persist_turn_start(
                root, "rollback-request", "change two files", "build"
            )

            gui_web._persist_turn_result(
                root,
                "rollback-request",
                {
                    "status": "partial_rollback",
                    "rolled_back": ["app.js"],
                    "remaining_changed_files": ["extra.js"],
                    "applied": [{"path": "extra.js", "action": "created"}],
                },
                mode="build",
                build=True,
            )
            thread = gui_recents.load_thread(root)

            self.assertEqual(thread["messages"][-1]["status"], "failed")
            self.assertIn("rollback was incomplete", thread["messages"][-1]["text"])
            self.assertIn("extra.js", thread["messages"][-1]["text"])
            self.assertEqual(thread["changed_files"], ["extra.js"])

    def test_late_worker_result_cannot_recreate_successfully_cleared_thread(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            epoch = gui_web._SessionPersistenceEpoch()
            token = epoch.capture()
            gui_web._persist_turn_start(
                root,
                "late-request",
                "work still running",
                "safe-auto",
            )

            cleared = epoch.invalidate_on_success(
                lambda: gui_web.start_fresh_payload(root)
            )
            persisted = epoch.run_if_current(
                token,
                lambda: gui_web._persist_turn_result(
                    root,
                    "late-request",
                    {"status": "answered", "answer": "late answer"},
                    mode="safe-auto",
                ),
            )

            self.assertTrue(cleared["ok"])
            self.assertFalse(persisted)
            self.assertFalse(gui_recents.load_thread(root))

    def test_gui_turn_lifecycle_is_durable_and_rejects_stale_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            gui_web._persist_turn_start(root, "request-one", "first task", "safe-auto")
            gui_web._persist_turn_start(root, "request-two", "second task", "safe-auto")
            gui_web._persist_turn_result(
                root,
                "request-one",
                {"status": "answered", "answer": "stale answer"},
                mode="safe-auto",
            )
            gui_web._persist_turn_result(
                root,
                "request-two",
                {
                    "status": "answered",
                    "answer": "Current answer",
                    "checkpoint_id": "cp-313",
                    "workflow": {
                        "task_id": "task-313",
                        "mode": "implement",
                        "plan_steps": ["Run focused tests"],
                    },
                    "changed_files": ["vesta/gui_web.py"],
                    "tool_trace": [{"source": "must never persist"}],
                },
                mode="safe-auto",
            )
            restored = gui_recents.load_thread(root)
            raw = gui_recents.thread_path(root).read_text(encoding="utf-8")

        self.assertEqual(restored["state"], "complete")
        self.assertEqual(restored["task_id"], "task-313")
        self.assertEqual(restored["checkpoint_id"], "cp-313")
        self.assertEqual(restored["messages"][-1]["text"], "Current answer")
        self.assertNotIn("stale answer", raw)
        self.assertNotIn("must never persist", raw)

    def test_build_lifecycle_persists_summary_not_provider_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            gui_web._persist_turn_start(root, "build-one", "change the UI", "build")
            gui_web._persist_turn_result(
                root,
                "build-one",
                {
                    "status": "applied",
                    "answer": "```file:secret.py\nprivate source\n```",
                    "applied": [
                        {"path": "vesta/assets/web/app.js", "action": "updated"}
                    ],
                },
                mode="build",
                build=True,
            )
            raw = gui_recents.thread_path(root).read_text(encoding="utf-8")
            thread = gui_recents.load_thread(root)

        self.assertIn("Applied and verified 1 change", raw)
        self.assertNotIn("private source", raw)
        self.assertEqual(thread["mode"], "build")

    def test_blocked_build_preserves_its_channel_over_nested_agent_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            gui_web._persist_turn_start(root, "build-gate", "add search", "build")
            gui_web._persist_turn_result(
                root,
                "build-gate",
                {
                    "status": "needs_auto_confirmation",
                    "answer": "Confirm the named cloud model.",
                    "workflow": {"mode": "explain", "task_id": "build-gate"},
                },
                mode="build",
                build=True,
            )

            thread = gui_recents.load_thread(root)

        self.assertEqual(thread["mode"], "build")
        self.assertEqual(thread["messages"][-1]["status"], "failed")

    def test_explicit_resume_context_reaches_provider_and_never_loads_implicitly(self):
        class ContextRunner(FakeStreamingRunner):
            def complete(self, prompt: str, **kwargs):
                self.calls.append({"prompt": prompt, **kwargs})
                return {"text": "continued", "cost": 0.0}

        marker = "prior-thread-marker-313"
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), commit=True)
            gui_recents.save_thread(
                root,
                task_id="task-313",
                mode="safe-auto",
                messages=[
                    {
                        "role": "user",
                        "text": marker,
                        "status": "complete",
                        "timestamp": "2026-07-13T08:00:00+00:00",
                    },
                    {
                        "role": "assistant",
                        "text": "The focused tests passed.",
                        "status": "complete",
                        "timestamp": "2026-07-13T08:01:00+00:00",
                    },
                ],
                checkpoint_id="cp-313",
                plan=[{"step": "Run the full suite", "status": "in_progress"}],
            )
            explicit_context = gui_recents.resume_execution_context(root)
            explicit_runner = ContextRunner(chunks=["continued"])
            explicit = handle_gui_message(
                root,
                "continue now",
                model_id="account:claude:opus",
                mode="ask",
                account_runner=explicit_runner,
                resume_context=explicit_context,
            )
            implicit_runner = ContextRunner(chunks=["new session"])
            implicit = handle_gui_message(
                root,
                "unrelated new request",
                model_id="account:claude:opus",
                mode="ask",
                account_runner=implicit_runner,
            )

        self.assertEqual(explicit["status"], "answered")
        self.assertIn(marker, explicit_runner.calls[0]["prompt"])
        self.assertIn("Run the full suite", explicit_runner.calls[0]["prompt"])
        self.assertIn("untrusted quoted data", explicit_runner.calls[0]["prompt"])
        self.assertEqual(implicit["status"], "answered")
        self.assertNotIn(marker, implicit_runner.calls[0]["prompt"])
        self.assertLessEqual(
            len(json.dumps(explicit_context)),
            gui_recents.MAX_RESUME_CONTEXT_CHARS,
        )

    def test_build_reuses_checkpoint_after_apply_and_links_workflow_and_thread(self):
        class EditingRunner(FakeStreamingRunner):
            def __init__(self):
                super().__init__(
                    chunks=["```file:app.js\nconsole.log('resumed');\n```"]
                )

            def complete(self, prompt: str, **kwargs):
                self.calls.append({"prompt": prompt, **kwargs})
                return {
                    "text": "```file:app.js\nconsole.log('resumed');\n```",
                    "cost": 0.0,
                }

        with tempfile.TemporaryDirectory() as tmp:
            result = scaffold_app(Path(tmp), "a todo app")
            root = make_repo(Path(result.root), commit=True)
            gui_web._persist_turn_start(root, "build-313", "change app.js", "build")
            report = run_build_request(
                root,
                "change app.js",
                model="claude:opus",
                account_runner=EditingRunner(),
            )
            gui_web._persist_turn_result(
                root,
                "build-313",
                report,
                mode="build",
                build=True,
            )
            checkpoint = load_run_checkpoint(root, report["checkpoint_id"])
            workflow = load_workflow_state(root)
            thread = gui_recents.load_thread(root)

        self.assertEqual(report["status"], "applied")
        self.assertEqual(checkpoint.checkpoint_id, report["checkpoint_id"])
        self.assertEqual(checkpoint.result_changed_files, ("app.js",))
        self.assertEqual(checkpoint.changed_during_run, ("app.js",))
        self.assertEqual(report["checkpoint"]["completion_state"], "answered")
        self.assertEqual(report["workflow"]["checkpoint_id"], checkpoint.checkpoint_id)
        self.assertEqual(report["workflow"]["changed_files"], ["app.js"])
        self.assertEqual(workflow.checkpoint_id, checkpoint.checkpoint_id)
        self.assertEqual(workflow.changed_files, ("app.js",))
        self.assertEqual(thread["checkpoint_id"], checkpoint.checkpoint_id)
        self.assertEqual(thread["changed_files"], ["app.js"])


class BootResumeContractTests(_ThreadAPI):
    def test_repeated_boot_is_read_only_for_a_linked_pending_checkpoint(self):
        with tempfile.TemporaryDirectory() as tmp, isolated_home():
            root = make_repo(Path(tmp), commit=True)
            pending = create_run_checkpoint(
                root,
                task="active work in another window",
                task_id="task-active",
                edit_capable=True,
                mode="implement",
                model="hermetic",
                checkpoint_id="active-cp",
                read_budget=False,
            )
            save_workflow_state(
                root,
                WorkflowState(
                    task_id="task-active",
                    checkpoint_id=pending.checkpoint_id,
                    phase="implementing",
                ),
            )
            self._save(root, task_id="task-active", checkpoint_id=pending.checkpoint_id)
            checkpoint_path = (
                root
                / ".vestahub"
                / "agent"
                / "checkpoints"
                / f"{pending.checkpoint_id}.json"
            )
            before = checkpoint_path.read_bytes()
            before_mtime = checkpoint_path.stat().st_mtime_ns

            first = boot_payload(root)["resume"]
            second = boot_payload(root)["resume"]

            self.assertEqual(first["checkpoint"]["completion_state"], "pending")
            self.assertEqual(second["checkpoint"]["completion_state"], "pending")
            self.assertEqual(checkpoint_path.read_bytes(), before)
            self.assertEqual(checkpoint_path.stat().st_mtime_ns, before_mtime)

    def test_boot_without_a_saved_thread_never_mutates_unlinked_pending_evidence(self):
        with tempfile.TemporaryDirectory() as tmp, isolated_home():
            root = make_repo(Path(tmp), commit=True)
            pending = create_run_checkpoint(
                root,
                task="unlinked work",
                task_id="other-task",
                edit_capable=True,
                mode="implement",
                model="hermetic",
                checkpoint_id="unlinked-cp",
                read_budget=False,
            )
            checkpoint_path = (
                root
                / ".vestahub"
                / "agent"
                / "checkpoints"
                / f"{pending.checkpoint_id}.json"
            )
            before = checkpoint_path.read_bytes()

            resume = boot_payload(root)["resume"]

            self.assertFalse(resume["available"])
            self.assertEqual(checkpoint_path.read_bytes(), before)
            self.assertEqual(
                load_run_checkpoint(root, pending.checkpoint_id).completion_state,
                "pending",
            )

    def test_finalization_between_boots_remains_terminal(self):
        with tempfile.TemporaryDirectory() as tmp, isolated_home():
            root = make_repo(Path(tmp), commit=True)
            pending = create_run_checkpoint(
                root,
                task="work finishing in another window",
                task_id="task-active",
                edit_capable=True,
                mode="implement",
                model="hermetic",
                checkpoint_id="finishing-cp",
                read_budget=False,
            )
            save_workflow_state(
                root,
                WorkflowState(
                    task_id="task-active",
                    checkpoint_id=pending.checkpoint_id,
                    phase="implementing",
                ),
            )
            self._save(root, task_id="task-active", checkpoint_id=pending.checkpoint_id)

            first = boot_payload(root)["resume"]
            finalize_run_checkpoint(
                root,
                pending.checkpoint_id,
                completion_state="answered",
                outcome="completed",
                changed_files=["finished.py"],
            )
            second = boot_payload(root)["resume"]

            self.assertEqual(first["checkpoint"]["completion_state"], "pending")
            self.assertEqual(second["checkpoint"]["completion_state"], "answered")
            self.assertEqual(second["checkpoint"]["changed_files"], ["finished.py"])
            self.assertEqual(
                load_run_checkpoint(root, pending.checkpoint_id).completion_state,
                "answered",
            )

    def test_boot_offers_explicit_resume_with_thread_workflow_and_latest_checkpoint(
        self,
    ):
        _require_workflow_continuity_fields(self)
        with tempfile.TemporaryDirectory() as tmp, isolated_home():
            root = make_repo(Path(tmp), commit=True)
            older = create_run_checkpoint(
                root,
                task="resume this work",
                task_id="task-313",
                edit_capable=True,
                mode="implement",
                model="hermetic",
                checkpoint_id="cp-001",
                read_budget=False,
            )
            finalize_run_checkpoint(
                root,
                older.checkpoint_id,
                completion_state="answered",
                changed_files=["vesta/old.py"],
            )
            newest = create_run_checkpoint(
                root,
                task="resume this work",
                task_id="task-313",
                edit_capable=True,
                mode="implement",
                model="hermetic",
                checkpoint_id="cp-999",
                read_budget=False,
            )
            save_workflow_state(
                root,
                WorkflowState(
                    task_id="task-313",
                    mode="implement",
                    phase="testing",
                    message="Tests were running when Vesta closed",
                    checkpoint_id=newest.checkpoint_id,
                    plan_steps=("Persist state", "Run focused tests"),
                    changed_files=("vesta/gui_web.py",),
                ),
            )
            self._save(
                root,
                checkpoint_id=newest.checkpoint_id,
                changed_files=["vesta/gui_web.py"],
            )
            broken = root / ".vestahub" / "agent" / "checkpoints" / "broken.json"
            broken.write_text("{bad-checkpoint", encoding="utf-8")

            resume = boot_payload(root).get("resume")

        self.assertIsInstance(resume, dict)
        self.assertTrue(resume["available"])
        self.assertTrue(resume["requires_choice"])
        self.assertEqual(resume["thread"]["task_id"], "task-313")
        self.assertEqual(resume["thread"]["messages"][0]["role"], "user")
        self.assertEqual(resume["workflow"]["phase"], "testing")
        self.assertEqual(resume["workflow"]["plan_steps"][1], "Run focused tests")
        self.assertEqual(resume["checkpoint"]["id"], newest.checkpoint_id)
        self.assertEqual(resume["checkpoint"]["completion_state"], "pending")
        self.assertEqual(resume["checkpoint"]["recovery_actions"], [])

    def test_explicit_thread_checkpoint_wins_same_timestamp_and_id_order_ties(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            isolated_home(),
            mock.patch(
                "vestahub.checkpoints._now", return_value="2026-07-13T08:00:00+00:00"
            ),
        ):
            root = make_repo(Path(tmp), commit=True)
            for checkpoint_id in ("aaa", "zzz"):
                checkpoint = create_run_checkpoint(
                    root,
                    task="same task",
                    task_id="task-313",
                    edit_capable=True,
                    mode="implement",
                    model="hermetic",
                    checkpoint_id=checkpoint_id,
                    read_budget=False,
                )
                finalize_run_checkpoint(
                    root,
                    checkpoint.checkpoint_id,
                    completion_state="answered",
                )
            self._save(root, checkpoint_id="aaa")
            save_workflow_state(
                root,
                WorkflowState(
                    task_id="task-313",
                    checkpoint_id="zzz",
                    mode="implement",
                    phase="testing",
                ),
            )

            resume = boot_payload(root)["resume"]

        self.assertEqual(resume["checkpoint"]["id"], "aaa")

    def test_running_thread_prefers_new_workflow_checkpoint_after_crash(self):
        with tempfile.TemporaryDirectory() as tmp, isolated_home():
            root = make_repo(Path(tmp), commit=True)
            old = create_run_checkpoint(
                root,
                task="completed turn",
                task_id="task-313",
                edit_capable=True,
                mode="implement",
                model="hermetic",
                checkpoint_id="old-cp",
                read_budget=False,
            )
            finalize_run_checkpoint(
                root,
                old.checkpoint_id,
                completion_state="answered",
            )
            self._save(root, checkpoint_id=old.checkpoint_id)
            gui_recents.begin_thread_turn(
                root,
                request_id="request-after-old-cp",
                text="continue with the new turn",
                mode="safe-auto",
            )
            new = create_run_checkpoint(
                root,
                task="continue with the new turn",
                task_id="task-new-turn",
                edit_capable=True,
                mode="implement",
                model="hermetic",
                checkpoint_id="new-cp",
                read_budget=False,
            )
            save_workflow_state(
                root,
                WorkflowState(
                    task_id="task-new-turn",
                    checkpoint_id=new.checkpoint_id,
                    mode="implement",
                    phase="implementing",
                    message="Provider was running when Vesta closed",
                ),
            )

            resume = boot_payload(root)["resume"]

        self.assertEqual(resume["thread"]["state"], "running")
        self.assertEqual(resume["thread"]["checkpoint_id"], old.checkpoint_id)
        self.assertEqual(resume["workflow"]["checkpoint_id"], new.checkpoint_id)
        self.assertEqual(resume["checkpoint"]["id"], new.checkpoint_id)
        self.assertEqual(resume["checkpoint"]["completion_state"], "pending")
        self.assertEqual(resume["checkpoint"]["recovery_actions"], [])


class CliResumeParityTests(_ThreadAPI):
    """`vesta resume` reports exactly what the GUI boot offers (#313 parity)."""

    def _run_cli(self, root: Path, *flags: str):
        import contextlib as _ctx
        import io

        from vesta.cli import main

        out = io.StringIO()
        with _ctx.redirect_stdout(out):
            code = main(["resume", "--project", str(root), *flags])
        return code, out.getvalue()

    def test_cli_json_equals_gui_boot_resume_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp))
            self._save(root)
            code, out = self._run_cli(root, "--json")
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(out), gui_web._resume_payload(root))
            # Reading via the CLI is read-only: the offer is still available.
            self.assertTrue(gui_web._resume_payload(root)["available"])

    def test_cli_markdown_renders_available_and_empty_states(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp))
            code, out = self._run_cli(root, "--markdown")
            self.assertEqual(code, 0)
            self.assertIn("No resumable session", out)
            self._save(root)
            code, out = self._run_cli(root, "--markdown")
            self.assertEqual(code, 0)
            self.assertIn("Resumable session", out)
            self.assertIn("continue yesterday's implementation", out)

    def test_cli_markdown_shows_this_process_as_owner_for_a_running_thread(self):
        # #545: the addendum's "ownership/lease view" -- a second terminal
        # running `vesta resume` while a turn is active (from either surface)
        # can tell "still running, owned right here" from an abandoned one.
        # begin_thread_turn is what actually claims a live lease (#295
        # invariant 4); save_thread always persists an empty one.
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp))
            self._callable("begin_thread_turn")(
                root, request_id="req-owner", text="a live task", mode="safe-auto"
            )
            code, out = self._run_cli(root, "--markdown")
            self.assertEqual(code, 0)
            self.assertIn("Owner: this process", out)


if __name__ == "__main__":
    unittest.main()
