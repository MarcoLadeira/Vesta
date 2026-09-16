from __future__ import annotations

import multiprocessing
import subprocess
import tempfile
import unittest
from pathlib import Path

from vestahub.aci import AgentComputerInterface, Observation
from vestahub.agent_runtime import AgentRuntime, AgentWorkbench, RuntimePhase
from vestahub.safety_gates import evaluate_safety_gates
from vestahub.task_packet import build_task_packet
from vestahub.test_loop import TestLoop, parse_test_failure
from vestahub.provider_adapters import ExecutionRequest, adapter_for
from vestahub.workflow_ledger import WorkflowLedger
from vestahub.workflow_templates import workflow_templates


def _concurrent_transition_worker(
    project_root: str,
    task_id: str,
    ready: object,
    start: object,
    outcomes: object,
    reason: str,
) -> None:
    """Transition one shared task after both spawned workers are initialized."""

    announced_ready = False
    try:
        runtime = AgentRuntime(Path(project_root), task="shared task", task_id=task_id)
        ready.put("ready")
        announced_ready = True
        if not start.wait(timeout=20):
            raise TimeoutError("timed out waiting to start the shared transition")
        state = runtime.block(reason)
        outcomes.put({"ok": True, "phase": state.phase.value})
    except BaseException as exc:
        if not announced_ready:
            ready.put({"error": repr(exc)})
        outcomes.put({"ok": False, "error": repr(exc)})


