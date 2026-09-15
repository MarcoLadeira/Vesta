"""write_file + git tools in the provider tool loop (fixes "can't touch code").

The capability contract used to advertise ``create_files`` while the tool
schema offered only ``apply_patch`` — small models refused to edit. These
tests pin the fix: a reliable whole-file ``write_file`` tool, real git tools,
consent-gated push/PR, and a contract that names the actual tools.
"""

from __future__ import annotations

import subprocess
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


def _executor(root: Path, *, allow_edits=True, allow_git_ops=False, allow_command=None):
    return RepositoryToolExecutor(
        root,
        allow_edits=allow_edits,
        allow_git_ops=allow_git_ops,
        allow_command=allow_command,
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

    def test_commit_blocks_when_the_index_changes_after_a_provider_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), commit=True)
            executor = _executor(root)
            self.assertTrue(
                executor.invoke("write_file", {"path": "f.txt", "content": "hello"})[
                    "ok"
                ]
            )
            (root / "user.txt").write_text("outside change\n", encoding="utf-8")
            subprocess.run(
                ["git", "add", "--", "user.txt"],
                cwd=root,
                check=True,
                capture_output=True,
            )

            result = executor.invoke("git_commit", {"message": "Add f.txt"})

            self.assertFalse(result["ok"], result)
            self.assertEqual(result["error_code"], "REPOSITORY_SAFETY_BLOCKED")
            head = subprocess.run(
                ["git", "log", "-1", "--format=%s"],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertEqual(head.stdout.strip(), "init")

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
            executor = _executor(
                root,
                allow_git_ops=True,
                allow_command="gh pr create --head feat/pr --base main",
            )
            executor.invoke("git_create_branch", {"name": "feat/pr"})
            with mock.patch(
                "opaihub.github_connector.create_pull_request",
                return_value={"ok": True, "url": "https://github.com/o/r/pull/1"},
            ) as fake:
                result = executor.invoke("open_pr", {"title": "Add feature"})
            self.assertTrue(result["ok"], result)
            self.assertIn("/pull/1", result["data"]["url"])
            self.assertEqual(fake.call_args.kwargs["head"], "feat/pr")


class OutwardActionApprovalTests(unittest.TestCase):
    """Round 5 finding 1: consent enables pushing; approval authorizes THIS push.

    Settings consent (``allow_git_ops``) decides whether these tools exist at
    all. Before this round, existing was the whole gate — so Full Auto ran a push
    with no confirmation UI while its own dialog promised one. Each outward action
    now needs the user's one-shot approval as well, delivered through the same
    ``COMMAND_NEEDS_APPROVAL`` -> approval-card flow blocked commands already use.
    """

    def test_push_asks_before_it_runs_and_never_touches_git(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), commit=True)
            executor = _executor(root, allow_git_ops=True)
            executor.invoke("git_create_branch", {"name": "feat/push"})
            with mock.patch.object(executor, "_git") as git:
                result = executor.invoke("git_push", {"branch": "feat/push"})
            self.assertFalse(result["ok"], result)
            self.assertEqual(result["error_code"], "COMMAND_NEEDS_APPROVAL")
            self.assertEqual(result["command"], "git push -u origin feat/push")
            self.assertIn("remote", result["approval_reason"])
            # The refusal must come before any git invocation, not after.
            git.assert_not_called()

    def test_an_approved_push_runs_exactly_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), commit=True)
            executor = _executor(
                root,
                allow_git_ops=True,
                allow_command="git push -u origin feat/push",
            )
            executor.invoke("git_create_branch", {"name": "feat/push"})
            with mock.patch.object(
                executor, "_git", return_value={"ok": True, "output": ""}
            ) as git:
                first = executor.invoke("git_push", {"branch": "feat/push"})
                second = executor.invoke("git_push", {"branch": "feat/push"})
            self.assertTrue(first["ok"], first)
            self.assertFalse(second["ok"])
            self.assertEqual(second["error_code"], "COMMAND_NEEDS_APPROVAL")
            pushes = [c for c in git.call_args_list if c.args[0][0] == "push"]
            self.assertEqual(len(pushes), 1)

    def test_github_writes_ask_too(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), commit=True)
            executor = RepositoryToolExecutor(
                root, allow_edits=False, allow_github_write=True
            )
            comment = executor.invoke("github_comment", {"number": 5, "body": "hi"})
            review = executor.invoke(
                "github_request_review", {"number": 7, "reviewers": ["alice"]}
            )
        for result in (comment, review):
            self.assertEqual(result["error_code"], "COMMAND_NEEDS_APPROVAL")
            self.assertTrue(result["command"])

    def test_a_comment_approval_shows_the_words_that_would_be_posted(self):
        # The Round 5 report's headline was a fabricated "comment posted" claim.
        # An approval is only meaningful if the user sees the actual text first,
        # so the reason the card renders carries a preview of the body.
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), commit=True)
            executor = RepositoryToolExecutor(
                root, allow_edits=False, allow_github_write=True
            )
            result = executor.invoke(
                "github_comment",
                {"number": 5, "body": "QA round 5:\n  the push gate is missing."},
            )
        self.assertEqual(result["error_code"], "COMMAND_NEEDS_APPROVAL")
        self.assertIn("the push gate is missing", result["approval_reason"])


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

    def test_get_issue_schema_warns_the_model_the_content_is_untrusted(self):
        # #540: the issue body/comments this tool returns are attacker-
        # influenceable (anyone can open an issue), so the tool description
        # itself -- read before the model ever calls it -- must say so.
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), commit=True)
            executor = RepositoryToolExecutor(
                root, allow_edits=False, allow_github_read=True
            )
            schemas = {s["function"]["name"]: s for s in executor.schemas()}
        description = schemas["github_get_issue"]["function"]["description"]
        self.assertIn("untrusted quoted data", description)

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
                root,
                allow_edits=False,
                allow_github_write=True,
                allow_command="gh pr comment 5",
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
                root,
                allow_edits=False,
                allow_github_write=True,
                allow_command="gh pr edit 7 --add-reviewer alice",
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
        # Push disabled -> the model is told BOTH gates (token + consent) and
        # pointed at the control a GUI user can actually see. Round 2: the old
        # text named CLI commands (`vesta github allow-push on`) to a GUI-only
        # user, who then invented their own plausible-sounding Settings path.
        # The contract now dictates the exact wording and forbids improvising.
        self.assertIn("Providers & Connections", contract)
        self.assertIn("Enable pushes & PRs", contract)
        self.assertIn("connect a GitHub token", contract)
        self.assertIn("Never invent a different button", contract)

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


