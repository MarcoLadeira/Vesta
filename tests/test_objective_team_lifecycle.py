from __future__ import annotations

import json
from pathlib import Path
import tempfile
import threading
import sys
import unittest
from unittest import mock

from _helpers import make_repo
from opai.agents_bridge import create_objective_payload, control_objective_payload
from opaihub.agent_objectives import ObjectiveStore
from opaihub.objective_execution import ObjectiveExecutor


class ObjectiveTeamLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "repo"
        self.root.mkdir()
        make_repo(
            self.root,
            files={
                "README.md": "Original documentation\n",
                ".gitignore": ".opaihub/\n",
            },
            commit=True,
        )
        self.store = ObjectiveStore(self.root)

    def create(self):
        return create_objective_payload(
            self.root,
            {"text": "Explain the documentation", "mode": "plan", "model": "auto"},
        )

    def executor(self, worker):
        from opaihub.objective_capacity import host_slot

        slots = self.root.parent / "slots"
        self.enterContext(
            mock.patch(
                "opaihub.objective_execution.host_slot",
                side_effect=lambda cancel: host_slot(cancel, directory=slots),
            )
        )
        return ObjectiveExecutor(
            self.root,
            worker=worker,
            worktree_root=self.root.parent / "worktrees",
        )

    def subprocess_worker(self, packet, cancel, activity):
        from opaihub.objective_execution import run_worker_process
        from opaihub.state import state_dir

        directory = state_dir(self.root) / "objectives" / "workers" / packet["run_id"]
        return run_worker_process(
            packet,
            directory,
            cancel,
            activity,
            argv=[
                sys.executable,
                str(Path(__file__).with_name("objective_provider_fixture.py")),
                str(directory / "request.json"),
                str(directory / "response.json"),
            ],
        )

    def executable_objective(self):
        policy = {
            "schema_version": 1,
            "checks": [
                {
                    "id": "static_analysis",
                    "kind": "static_analysis",
                    "requirement": "required",
                    "reason": "Verify the requested documentation edit",
                    "command": [
                        sys.executable,
                        "-c",
                        "from pathlib import Path; assert Path('README.md').read_text() == 'Updated documentation\\n'",
                    ],
                }
            ],
        }
        (self.root / "opai-verification-policy.yaml").write_text(
            json.dumps(policy), encoding="utf-8"
        )
        from test_objective_execution import git

        git(self.root, "add", "opai-verification-policy.yaml")
        git(self.root, "commit", "-m", "Set documentation verification")
        return create_objective_payload(
            self.root,
            {
                "text": "Update README documentation",
                "mode": "safe-auto",
                "model": "auto",
                "allowCloud": True,
                "maxParallel": 1,
            },
        )

    def assert_executed(self, objective):
        self.assertEqual(objective["status"], "completed", objective)
        self.assertEqual(objective["assignments"][0]["status"], "completed")
        self.assertEqual(
            objective["assignments"][0]["result"]["routing"]["model_id"],
            "account:codex:test",
        )
        self.assertEqual(
            objective["assignments"][0]["result"]["git_evidence"]["changed_files"],
            ["README.md"],
        )
        self.assertEqual(
            Path(objective["integration"]["worktree"], "README.md").read_text(),
            "Updated documentation\n",
        )
        self.assertEqual(
            (self.root / "README.md").read_text(), "Original documentation\n"
        )
        self.assertTrue(
            objective["assignments"][0]["execution"]["termination_proof"][
                "tree_terminated"
            ]
        )
        self.assertFalse(objective["bypass_permissions"])

    def test_auto_team_plans_and_executes_through_real_guardian_and_verification(self):
        objective = self.executable_objective()
        result = self.executor(self.subprocess_worker).run(objective["objective_id"])
        self.assertEqual(result["planning"]["status"], "completed", result["planning"])
        self.assertTrue(
            result["planning"]["execution"]["termination_proof"]["tree_terminated"]
        )
        self.assert_executed(result)

    def test_manual_first_agent_executes_through_real_guardian_and_verification(self):
        objective = self.executable_objective()
        result = control_objective_payload(
            self.root,
            {
                "objective_id": objective["objective_id"],
                "action": "add_agent",
                "value": {
                    "revision": objective["team_revision"],
                    "objective": "Update README",
                },
            },
        )["objective"]
        self.assertFalse(result["assignments"][0]["held"])
        result = self.executor(self.subprocess_worker).run(objective["objective_id"])
        self.assert_executed(result)

    def test_planner_admission_failure_retains_the_actionable_reason(self):
        from opaihub import objective_worker
        from opaihub.state import state_dir

        self.enterContext(
            mock.patch("opai.app_state.available_models", return_value={"models": []})
        )
        self.enterContext(
            mock.patch(
                "opaihub.objective_routing.provider_usage.usage_overview",
                return_value=[],
            )
        )

        def worker(packet, cancel, activity):
            directory = (
                state_dir(self.root) / "objectives" / "workers" / packet["run_id"]
            )
            directory.mkdir(parents=True)
            request, response = directory / "request.json", directory / "response.json"
            request.write_text(json.dumps(packet), encoding="utf-8")
            objective_worker.main([str(request), str(response)])
            return json.loads(response.read_text(encoding="utf-8"))

        objective = self.create()
        result = self.executor(worker).run(objective["objective_id"])
        self.assertEqual(result["status"], "needs-attention")
        self.assertFalse(result["planning"]["owner"])
        self.assertFalse(result["assignments"])
        self.assertIn("No eligible model", result["planning"]["result"]["error"])
        self.assertEqual(
            result["planning"]["result"]["dispatch_state"], "not-dispatched"
        )
        self.assertFalse(result["planning"]["result"]["routing"]["allowed"])
        self.assertEqual(result["cost_usd"], "0")
        self.assertTrue(result["cost_complete"])

    def test_failed_planner_preserves_provider_error_instead_of_generic_failure(self):
        objective = self.create()
        result = self.executor(
            lambda packet, cancel, activity: {
                "status": "blocked",
                "dispatch_state": "not-dispatched",
                "error": "Cloud context transmission requires authorization",
                "routing": {"allowed": False, "blockers": []},
            }
        ).run(objective["objective_id"])
        detail = result["planning"]["result"]
        self.assertIn("requires authorization", detail["error"])
        self.assertEqual(detail["dispatch_state"], "not-dispatched")
        self.assertFalse(detail["routing"]["allowed"])

    def test_stalled_planner_cancels_and_releases_owner_with_timeout_evidence(self):
        observed_cancel = threading.Event()
        snapshots = []

        def worker(packet, cancel, activity):
            if cancel.wait(0.8):
                observed_cancel.set()
            return {"status": "cancelled"}

        objective = self.create()
        executor = self.executor(worker)
        executor.on_event = snapshots.append
        lease = executor._lease(
            objective,
            {
                "assignment_id": None,
                "task_id": objective["task_id"],
                "run_id": objective["run_id"] + "-plan",
            },
        )
        with (
            mock.patch("opaihub.objective_execution.PLANNING_TIMEOUT_SECONDS", 0.05),
            mock.patch.object(executor, "_lease", return_value=lease),
        ):
            result = executor.plan(objective["objective_id"])
        self.assertTrue(observed_cancel.is_set())
        self.assertEqual(result["status"], "needs-attention")
        self.assertFalse(result["planning"]["owner"])
        self.assertIn("timed out", result["planning"]["result"]["error"])
        self.assertEqual(result["planning"]["result"]["completion_state"], "timeout")
        self.assertTrue(
            any(row["planning"]["status"] == "running" for row in snapshots)
        )
        self.assertTrue(
            any(
                row["status"] == "needs-attention" and row["planning"]["owner"]
                for row in snapshots
            )
        )

    def test_timeout_during_worktree_startup_remains_timeout_not_user_cancellation(
        self,
    ):
        objective = self.create()
        executor = self.executor(
            lambda *args: self.fail("Timed-out planning must not dispatch")
        )
        timed_out = threading.Event()
        acquire_lease = executor._lease

        def notify(snapshot):
            if (
                snapshot["planning"].get("result", {}).get("completion_state")
                == "timeout"
            ):
                timed_out.set()

        def slow_lease(*args):
            lease = acquire_lease(*args)
            self.assertTrue(timed_out.wait(5), "Planning deadline was not reported")
            return lease

        executor.on_event = notify
        with (
            mock.patch("opaihub.objective_execution.PLANNING_TIMEOUT_SECONDS", 0.01),
            mock.patch.object(executor, "_lease", side_effect=slow_lease),
        ):
            result = executor.plan(objective["objective_id"])
        self.assertEqual(result["status"], "needs-attention")
        self.assertFalse(result["planning"]["owner"])
        self.assertEqual(
            result["planning"]["result"].get("completion_state"), "timeout"
        )

    def test_first_manual_agent_can_be_added_after_failed_planning_and_started(self):
        objective = self.create()
        result = self.executor(
            lambda packet, cancel, activity: {
                "status": "failed",
                "error": "Planner unavailable",
            }
        ).run(objective["objective_id"])
        self.assertTrue(result["team_controls"]["can_add"])
        result = control_objective_payload(
            self.root,
            {
                "objective_id": objective["objective_id"],
                "action": "add_agent",
                "value": {
                    "revision": result["team_revision"],
                    "objective": "Read README",
                    "start": False,
                },
            },
        )["objective"]
        assignment = result["assignments"][0]
        self.assertTrue(assignment["held"])
        self.assertIsNone(self.store.claim_next(objective["objective_id"], "worker"))
        result = control_objective_payload(
            self.root,
            {
                "objective_id": objective["objective_id"],
                "assignment_id": assignment["assignment_id"],
                "action": "start_agent",
                "value": {"revision": result["team_revision"]},
            },
        )["objective"]
        claimed = self.store.claim_next(objective["objective_id"], "worker")
        self.assertEqual(claimed["assignment_id"], assignment["assignment_id"])
        self.assertFalse(result["allow_cloud"])
        self.assertFalse(result["bypass_permissions"])

    def test_first_manual_agent_does_not_race_an_active_planner(self):
        objective = self.create()
        self.store.begin_plan(objective["objective_id"], "planner")
        result = self.store.snapshot(objective["objective_id"])
        self.assertFalse(result["team_controls"]["can_add"])
        with self.assertRaises(ValueError):
            control_objective_payload(
                self.root,
                {
                    "objective_id": objective["objective_id"],
                    "action": "add_agent",
                    "value": {
                        "revision": result["team_revision"],
                        "objective": "Read README",
                    },
                },
            )
        self.assertFalse(self.store.snapshot(objective["objective_id"])["assignments"])

    def test_unconfirmed_planner_termination_keeps_ownership_and_explains_failure(self):
        from opaihub.objective_execution import UnconfirmedTerminationError

        objective = self.create()
        executor = self.executor(
            mock.Mock(
                side_effect=UnconfirmedTerminationError(
                    "Worker tree termination is unconfirmed"
                )
            )
        )
        result = executor.run(objective["objective_id"])
        self.assertEqual(result["status"], "needs-attention")
        self.assertTrue(result["planning"]["owner"])
        self.assertFalse(result["team_controls"]["can_add"])
        self.assertIn(
            "termination is unconfirmed", result["planning"]["result"]["error"]
        )

    def test_failed_start_notification_does_not_leave_a_planner_owner(self):
        objective = self.create()
        executor = self.executor(
            mock.Mock(side_effect=AssertionError("No dispatch expected"))
        )

        def notify(snapshot):
            if snapshot["planning"]["status"] == "running":
                raise RuntimeError("Team view closed")

        executor.on_event = notify
        result = executor.run(objective["objective_id"])
        self.assertEqual(result["status"], "needs-attention")
        self.assertFalse(result["planning"]["owner"])
        self.assertIn("Team view closed", result["planning"]["result"]["error"])


if __name__ == "__main__":
    unittest.main()
