"""write_file + git tools in the provider tool loop (fixes "can't touch code").

The capability contract used to advertise ``create_files`` while the tool
schema offered only ``apply_patch`` — small models refused to edit. These
tests pin the fix: a reliable whole-file ``write_file`` tool, real git tools,
consent-gated push/PR, and a contract that names the actual tools.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from opaihub.agent_policy import build_capability_contract, resolve_agent_policy
from opaihub.provider_tools import (
    GIT_OPS_TOOLS,
    RepositoryToolExecutor,
    available_tool_names,
)

from tests._helpers import make_repo


def _executor(root: Path, *, allow_edits=True, allow_git_ops=False):
    return RepositoryToolExecutor(
        root, allow_edits=allow_edits, allow_git_ops=allow_git_ops
    )


class WriteFileTests(unittest.TestCase):
    def test_creates_a_new_file_with_parents(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), commit=True)
            result = _executor(root).invoke(
                "write_file", {"path": "src/new/module.js", "content": "export {};\n"}
            )
            self.assertTrue(result["ok"], result)
            self.assertTrue(result["data"]["created"])
            self.assertEqual(
                (root / "src/new/module.js").read_text(encoding="utf-8"),
                "export {};\n",
            )

    def test_replaces_a_file_it_already_wrote(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), commit=True)
            executor = _executor(root)
            executor.invoke("write_file", {"path": "a.txt", "content": "one"})
            result = executor.invoke("write_file", {"path": "a.txt", "content": "two"})
            self.assertTrue(result["ok"])
            self.assertFalse(result["data"]["created"])
            self.assertEqual((root / "a.txt").read_text(encoding="utf-8"), "two")
            # Tracked once, not twice.
            self.assertEqual(executor.written_paths.count("a.txt"), 1)

    def test_refuses_paths_outside_the_repository(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), commit=True)
            result = _executor(root).invoke(
                "write_file", {"path": "../escape.txt", "content": "x"}
            )
            self.assertFalse(result["ok"])
            self.assertEqual(result["error_code"], "PATH_OUTSIDE_REPO")

    def test_refuses_protected_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), commit=True)
            executor = _executor(root)
            for path in (".env", ".git/config", ".opaihub/state.json", "id_rsa"):
                result = executor.invoke("write_file", {"path": path, "content": "x"})
                self.assertFalse(result["ok"], path)
                self.assertIn(
                    result["error_code"], {"PATH_BLOCKED", "PATH_OUTSIDE_REPO"}, path
                )

    def test_refuses_overwriting_preexisting_user_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), files={"work.py": "v1"}, commit=True)
            (root / "work.py").write_text("user edit in progress", encoding="utf-8")
            result = _executor(root).invoke(
                "write_file", {"path": "work.py", "content": "model version"}
            )
            self.assertFalse(result["ok"])
            self.assertEqual(result["error_code"], "DIRTY_PATH_CONFLICT")
            self.assertEqual(
                (root / "work.py").read_text(encoding="utf-8"),
                "user edit in progress",
            )

    def test_requires_edit_permission(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), commit=True)
            result = _executor(root, allow_edits=False).invoke(
                "write_file", {"path": "a.txt", "content": "x"}
            )
            self.assertFalse(result["ok"])
            self.assertEqual(result["error_code"], "TOOL_NOT_ALLOWED")


class GitToolTests(unittest.TestCase):
    def test_branch_commit_flow(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), commit=True)
            executor = _executor(root)
            branch = executor.invoke("git_create_branch", {"name": "feat/x"})
            self.assertTrue(branch["ok"], branch)
            executor.invoke("write_file", {"path": "f.txt", "content": "hello"})
            committed = executor.invoke("git_commit", {"message": "Add f.txt"})
            self.assertTrue(committed["ok"], committed)
            status = executor.invoke("git_status", {})
            self.assertTrue(status["ok"])

    def test_commit_with_nothing_written_is_an_honest_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), commit=True)
            result = _executor(root).invoke("git_commit", {"message": "empty"})
            self.assertFalse(result["ok"])
            self.assertEqual(result["error_code"], "NOTHING_TO_COMMIT")

    def test_invalid_branch_names_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), commit=True)
            executor = _executor(root)
            for name in ("-bad", "a..b", "a b", "x/", "a@{b}", ""):
                result = executor.invoke("git_create_branch", {"name": name})
                self.assertFalse(result["ok"], name)

    def test_push_and_pr_are_gated_behind_git_ops(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), commit=True)
            executor = _executor(root, allow_git_ops=False)
            for name in GIT_OPS_TOOLS:
                result = executor.invoke(name, {"title": "t"})
                self.assertFalse(result["ok"], name)
                self.assertEqual(result["error_code"], "TOOL_NOT_ALLOWED")

    def test_git_ops_require_edits_too(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), commit=True)
            executor = _executor(root, allow_edits=False, allow_git_ops=True)
            self.assertFalse(executor.allow_git_ops)

    def test_open_pr_uses_the_connector(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), commit=True)
            executor = _executor(root, allow_git_ops=True)
            executor.invoke("git_create_branch", {"name": "feat/pr"})
            with mock.patch(
                "opaihub.github_connector.create_pull_request",
                return_value={"ok": True, "url": "https://github.com/o/r/pull/1"},
            ) as fake:
                result = executor.invoke("open_pr", {"title": "Add feature"})
            self.assertTrue(result["ok"], result)
            self.assertIn("/pull/1", result["data"]["url"])
            self.assertEqual(fake.call_args.kwargs["head"], "feat/pr")


class GithubReadToolTests(unittest.TestCase):
    """Read-only GitHub tools appear with a token and route to the connector."""

    def test_read_tools_appear_with_a_token_even_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), commit=True)
            with_token = available_tool_names(
                root, allow_edits=False, allow_github_read=True
            )
            without = available_tool_names(
                root, allow_edits=False, allow_github_read=False
            )
        self.assertIn("github_pr_status", with_token)
        self.assertIn("github_get_issue", with_token)
        # No token -> no GitHub read tools, even though other read tools remain.
        self.assertNotIn("github_pr_status", without)
        self.assertIn("git_status", without)

    def test_pr_status_tool_routes_to_the_connector(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), commit=True)
            executor = RepositoryToolExecutor(
                root, allow_edits=False, allow_github_read=True
            )
            with mock.patch(
                "opaihub.github_connector.pull_request_status",
                return_value={"ok": True, "number": 7, "state": "open", "checks": {}},
            ) as fake:
                result = executor.invoke("github_pr_status", {"number": 7})
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["data"]["state"], "open")
        self.assertEqual(fake.call_args.args[1], 7)

    def test_read_tool_rejects_a_bad_number(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), commit=True)
            executor = RepositoryToolExecutor(
                root, allow_edits=False, allow_github_read=True
            )
            result = executor.invoke("github_get_issue", {"number": "abc"})
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "INVALID_TOOL_ARGUMENTS")

    def test_read_tool_blocked_without_a_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), commit=True)
            executor = RepositoryToolExecutor(
                root, allow_edits=True, allow_github_read=False
            )
            result = executor.invoke("github_pr_status", {"number": 1})
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "TOOL_NOT_ALLOWED")

    def test_write_tools_need_consent_and_route_to_the_connector(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), commit=True)
            # Consent off -> outward GitHub write tools are not even offered.
            no_consent = available_tool_names(
                root, allow_edits=True, allow_github_write=False
            )
            self.assertNotIn("github_comment", no_consent)

            executor = RepositoryToolExecutor(
                root, allow_edits=False, allow_github_write=True
            )
            self.assertIn(
                "github_comment", {s["function"]["name"] for s in executor.schemas()}
            )
            with mock.patch(
                "opaihub.github_connector.add_comment",
                return_value={"ok": True, "url": "https://github.com/o/r/issues/5#c1"},
            ) as fake:
                result = executor.invoke(
                    "github_comment", {"number": 5, "body": "looks good"}
                )
            self.assertTrue(result["ok"], result)
            self.assertEqual(fake.call_args.args[1], 5)

    def test_request_review_routes_and_validates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), commit=True)
            executor = RepositoryToolExecutor(
                root, allow_edits=False, allow_github_write=True
            )
            empty = executor.invoke(
                "github_request_review", {"number": 7, "reviewers": []}
            )
            self.assertEqual(empty["error_code"], "INVALID_TOOL_ARGUMENTS")
            with mock.patch(
                "opaihub.github_connector.request_reviewers",
                return_value={"ok": True, "requested": ["alice"]},
            ) as fake:
                ok = executor.invoke(
                    "github_request_review", {"number": 7, "reviewers": ["alice"]}
                )
            self.assertTrue(ok["ok"], ok)
            self.assertEqual(fake.call_args.args[2], ["alice"])


class SchemaAndContractTests(unittest.TestCase):
    def test_schema_names_follow_permissions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), commit=True)
            read_only = available_tool_names(root, allow_edits=False)
            edits = available_tool_names(root, allow_edits=True, allow_git_ops=False)
            full = available_tool_names(root, allow_edits=True, allow_git_ops=True)
        self.assertIn("git_status", read_only)
        self.assertNotIn("write_file", read_only)
        self.assertIn("write_file", edits)
        self.assertIn("git_commit", edits)
        self.assertNotIn("git_push", edits)
        self.assertIn("git_push", full)
        self.assertIn("open_pr", full)

    def test_contract_names_the_real_tools(self):
        policy = resolve_agent_policy("Implement the new sync feature")
        contract = build_capability_contract(
            policy,
            active_repo="repo",
            tool_names=("read_file", "write_file", "apply_patch", "git_commit"),
        )
        self.assertIn("Callable tools this turn:", contract)
        self.assertIn("write_file", contract)
        self.assertIn("do not claim edits are not permitted", contract)
        # Push disabled -> the model is told the BOTH gates (token + consent),
        # not just to re-run allow-push, and pointed at `opai github status`.
        self.assertIn("allow-push on", contract)
        self.assertIn("opai github connect", contract)
        self.assertIn("opai github status", contract)

    def test_contract_announces_pr_ability_when_enabled(self):
        policy = resolve_agent_policy("Implement the new sync feature and open a PR")
        contract = build_capability_contract(
            policy,
            active_repo="repo",
            tool_names=("write_file", "git_commit", "git_push", "open_pr"),
        )
        self.assertIn("open a pull request", contract)

    def test_contract_without_tools_is_unchanged_shape(self):
        policy = resolve_agent_policy("Explain how routing works")
        contract = build_capability_contract(policy, active_repo="repo")
        self.assertIn("read-only", contract)
        self.assertNotIn("Callable tools", contract)


if __name__ == "__main__":
    unittest.main()