class RuntimeStateMachineTests(unittest.TestCase):
    def test_runtime_exposes_every_required_phase(self):
        self.assertEqual(
            {phase.value for phase in RuntimePhase},
            {
                "idle",
                "intent_resolved",
                "repo_resolved",
                "issue_selected",
                "context_gathering",
                "planning",
                "awaiting_approval",
                "implementing",
                "testing",
                "repairing",
                "reviewing_diff",
                "preparing_pr",
                "pr_created",
                "merge_check_running",
                "merged",
                "blocked",
                "failed",
                "completed",
            },
        )

    def test_transitions_are_validated_persisted_and_keep_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = AgentRuntime(root, task="fix issue #42", task_id="task-42")
            runtime.transition(
                RuntimePhase.INTENT_RESOLVED,
                message="Implement mode selected",
                metadata={"mode": "implement"},
                next_actions=("resolve repository",),
            )
            runtime.transition(RuntimePhase.REPO_RESOLVED, message="Repository ready")
            restored = AgentRuntime.load(root, "task-42")

            self.assertEqual(restored.state.phase, RuntimePhase.REPO_RESOLVED)
            self.assertEqual(len(restored.state.history), 2)
            self.assertEqual(restored.state.history[0].metadata["mode"], "implement")
            self.assertEqual(restored.state.next_actions, ())

    def test_invalid_transition_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = AgentRuntime(Path(tmp), task="ship it")
            with self.assertRaises(ValueError):
                runtime.transition(RuntimePhase.MERGED, message="skipped every gate")

    def test_runtime_task_id_cannot_escape_state_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                AgentRuntime(Path(tmp), task="fix it", task_id="../../escape")

    def test_runtime_state_redacts_secret_bearing_observations_before_persisting(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = AgentRuntime(root, task="fix auth")
            runtime.transition(
                RuntimePhase.INTENT_RESOLVED,
                message="token=ghp_abcdefghijklmnopqrstuvwxyz123456",
                metadata={"stderr": "sk-abcdefghijklmnopqrst"},
            )
            raw = runtime.path.read_text(encoding="utf-8")

        self.assertNotIn("ghp_abcdefghijklmnopqrstuvwxyz123456", raw)
        self.assertNotIn("sk-abcdefghijklmnopqrst", raw)

    def test_blocker_and_resume_are_explicit_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = AgentRuntime(Path(tmp), task="fix it")
            runtime.transition(RuntimePhase.INTENT_RESOLVED, message="Intent ready")
            runtime.block("dirty conflict", next_actions=("use an isolated worktree",))
            self.assertEqual(runtime.state.phase, RuntimePhase.BLOCKED)
            self.assertEqual(runtime.state.blocker, "dirty conflict")
            runtime.resume(RuntimePhase.REPO_RESOLVED, message="Worktree ready")
            self.assertEqual(runtime.state.phase, RuntimePhase.REPO_RESOLVED)
            self.assertEqual(runtime.state.blocker, "")

    def test_concurrent_workers_preserve_each_task_transition(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_id = "shared-task"
            AgentRuntime(root, task="shared task", task_id=task_id)
            context = multiprocessing.get_context("spawn")
            ready = context.Queue()
            start = context.Event()
            outcomes = context.Queue()
            workers = [
                context.Process(
                    target=_concurrent_transition_worker,
                    args=(
                        str(root),
                        task_id,
                        ready,
                        start,
                        outcomes,
                        f"worker {number} needs attention",
                    ),
                )
                for number in range(2)
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
            self.assertEqual(
                [outcome["ok"] for outcome in worker_outcomes], [True, True]
            )
            restored = AgentRuntime.load(root, task_id)
            self.assertEqual(restored.state.phase, RuntimePhase.BLOCKED)
            self.assertEqual(
                [event.sequence for event in restored.state.history], [1, 2]
            )
            self.assertEqual(
                {event.blocker for event in restored.state.history},
                {"worker 0 needs attention", "worker 1 needs attention"},
            )

    def test_workbench_react_loop_turns_observations_into_runtime_transitions(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = AgentRuntime(Path(tmp), task="fix it")
            runtime.transition(RuntimePhase.INTENT_RESOLVED, message="Intent ready")
            runtime.transition(RuntimePhase.REPO_RESOLVED, message="Repo ready")
            runtime.transition(RuntimePhase.CONTEXT_GATHERING, message="Context ready")
            runtime.transition(RuntimePhase.IMPLEMENTING, message="Implementing")
            workbench = AgentWorkbench(runtime, max_repairs=1)

            next_action = workbench.observe(
                Observation("patch_apply", True, {"files": ["app.py"]})
            )
            self.assertEqual(runtime.state.phase, RuntimePhase.TESTING)
            self.assertEqual(next_action.action, "run_focused_tests")

            repair = workbench.observe(
                Observation("test_run", False, {"stderr": "1 failed"})
            )
            self.assertEqual(runtime.state.phase, RuntimePhase.REPAIRING)
            self.assertEqual(repair.action, "repair_failure")

            workbench.observe(Observation("patch_apply", True, {"files": ["app.py"]}))
            done = workbench.observe(Observation("test_run", True, {"scope": "full"}))
            self.assertEqual(runtime.state.phase, RuntimePhase.REVIEWING_DIFF)
            self.assertEqual(done.action, "review_diff")


class AgentComputerInterfaceTests(unittest.TestCase):
    def test_bounded_file_slice_returns_structured_observation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "sample.py").write_text("one\ntwo\nthree\nfour\n", encoding="utf-8")
            aci = AgentComputerInterface(root)

            result = aci.read_slice("sample.py", start_line=2, end_line=3)

            self.assertIsInstance(result, Observation)
            self.assertTrue(result.ok)
            self.assertEqual(result.kind, "file_slice")
            self.assertEqual(result.data["text"], "two\nthree")
            self.assertEqual(result.data["start_line"], 2)

    def test_reads_cannot_escape_repository(self):
        with tempfile.TemporaryDirectory() as tmp:
            aci = AgentComputerInterface(Path(tmp))
            result = aci.read_slice("../secret.txt")
            self.assertFalse(result.ok)
            self.assertEqual(result.error_code, "PATH_OUTSIDE_REPO")

    def test_command_observation_is_argv_only_redacted_and_bounded(self):
        calls = []

        def fake_run(command, **kwargs):
            calls.append((command, kwargs))
            return subprocess.CompletedProcess(command, 0, "ok\n", "")

        with tempfile.TemporaryDirectory() as tmp:
            aci = AgentComputerInterface(Path(tmp), run=fake_run)
            result = aci.run_command(
                ["python", "-m", "unittest"], purpose="focused tests"
            )

        self.assertTrue(result.ok)
        self.assertEqual(result.kind, "command")
        self.assertEqual(result.data["purpose"], "focused tests")
        self.assertEqual(calls[0][0], ["python", "-m", "unittest"])
        self.assertFalse(calls[0][1].get("shell", False))

    def test_destructive_command_is_not_exposed_as_normal_action(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = AgentComputerInterface(Path(tmp)).run_command(
                ["git", "reset", "--hard"], purpose="oops"
            )
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "DESTRUCTIVE_COMMAND")

    def test_destructive_command_is_detected_through_a_shell_wrapper(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = AgentComputerInterface(Path(tmp)).run_command(
                ["powershell", "-Command", "git push --force origin main"],
                purpose="unsafe",
            )
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "DESTRUCTIVE_COMMAND")


class TaskPacketTests(unittest.TestCase):
    def test_packet_contains_complete_provider_neutral_context(self):
        packet = build_task_packet(
            user_request="Fix issue #42 and open a PR",
            mode="ship",
            repo={"path": "C:/repo", "branch": "codex/fix", "remote": "origin"},
            issue={"number": 42, "title": "Broken picker"},
            dirty={"status": "unrelated", "unrelated_paths": ["notes.md"]},
            relevant_files=("vesta/gui.py",),
            constraints=("no force push",),
            allowed_actions=("edit", "test", "open_pr"),
            forbidden_actions=("force_push",),
            done_criteria=("focused and full tests pass",),
            tests=("python -m unittest",),
            last_failure={"summary": "one failure"},
            next_action="inspect gui.py",
        )
        data = packet.to_dict()
        self.assertEqual(data["issue"]["number"], 42)
        self.assertEqual(data["dirty"]["status"], "unrelated")
        self.assertIn("open_pr", data["allowed_actions"])
        self.assertEqual(data["next_action"], "inspect gui.py")


class StructuredProviderContractTests(unittest.TestCase):
    def test_account_adapter_builds_explicit_execution_request(self):
        request = ExecutionRequest(
            prompt="fix it",
            cwd="C:/repo",
            mode="safe-auto",
            model="gpt-5-codex",
            sandbox="workspace-write",
            permission="on-request",
            max_retries=2,
        )
        prepared = adapter_for("codex").prepare_execution(request)
        self.assertEqual(prepared["cwd"], "C:/repo")
        self.assertEqual(prepared["provider"], "codex")
        self.assertIn("--json", prepared["command"])
        self.assertEqual(prepared["retry"]["max_attempts"], 3)
        self.assertTrue(prepared["supports_cancel"])

    def test_provider_events_normalize_without_owning_runtime_state(self):
        event = adapter_for("claude").normalize_event(
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "done"}]},
            }
        )
        self.assertEqual(event["kind"], "provider_event")
        self.assertEqual(event["provider"], "claude")
        self.assertNotIn("workflow_phase", event)

    def test_provider_cannot_weaken_the_centralized_sandbox_contract(self):
        request = ExecutionRequest(
            prompt="fix it",
            cwd="C:/repo",
            mode="safe-auto",
            sandbox="read-only",
            permission="never",
        )
        with self.assertRaises(ValueError):
            adapter_for("codex").prepare_execution(request)


