"""The executor's ``run_command`` gate, per autonomy level.

Nothing here executes a real command: the ACI and the granted-command path are
replaced with recorders, so these assert *decisions* only.

The regression being pinned is the one from the bug report -- a wall of
"COMMAND BLOCKED" for ordinary read-only work. ``run_command`` used to accept
only five allowlisted git subcommands and hard-refuse everything else, ignoring
the command-policy store's own ``allow`` verdict.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from vestahub.provider_tools import RepositoryToolExecutor

PUSH = "git push -u origin feature"
FORCE_PUSH = "git push --force origin main"
MERGE_PR = "gh pr merge 42 --squash"


class _RecordingACI:
    """Stands in for the real ACI; records instead of running."""

    def __init__(self) -> None:
        self.argv_calls: list[list[str]] = []
        self.shell_calls: list[str] = []

    def run_command(self, argv: Any, **kwargs: Any) -> Any:
        self.argv_calls.append([str(item) for item in argv])
        return _Ran(" ".join(str(item) for item in argv))

    def run_shell(self, command: str, **kwargs: Any) -> Any:
        self.shell_calls.append(str(command))
        return _Ran(str(command))


class _Ran:
    def __init__(self, command: str) -> None:
        self.command = command

    def to_dict(self) -> dict[str, Any]:
        return {"ok": True, "tool": "command", "data": {"command": self.command}}


class RunCommandGateTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def executor(self, autonomy: str) -> RepositoryToolExecutor:
        executor = RepositoryToolExecutor(
            self.root, allow_edits=True, autonomy=autonomy
        )
        executor.aci = _RecordingACI()
        self.granted: list[list[str]] = []

        def _granted(argv: Any, arguments: Any, **kwargs: Any) -> dict[str, Any]:
            self.granted.append([str(item) for item in argv])
            return {"ok": True, "tool": "command"}

        executor._run_granted_command = _granted  # type: ignore[assignment]
        return executor

    def run_one(self, executor: RepositoryToolExecutor, command: str) -> dict[str, Any]:
        return executor._run_command({"command": command})


class ReadOnlyCommandsAreNeverBlockedTests(RunCommandGateTestCase):
    """The core of the bug report: reads must just run, in every mode."""

    def test_reads_run_in_every_mode(self) -> None:
        for autonomy in ("safe-auto", "approve-edits", "full-auto"):
            for command in (
                "cat package.json",
                "ls -la",
                "grep -rn needle src",
                "git status --short",
                "git merge-tree --write-tree main HEAD",
                "gh pr view 511",
            ):
                with self.subTest(autonomy=autonomy, command=command):
                    result = self.run_one(self.executor(autonomy), command)
                    self.assertNotEqual(
                        result.get("error_code"), "COMMAND_BLOCKED", command
                    )
                    self.assertNotEqual(
                        result.get("error_code"), "COMMAND_NEEDS_APPROVAL", command
                    )

    def test_a_read_only_pipeline_runs_through_the_shell(self) -> None:
        # Previously impossible: any shell operator was refused outright, so
        # `cat x | head -40` could never run.
        executor = self.executor("safe-auto")
        result = self.run_one(executor, "cat package.json 2>/dev/null | head -40")
        self.assertNotEqual(result.get("error_code"), "COMMAND_BLOCKED")
        self.assertEqual(
            executor.aci.shell_calls, ["cat package.json 2>/dev/null | head -40"]
        )


class LocalWriteTests(RunCommandGateTestCase):
    def test_commit_asks_in_normal_but_runs_under_auto_levels(self) -> None:
        # git commit is local and undoable; risky_commands.yaml has said so in a
        # comment for a long time, while the executor refused it anyway.
        asked = self.run_one(self.executor("safe-auto"), "git commit -m x")
        self.assertEqual(asked.get("error_code"), "COMMAND_NEEDS_APPROVAL")

        executor = self.executor("full-auto")
        ran = self.run_one(executor, "git commit -m x")
        self.assertIsNone(ran.get("error_code"))
        self.assertEqual(self.granted, [["git", "commit", "-m", "x"]])

    def test_test_runners_are_not_blocked(self) -> None:
        executor = self.executor("full-auto")
        result = self.run_one(executor, "python -m pytest tests -q")
        self.assertIsNone(result.get("error_code"))


class BypassRunsOutwardFacingWorkTests(RunCommandGateTestCase):
    """Full Auto maps to bypass: the user asked for no prompts."""

    def test_push_and_merge_run_without_approval(self) -> None:
        for command in (PUSH, MERGE_PR, FORCE_PUSH):
            with self.subTest(command=command):
                executor = self.executor("full-auto")
                result = self.run_one(executor, command)
                self.assertIsNone(result.get("error_code"), command)
                self.assertEqual(len(self.granted), 1)

    def test_lower_levels_still_ask_for_the_same_commands(self) -> None:
        for autonomy in ("safe-auto", "approve-edits"):
            for command in (PUSH, MERGE_PR):
                with self.subTest(autonomy=autonomy, command=command):
                    result = self.run_one(self.executor(autonomy), command)
                    self.assertEqual(
                        result.get("error_code"), "COMMAND_NEEDS_APPROVAL", command
                    )

    def test_a_one_shot_grant_still_satisfies_an_ask(self) -> None:
        executor = RepositoryToolExecutor(
            self.root, allow_edits=True, autonomy="safe-auto", allow_command=PUSH
        )
        executor.aci = _RecordingACI()
        recorded: list[list[str]] = []
        executor._run_granted_command = (  # type: ignore[assignment]
            lambda argv, arguments, **kw: recorded.append(list(argv)) or {"ok": True}
        )
        result = executor._run_command({"command": PUSH})
        self.assertIsNone(result.get("error_code"))
        self.assertEqual(len(recorded), 1)


class ReadOnlyModesStillRefuseWritesTests(RunCommandGateTestCase):
    def test_plan_and_ask_block_changes_but_allow_reads(self) -> None:
        for autonomy in ("ask", "plan"):
            with self.subTest(autonomy=autonomy):
                executor = self.executor(autonomy)
                self.assertIsNone(
                    self.run_one(executor, "git status").get("error_code")
                )
                blocked = self.run_one(self.executor(autonomy), "git commit -m x")
                self.assertEqual(blocked.get("error_code"), "COMMAND_BLOCKED")


if __name__ == "__main__":
    unittest.main()
