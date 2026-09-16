"""Repository-safety parity across the CLI and desktop workspace payload."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from _helpers import make_repo

from vesta.cli import main
from vesta.gui_web import _workspace
from vestahub.repository_safety import capture_repository_handle
from vestahub.worktree_leases import WorktreeManager


class RepositorySafetySurfaceTests(unittest.TestCase):
    def _cli(self, *args: str) -> dict[str, object]:
        output = io.StringIO()
        with redirect_stdout(output):
            code = main(list(args))
        self.assertEqual(code, 0, output.getvalue())
        return json.loads(output.getvalue())

    def test_cli_and_gui_expose_matching_redacted_identity_assessment_and_receipt(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), files={"src/app.py": "ok\n"}, commit=True)
            import subprocess

            subprocess.run(
                [
                    "git",
                    "remote",
                    "add",
                    "origin",
                    "https://secret-token@github.com/acme/demo.git",
                ],
                cwd=root,
                check=True,
                capture_output=True,
            )

            cli = self._cli("repo", "inspect", "--project", str(root), "--json")
            gui = _workspace(root)

        cli_safety = cli["repository_safety"]
        gui_safety = gui["repository_safety"]
        self.assertEqual(
            cli_safety["identity"]["repository_id"],
            gui_safety["identity"]["repository_id"],
        )
        self.assertEqual(cli_safety["assessment"], gui_safety["assessment"])
        self.assertEqual(cli_safety["receipt"], gui_safety["receipt"])
        self.assertNotIn("secret-token", json.dumps({"cli": cli, "gui": gui}))

    def test_cli_recovery_reports_a_modified_lease_without_cleaning_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "repo"
            root.mkdir()
            make_repo(root, files={"src/app.py": "ok\n"}, commit=True)
            handle = capture_repository_handle(root, task_id="task-a", run_id="run-a")
            manager = WorktreeManager(root, min_free_bytes=0)
            lease = manager.create(
                handle,
                task_id="task-a",
                run_id="run-a",
                owner="worker-a",
                branch="codex/task-a",
                target=base / "task-a",
                base="HEAD",
                planned_paths=("src/",),
            )
            user_note = Path(lease.path) / "user-note.txt"
            user_note.write_text("preserve\n", encoding="utf-8")

            payload = self._cli(
                "repo", "worktrees", "--project", str(root), "--recover", "--json"
            )

            self.assertEqual(payload["leases"][0]["state"], "needs_review")
            self.assertTrue(user_note.exists())
            self.assertIn("inspect", payload["recovery"][0]["recommended_actions"])

    def test_edit_capable_gui_turn_persists_and_threads_its_task_handle(self) -> None:
        from vestahub.gui_pipeline import handle_gui_message

        selected = "free:gemini:gemini-3.1-flash-lite"
        provider_result = {
            "status": "answered_by_free_api",
            "answer": "Implemented.",
            "model_id": selected,
            "tool_trace": [{"tool": "write_file", "ok": True}],
            "changed_files": ["src/app.py"],
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), files={"src/app.py": "old\n"}, commit=True)
            with mock.patch("vesta.app_state.ask", return_value=provider_result) as ask:
                result = handle_gui_message(
                    root,
                    "Update src/app.py.",
                    model_id=selected,
                    mode="safe-auto",
                    allow_cloud=True,
                )

            handle = ask.call_args.kwargs["repository_handle"]
            persisted = (
                root
                / ".vestahub"
                / "repository"
                / "handles"
                / f"{handle.handle_id}.json"
            )
            self.assertTrue(persisted.is_file())
            self.assertEqual(
                result["task_packet"]["repo"]["handle_id"], handle.handle_id
            )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