class GuardDecisionAuditTests(unittest.TestCase):
    """A live tool call leaves a real audit-trail entry, not just a return
    value (#546). Before this, opaihub.audit's tamper-evident chain only
    recorded entries from the manual `vesta guard` CLI command -- an actual
    autonomous run's own decisions left no trace at all."""

    def test_a_permitted_write_is_recorded_as_guard_allow(self):
        from opaihub.audit import GUARD_ALLOW, read_audit

        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), commit=True)
            result = _executor(root).invoke(
                "write_file", {"path": "a.txt", "content": "hello"}
            )
            self.assertTrue(result["ok"], result)
            events = [e for e in read_audit(root) if e["event_type"] == GUARD_ALLOW]

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["operation"], "write_file")
        self.assertEqual(events[0]["actor"], "agent")

    def test_a_blocked_write_is_recorded_as_guard_deny_with_a_reason(self):
        # DIRTY_PATH_CONFLICT (a *different*, earlier check on the planned
        # path itself) never reaches _mutation_gate at all -- the scenario
        # that actually exercises require_mutation_permitted's own deny path
        # is an *unrelated* foreign dirty file blocking an unrelated planned
        # write, exactly like test_repository_safety.py's
        # test_gate_refuses_unrelated_user_changes_without_isolation.
        from opaihub.audit import GUARD_DENY, read_audit

        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), files={"docs/guide.md": "guide"}, commit=True)
            executor = _executor(root)
            (root / "docs" / "guide.md").write_text("user edit", encoding="utf-8")
            result = executor.invoke(
                "write_file", {"path": "src/app.py", "content": "print(1)"}
            )
            self.assertFalse(result["ok"], result)
            events = [e for e in read_audit(root) if e["event_type"] == GUARD_DENY]

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["operation"], "write_file")
        self.assertTrue(events[0]["reason"])

    def test_denied_and_allowed_entries_share_one_tamper_evident_chain(self):
        # Not two independent logs -- one hash-chained sequence a later
        # entry's prev_hash depends on, proving nothing was inserted or
        # reordered after the fact.
        from opaihub.audit import verify_chain

        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), files={"docs/guide.md": "guide"}, commit=True)
            executor = _executor(root)
            executor.invoke("write_file", {"path": "new.txt", "content": "ok"})
            (root / "docs" / "guide.md").write_text("user edit", encoding="utf-8")
            executor.invoke("write_file", {"path": "src/app.py", "content": "print(1)"})
            chain = verify_chain(root)

        self.assertTrue(chain["ok"], chain)
        self.assertGreaterEqual(chain["length"], 2)

    def test_an_executed_confirm_class_command_is_recorded_as_evidence(self):
        from opaihub.audit import EVIDENCE_PACKET, read_audit

        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), commit=True)
            executor = _executor(root, allow_git_ops=True)
            executor.grant_command_once("git push origin HEAD")
            executor.invoke("run_command", {"command": "git push origin HEAD"})
            events = [e for e in read_audit(root) if e["event_type"] == EVIDENCE_PACKET]

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["operation"], "one_shot_grant_consumed")
        self.assertIn("git push", events[0]["command"])


if __name__ == "__main__":
    unittest.main()
