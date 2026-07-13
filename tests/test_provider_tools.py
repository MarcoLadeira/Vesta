import json
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

from opaihub.provider_tools import RepositoryToolExecutor
from tests._helpers import make_repo


PATCH_ONE_TO_TWO = """diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1 +1 @@
-value = 1
+value = 2
"""


class RepositoryToolExecutorTests(unittest.TestCase):
    def test_read_only_executor_does_not_expose_or_run_write_tools(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), files={"app.py": "value = 1\n"}, commit=True)
            # Pin GitHub read/write tools off so the set is deterministic
            # regardless of an ambient token/consent (e.g. CI's GITHUB_TOKEN);
            # this test is about local write tools, gated by allow_edits.
            executor = RepositoryToolExecutor(
                root,
                allow_edits=False,
                allow_github_read=False,
                allow_github_write=False,
            )

            names = [item["function"]["name"] for item in executor.schemas()]
            denied = executor.invoke("apply_patch", {"patch": PATCH_ONE_TO_TWO})

        # git_status joined the read set with the git/PR tools work; every
        # write-capable tool stays out of a read-only schema.
        self.assertEqual(
            names, ["find_files", "search_code", "read_file", "git_status"]
        )
        self.assertFalse(denied["ok"])
        self.assertEqual(denied["error_code"], "TOOL_NOT_ALLOWED")

    def test_read_and_search_paths_cannot_escape_repository(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "repo"
            root.mkdir()
            root = make_repo(root, files={"app.py": "safe\n"}, commit=True)
            (base / "secret.txt").write_text("outside\n", encoding="utf-8")
            executor = RepositoryToolExecutor(root, allow_edits=False)

            read = executor.invoke("read_file", {"path": "../secret.txt"})
            search = executor.invoke(
                "search_code", {"query": "outside", "paths": ["../secret.txt"]}
            )

        self.assertEqual(read["error_code"], "PATH_OUTSIDE_REPO")
        self.assertEqual(search["error_code"], "PATH_OUTSIDE_REPO")

    def test_editable_executor_applies_valid_patch_inside_clean_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), files={"app.py": "value = 1\n"}, commit=True)
            executor = RepositoryToolExecutor(root, allow_edits=True)

            result = executor.invoke("apply_patch", {"patch": PATCH_ONE_TO_TWO})

            self.assertTrue(result["ok"], result)
            self.assertEqual((root / "app.py").read_text(), "value = 2\n")
            self.assertEqual(result["data"]["paths"], ["app.py"])

    def test_preexisting_dirty_conflict_fails_without_overwriting_user_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), files={"app.py": "value = 1\n"}, commit=True)
            (root / "app.py").write_text("value = 99\n", encoding="utf-8")
            executor = RepositoryToolExecutor(root, allow_edits=True)

            result = executor.invoke("apply_patch", {"patch": PATCH_ONE_TO_TWO})

            self.assertFalse(result["ok"])
            self.assertEqual(result["error_code"], "DIRTY_PATH_CONFLICT")
            self.assertEqual((root / "app.py").read_text(), "value = 99\n")

    def test_unrelated_dirty_file_is_preserved_while_patch_applies(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(
                Path(tmp),
                files={"app.py": "value = 1\n", "notes.txt": "mine\n"},
                commit=True,
            )
            (root / "notes.txt").write_text("user change\n", encoding="utf-8")
            executor = RepositoryToolExecutor(root, allow_edits=True)

            result = executor.invoke("apply_patch", {"patch": PATCH_ONE_TO_TWO})

            self.assertTrue(result["ok"], result)
            self.assertEqual((root / "app.py").read_text(), "value = 2\n")
            self.assertEqual((root / "notes.txt").read_text(), "user change\n")

    def test_deleted_file_patch_and_oversized_patch_fail_closed(self):
        delete_patch = """diff --git a/app.py b/app.py
deleted file mode 100644
--- a/app.py
+++ /dev/null
@@ -1 +0,0 @@
-value = 1
"""
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), files={"app.py": "value = 1\n"}, commit=True)
            executor = RepositoryToolExecutor(
                root, allow_edits=True, max_patch_chars=len(PATCH_ONE_TO_TWO)
            )

            deleted = executor.invoke("apply_patch", {"patch": delete_patch})
            oversized = executor.invoke(
                "apply_patch", {"patch": PATCH_ONE_TO_TWO + "x"}
            )

        self.assertEqual(deleted["error_code"], "DESTRUCTIVE_PATCH")
        self.assertEqual(oversized["error_code"], "PATCH_TOO_LARGE")

    def test_malformed_tool_arguments_and_cancellation_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), files={"app.py": "value = 1\n"}, commit=True)
            executor = RepositoryToolExecutor(root, allow_edits=True)
            malformed = executor.invoke_call(
                {
                    "id": "call-1",
                    "function": {"name": "read_file", "arguments": "{bad json"},
                }
            )
            cancel = threading.Event()
            cancel.set()
            cancelled = executor.invoke(
                "apply_patch", {"patch": PATCH_ONE_TO_TWO}, cancel=cancel
            )

        self.assertEqual(malformed["error_code"], "INVALID_TOOL_ARGUMENTS")
        self.assertEqual(cancelled["error_code"], "CANCELLED")

    def test_invalid_numeric_arguments_and_absolute_globs_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), files={"app.py": "value = 1\n"}, commit=True)
            executor = RepositoryToolExecutor(root, allow_edits=False)

            invalid_limit = executor.invoke("find_files", {"limit": "many"})
            invalid_line = executor.invoke(
                "read_file", {"path": "app.py", "start_line": "first"}
            )
            absolute_glob = executor.invoke("find_files", {"pattern": str(root / "*")})

        self.assertEqual(invalid_limit["error_code"], "INVALID_TOOL_ARGUMENTS")
        self.assertEqual(invalid_line["error_code"], "INVALID_TOOL_ARGUMENTS")
        self.assertEqual(absolute_glob["error_code"], "INVALID_TOOL_ARGUMENTS")

    def test_test_tool_accepts_only_detected_fixed_command_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(
                Path(tmp),
                files={
                    "tests/test_ok.py": (
                        "import unittest\n\n"
                        "class T(unittest.TestCase):\n"
                        "    def test_ok(self):\n"
                        "        self.assertTrue(True)\n"
                    )
                },
                commit=True,
            )
            executor = RepositoryToolExecutor(root, allow_edits=True)

            denied = executor.invoke(
                "run_tests", {"command_id": "python -c import os; os.remove('x')"}
            )
            passed = executor.invoke(
                "run_tests", {"command_id": "python-unittest", "scope": "unit"}
            )

        self.assertEqual(denied["error_code"], "TEST_COMMAND_NOT_ALLOWED")
        self.assertTrue(passed["ok"], json.dumps(passed, indent=2))
        self.assertEqual(passed["data"]["returncode"], 0)

    def test_patch_application_uses_argv_not_a_shell(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), files={"app.py": "value = 1\n"}, commit=True)
            executor = RepositoryToolExecutor(root, allow_edits=True)

            result = executor.invoke("apply_patch", {"patch": PATCH_ONE_TO_TWO})
            status = subprocess.run(
                ["git", "diff", "--", "app.py"],
                cwd=root,
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertTrue(result["ok"])
        self.assertIn("+value = 2", status.stdout)


if __name__ == "__main__":
    unittest.main()