class TestRepairLoopTests(unittest.TestCase):
    def test_failure_parser_extracts_actionable_summary(self):
        failure = parse_test_failure(
            "FAILED tests/test_gui.py::GuiTests::test_status - AssertionError: nope\n"
            "1 failed, 9 passed in 1.2s"
        )
        self.assertEqual(failure.failed, 1)
        self.assertIn("tests/test_gui.py", failure.summary)

    def test_repair_loop_is_bounded_then_runs_full_suite(self):
        commands = []
        focused_results = iter([(1, "1 failed"), (0, "2 passed")])

        def run(command):
            commands.append(tuple(command))
            if command == ["full"]:
                return 0, "20 passed"
            return next(focused_results)

        repairs = []
        result = TestLoop(
            run=run, repair=lambda failure, attempt: repairs.append(attempt)
        ).execute(focused=["focused"], full=["full"], max_repairs=2)
        self.assertTrue(result.passed)
        self.assertEqual(repairs, [1])
        self.assertEqual(commands, [("focused",), ("focused",), ("full",)])

    def test_repair_loop_stops_at_configured_limit(self):
        result = TestLoop(
            run=lambda _command: (1, "FAILED tests/test_x.py::test_x"),
            repair=lambda _failure, _attempt: None,
        ).execute(focused=["focused"], full=["full"], max_repairs=2)
        self.assertFalse(result.passed)
        self.assertEqual(result.repair_attempts, 2)
        self.assertEqual(result.stage, "focused")


class WorkflowLedgerAndSafetyTests(unittest.TestCase):
    def test_workflow_ledger_redacts_secrets_and_raw_prompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = WorkflowLedger(Path(tmp), task_id="task-1")
            ledger.append(
                "command",
                task="use sk-abcdefghijklmnopqrst to fix auth",
                metadata={"stderr": "token=ghp_abcdefghijklmnopqrstuvwxyz123456"},
            )
            raw = ledger.path.read_text(encoding="utf-8")
            event = ledger.read()[0]

        self.assertNotIn("sk-abcdefghijklmnopqrst", raw)
        self.assertNotIn("ghp_abcdefghijklmnopqrstuvwxyz123456", raw)
        self.assertNotIn("use ", raw)
        self.assertEqual(event["task_id"], "task-1")
        self.assertIn("task_hash", event)

    def test_safety_gates_are_deterministic_and_fail_closed(self):
        report = evaluate_safety_gates(
            changed_files=("vesta/auth.py", ".env"),
            intended_files=("vesta/auth.py",),
            command=("git", "push", "--force"),
            diff_text="+API_KEY=sk-abcdefghijklmnopqrst",
            tests_pass=False,
            correct_branch=True,
            no_conflicts=True,
            pr_checks_pass=False,
        )
        self.assertFalse(report.can_ship)
        self.assertIn("secrets", report.failed)
        self.assertIn("risky_files", report.failed)
        self.assertIn("destructive_command", report.failed)
        self.assertIn("unrelated_diff", report.failed)
        self.assertIn("tests", report.failed)
        self.assertIn("pr_checks", report.failed)


class WorkflowTemplateTests(unittest.TestCase):
    def test_common_workflows_have_versioned_templates(self):
        templates = workflow_templates()
        self.assertEqual(
            set(templates),
            {
                "issue_triage",
                "bug_fix",
                "feature",
                "review",
                "refactor",
                "dependency_update",
                "test_repair",
                "ci_repair",
                "docs",
                "pr_preparation",
                "guarded_merge",
            },
        )
        self.assertTrue(all(item["version"] == 1 for item in templates.values()))
        self.assertTrue(all(item["steps"] for item in templates.values()))


if __name__ == "__main__":
    unittest.main()
