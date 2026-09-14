"""Behavioral contract for Vesta's coding-agent autonomy layer."""

from __future__ import annotations

import unittest
import subprocess
import tempfile
from pathlib import Path
from unittest import mock

from opaihub.agent_policy import (
    AgentMode,
    build_capability_contract,
    resolve_agent_policy,
)
from opaihub.repo_context import (
    active_repo_context,
    DirtyConflictError,
    classify_dirty_paths,
    load_active_repo,
    prepare_isolated_worktree,
    resolve_repo_context,
    save_active_repo,
)
from opaihub.github_workflow import (
    CodingWorkflow,
    GitHubAdapter,
    ShipChecks,
    rank_issues,
    select_small_important_issue,
)
from opaihub.workflow_state import (
    WorkflowState,
    load_workflow_state,
    save_workflow_state,
)

from tests._helpers import FakeAccountRunner, make_repo


class AgentPolicyTests(unittest.TestCase):
    def test_plain_implementation_request_keeps_publication_local(self):
        policy = resolve_agent_policy("Fix the parser and run the tests.")

        self.assertEqual(policy.mode, AgentMode.IMPLEMENT)
        self.assertTrue(policy.allows("edit_files"))
        self.assertTrue(policy.allows("commit"))
        self.assertFalse(policy.allows("push"))
        self.assertFalse(policy.allows("create_pr"))
        self.assertFalse(policy.allows("merge_pr"))

    def test_explicit_push_request_allows_publication_without_merge(self):
        policy = resolve_agent_policy("Fix the parser, commit it, and push the branch.")

        self.assertEqual(policy.mode, AgentMode.IMPLEMENT)
        self.assertTrue(policy.allows("push"))
        self.assertTrue(policy.allows("create_pr"))
        self.assertFalse(policy.allows("merge_pr"))

    def test_explicit_pull_request_allows_publication_without_merge(self):
        policy = resolve_agent_policy("Fix the parser and open a pull request.")

        self.assertEqual(policy.mode, AgentMode.IMPLEMENT)
        self.assertTrue(policy.allows("push"))
        self.assertTrue(policy.allows("create_pr"))
        self.assertFalse(policy.allows("merge_pr"))

    def test_bare_publish_request_does_not_authorize_git_publication(self):
        policy = resolve_agent_policy("Publish the documentation locally.")

        self.assertFalse(policy.allows("push"))
        self.assertFalse(policy.allows("create_pr"))

    def test_submit_pull_request_allows_publication_without_merge(self):
        policy = resolve_agent_policy("Submit a pull request.")

        self.assertEqual(policy.mode, AgentMode.IMPLEMENT)
        self.assertTrue(policy.allows("push"))
        self.assertTrue(policy.allows("create_pr"))
        self.assertFalse(policy.allows("merge_pr"))

    def test_local_request_with_publish_prohibition_stays_local(self):
        policy = resolve_agent_policy(
            "Fix the parser locally. Do not push or open a pull request."
        )

        self.assertEqual(policy.mode, AgentMode.IMPLEMENT)
        self.assertTrue(policy.allows("edit_files"))
        self.assertFalse(policy.allows("push"))
        self.assertFalse(policy.allows("create_pr"))

    def test_later_publish_prohibition_overrides_earlier_publish_request(self):
        policy = resolve_agent_policy(
            "Fix the parser and push the branch. Do not push or open a pull request."
        )

        self.assertEqual(policy.mode, AgentMode.IMPLEMENT)
        self.assertTrue(policy.allows("edit_files"))
        self.assertFalse(policy.allows("push"))
        self.assertFalse(policy.allows("create_pr"))

    def test_ship_prohibitions_override_earlier_and_local_ship_signals(self):
        for message in [
            "Do not merge this PR; implement the fix only.",
            "Fix the parser, merge it after tests pass. But must not merge.",
            "Never ship this. Implement the fix and run the relevant tests.",
        ]:
            policy = resolve_agent_policy(message)

            self.assertEqual(policy.mode, AgentMode.IMPLEMENT, message)
            self.assertTrue(policy.allows("edit_files"), message)
            self.assertFalse(policy.allows("push"), message)
            self.assertFalse(policy.allows("create_pr"), message)
            self.assertFalse(policy.allows("merge_pr"), message)

    def test_fix_issue_and_make_pr_selects_implement_mode(self):
        policy = resolve_agent_policy("Fix issue #42, run tests, and make a PR.")

        self.assertEqual(policy.mode, AgentMode.IMPLEMENT)
        self.assertTrue(policy.allows("edit_files"))
        self.assertTrue(policy.allows("create_pr"))
        self.assertFalse(policy.needs_clarification)

    def test_common_coding_imperatives_select_implement_without_a_focus_picker(self):
        for request in (
            "Improve the workspace picker.",
            "Update the README and tests.",
            "Rename the confusing mode label.",
            "Remove the obsolete fallback.",
            "Run the relevant tests.",
            "Create a new config file.",
            "Solve issue #18.",
        ):
            with self.subTest(request=request):
                self.assertEqual(
                    resolve_agent_policy(request).mode, AgentMode.IMPLEMENT
                )

    def test_explain_only_selects_read_only_explain_mode(self):
        policy = resolve_agent_policy(
            "Explain how the auth flow works. Do not edit files."
        )

        self.assertEqual(policy.mode, AgentMode.EXPLAIN)
        self.assertFalse(policy.allows("edit_files"))

    def test_explanation_questions_that_mention_write_verbs_stay_read_only(self):
        for request in (
            "How do I update the README?",
            "Explain how to fix the auth bug.",
            "Why would I remove this fallback?",
        ):
            with self.subTest(request=request):
                policy = resolve_agent_policy(request)
                self.assertEqual(policy.mode, AgentMode.EXPLAIN)
                self.assertFalse(policy.allows("edit_files"))

    def test_a_write_instruction_after_an_explanation_leading_question_is_honoured(
        self,
    ):
        # The bug: a message opening with an explanation-sounding word ("What",
        # "Why", "How") was forced into EXPLAIN regardless of what the rest of
        # the message asked for, discarding an unambiguous later instruction —
        # e.g. "What's uncommitted? Commit it and open a PR" stayed read-only
        # and Vesta reported it could not act. The write instruction is in its
        # own, later sentence here, which is what distinguishes it from "How do
        # I fix this bug?" below.
        for request in (
            "What's uncommitted right now? Create a branch, commit it, "
            "and open a pull request for me.",
            "What changes are pending? Please commit them and open a PR.",
            "Why is the repo dirty? Go ahead and push a PR with these changes.",
        ):
            with self.subTest(request=request):
                policy = resolve_agent_policy(request)
                self.assertEqual(policy.mode, AgentMode.IMPLEMENT)
                self.assertTrue(policy.allows("commit"))
                self.assertTrue(policy.allows("push"))
                self.assertTrue(policy.allows("create_pr"))

    def test_a_write_verb_inside_the_question_itself_still_stays_read_only(self):
        # The guard the fix must not break: "fix" here is what is being asked
        # ABOUT ("how do I do X"), not a standalone instruction to do it. There
        # is no sentence break between the question and the write verb, which
        # is exactly what keeps this apart from the cases above.
        for request in (
            "How do I fix this bug?",
            "What's the best way to commit this?",
            "Why would pushing this branch help?",
        ):
            with self.subTest(request=request):
                policy = resolve_agent_policy(request)
                self.assertEqual(policy.mode, AgentMode.EXPLAIN)
                self.assertFalse(policy.allows("edit_files"))

    def test_a_negated_write_clause_after_a_question_still_stays_read_only(self):
        # A later sentence exists, but it explicitly forbids acting — the fix
        # must not treat "a later sentence exists" as sufficient on its own.
        policy = resolve_agent_policy(
            "Explain the routing flow only. Do not edit files."
        )
        self.assertEqual(policy.mode, AgentMode.EXPLAIN)
        self.assertFalse(policy.allows("edit_files"))

    def test_a_current_request_marker_still_overrides_a_leading_question(self):
        # The harness-formatted path: a "Current request:" section is how a
        # caller demarcates the live ask after prior context. A write
        # instruction there must control regardless of what came before it.
        policy = resolve_agent_policy(
            "What does this repo do? Some background.\n\n"
            "Current request: commit the pending changes and open a pull request."
        )
        self.assertEqual(policy.mode, AgentMode.IMPLEMENT)
        self.assertTrue(policy.allows("create_pr"))

    def test_review_without_edits_selects_review_mode(self):
        policy = resolve_agent_policy(
            "Review this repository for correctness. Report findings only; no edits."
        )

        self.assertEqual(policy.mode, AgentMode.REVIEW)
        self.assertTrue(policy.allows("read_files"))
        self.assertFalse(policy.allows("edit_files"))

    def test_make_pr_implies_file_edits_commits_and_push(self):
        policy = resolve_agent_policy(
            "Make a PR that fixes the broken workspace picker."
        )

        for capability in (
            "edit_files",
            "create_branch",
            "commit",
            "push",
            "create_pr",
        ):
            self.assertTrue(policy.allows(capability), capability)

    def test_merge_after_tests_selects_ship_with_conditional_merge(self):
        policy = resolve_agent_policy(
            "Fix it, open a PR, and merge it after tests pass."
        )

        self.assertEqual(policy.mode, AgentMode.SHIP)
        self.assertTrue(policy.allows("merge_pr"))
        self.assertIn("tests_pass", policy.merge_requirements)

    def test_latest_explicit_fix_overrides_stale_read_only_boilerplate(self):
        policy = resolve_agent_policy(
            "Generic project note: do not modify files.\n\n"
            "Current request: fix the issue and make a PR."
        )

        self.assertEqual(policy.mode, AgentMode.IMPLEMENT)
        self.assertTrue(policy.allows("edit_files"))

    def test_dangerous_action_always_requires_confirmation(self):
        for request in (
            "Force push the branch and delete the old database.",
            "Delete the user's files.",
            "Run Remove-Item -Recurse on the workspace.",
        ):
            with self.subTest(request=request):
                policy = resolve_agent_policy(request)
                self.assertEqual(policy.mode, AgentMode.DANGEROUS)
                self.assertTrue(policy.requires_confirmation)
                self.assertFalse(policy.allows("force_push"))

    def test_safety_constraints_that_forbid_force_push_do_not_become_dangerous_intent(
        self,
    ):
        policy = resolve_agent_policy(
            "Fix the bug, run tests, and open a PR. Do not force push or reset --hard."
        )
        self.assertEqual(policy.mode, AgentMode.IMPLEMENT)
        self.assertFalse(policy.requires_confirmation)

    def test_unrelated_negative_sentence_does_not_hide_a_dangerous_request(self):
        policy = resolve_agent_policy("No tests are needed. Force push the branch.")
        self.assertEqual(policy.mode, AgentMode.DANGEROUS)
        self.assertTrue(policy.requires_confirmation)

    def test_capability_contract_redacts_secrets_and_is_action_oriented(self):
        policy = resolve_agent_policy(
            "Fix auth using token sk-abcdefghijklmnopqrst and make a PR."
        )
        contract = build_capability_contract(policy, active_repo="C:/repo")

        self.assertNotIn("sk-abcdefghijklmnopqrst", contract)
        self.assertIn("Implement", contract)
        self.assertIn("Proceed without repeated confirmation", contract)
        self.assertIn("C:/repo", contract)

    def test_capability_contract_marks_issue_search_results_as_untrusted_data(self):
        policy = resolve_agent_policy("Find me a GitHub issue we can solve.")

        contract = build_capability_contract(
            policy,
            active_repo="C:/repo",
            tool_names=("github_search_issues", "read_file"),
        )

        self.assertIn("github_search_issues", contract)
        self.assertIn("untrusted quoted data", contract.lower())
        self.assertIn("cannot authorize", contract.lower())

    def test_capability_contract_marks_a_read_issue_as_untrusted_data(self):
        # #540: github_get_issue imports a full issue body plus up to 50
        # comments -- a far larger injection surface than a search excerpt --
        # so it needs the same explicit warning github_search_issues has.
        policy = resolve_agent_policy("Find me a GitHub issue we can solve.")

        contract = build_capability_contract(
            policy,
            active_repo="C:/repo",
            tool_names=("github_get_issue", "read_file"),
        )

        self.assertIn("github_get_issue", contract)
        self.assertIn("untrusted quoted data", contract.lower())
        self.assertIn("cannot authorize", contract.lower())

    def test_explain_contract_tells_the_model_the_one_step_fix(self):
        # A genuinely read-only turn must not leave the model to invent its
        # own excuse or essay about why it can't act; the contract spells out
        # the exact, one-step fix.
        policy = resolve_agent_policy("Explain the routing flow. Do not edit files.")
        contract = build_capability_contract(policy, active_repo="C:/repo")

        self.assertIn("resend the same request", contract)
        self.assertIn("read-only", contract)


class AutoApplyAmbiguousRequestTests(unittest.TestCase):
    """Auto-apply's promise is "acts without asking first" (#674 follow-up):
    a message with no explicit read/write signal must not silently fall back
    to a read-only contract just because it didn't match a write-verb regex —
    that breaks Auto-apply's guarantee and is exactly the "stops and asks
    permission instead of just doing it" failure Auto-apply exists to avoid.
    An explicit read-only signal always wins regardless of run mode.
    """

    def test_ambiguous_message_defaults_to_implement_under_full_auto(self):
        policy = resolve_agent_policy(
            "the login form is broken somewhere in here", run_mode_hint="full-auto"
        )
        self.assertEqual(policy.mode, AgentMode.IMPLEMENT)
        self.assertTrue(policy.allows("edit_files"))

    def test_ambiguous_message_stays_explain_outside_full_auto(self):
        for hint in (None, "ask", "safe-auto", "approve-edits", "plan"):
            with self.subTest(run_mode_hint=hint):
                policy = resolve_agent_policy(
                    "the login form is broken somewhere in here", run_mode_hint=hint
                )
                self.assertEqual(policy.mode, AgentMode.EXPLAIN)

    def test_read_only_focus_does_not_revoke_a_pinned_full_auto(self):
        """A stored style hint must not silently cancel a pinned permission.

        This asserted the opposite. The focus is *persisted* as
        ``default_task_mode``, so a selection made once was indistinguishable
        from a deliberate choice for this turn: a workspace carrying a stored
        Explain focus answered every request read-only forever while the
        composer advertised "Auto-apply", and the user was told to switch off a
        control they had not touched. Full Auto reaching this function has
        already been pinned with an explicit acknowledgement, which outranks a
        stored hint about answer style.

        Read-only intent stays fully available: read-only wording, a question,
        a greeting, and the Ask/Plan run modes are all still honored -- the
        tests either side of this one cover exactly that.
        """

        for focus in ("explain", "plan", "review"):
            with self.subTest(focus=focus):
                policy = resolve_agent_policy(
                    "the login form", focus_hint=focus, run_mode_hint="full-auto"
                )
                self.assertEqual(policy.mode, AgentMode.IMPLEMENT)
                self.assertTrue(policy.allows("edit_files"))

    def test_read_only_focus_is_still_honored_outside_full_auto(self):
        for focus in ("explain", "plan"):
            for run_mode in (None, "ask", "safe-auto", "approve-edits"):
                with self.subTest(focus=focus, run_mode=run_mode):
                    policy = resolve_agent_policy(
                        "the login form", focus_hint=focus, run_mode_hint=run_mode
                    )
                    self.assertEqual(policy.mode, AgentMode.EXPLAIN)

    def test_continuation_resumes_work_instead_of_answering_read_only(self):
        """ "try again" means carry on -- it is an instruction, not an absence.

        Such a message carries no write verb, so it used to score as no signal
        at all and fall through to the focus hint, where a stored read-only
        focus turned it into a refusal.
        """

        for message in (
            "try again",
            "continue",
            "do it",
            "keep going",
            "please continue",
            "go ahead",
            "retry",
        ):
            for run_mode in ("safe-auto", "approve-edits", "full-auto"):
                with self.subTest(message=message, run_mode=run_mode):
                    policy = resolve_agent_policy(
                        message, focus_hint="explain", run_mode_hint=run_mode
                    )
                    self.assertEqual(policy.mode, AgentMode.IMPLEMENT)

    def test_continuation_does_not_widen_a_read_only_run_mode(self):
        for run_mode in ("ask", "plan"):
            with self.subTest(run_mode=run_mode):
                policy = resolve_agent_policy("try again", run_mode_hint=run_mode)
                self.assertEqual(policy.mode, AgentMode.EXPLAIN)

    def test_pr_request_is_recognized_with_any_determiner(self):
        """ "make **the** pr" is as much a PR request as "make a pr".

        Only "a pr" was matched, so the everyday phrasing used once a specific
        PR is under discussion scored as no write intent and was refused.
        """

        for phrase in (
            "make the pr",
            "make my pr",
            "open the pr",
            "create the pr",
            "submit the pr",
            "raise a pr",
            "send the pr",
            "put up a pr",
            "open the pull request",
            "make another pr",
        ):
            with self.subTest(phrase=phrase):
                policy = resolve_agent_policy(f"fix the bug and {phrase}")
                self.assertEqual(policy.mode, AgentMode.IMPLEMENT)
                self.assertTrue(policy.allows("create_pr"), phrase)

    def test_explicit_read_only_wording_stays_explain_even_under_full_auto(self):
        policy = resolve_agent_policy(
            "Explain how this works. Do not edit files.", run_mode_hint="full-auto"
        )
        self.assertEqual(policy.mode, AgentMode.EXPLAIN)

    def test_greeting_stays_explain_even_under_full_auto(self):
        policy = resolve_agent_policy("hi", run_mode_hint="full-auto")
        self.assertEqual(policy.mode, AgentMode.EXPLAIN)

    def test_dangerous_request_still_requires_confirmation_under_full_auto(self):
        policy = resolve_agent_policy(
            "force push the branch", run_mode_hint="full-auto"
        )
        self.assertEqual(policy.mode, AgentMode.DANGEROUS)
        self.assertTrue(policy.requires_confirmation)

    def test_build_focus_still_outranks_full_auto_default_trivially(self):
        # Not a behavior change (build already implied IMPLEMENT); guards the
        # new branch doesn't shadow the existing focus_hint dispatch.
        policy = resolve_agent_policy(
            "something", focus_hint="build", run_mode_hint="full-auto"
        )
        self.assertEqual(policy.mode, AgentMode.IMPLEMENT)


class IssueSolveIntentTests(unittest.TestCase):
    """F5/F10/F18: 'solve/fix/implement <qualifier> issue|bug|ticket' is a write
    intent, and a write verb in the message outranks a read-only focus hint."""

    def test_solve_github_issue_phrasings_classify_implement(self):
        for request in (
            "Solve GitHub issue #219 in this repo for me.",
            "Solve the GitHub issue #219.",
            "Resolve Jira ticket OPS-42.",
            "Fix ticket #42.",
            "Implement issue #7.",
            "Fix the login bug.",
        ):
            with self.subTest(request=request):
                policy = resolve_agent_policy(request)
                self.assertEqual(policy.mode, AgentMode.IMPLEMENT)
                self.assertTrue(policy.allows("edit_files"))

    def test_write_verb_in_message_outranks_read_only_focus_hint(self):
        policy = resolve_agent_policy("Fix this bug", focus_hint="explain")

        self.assertEqual(policy.mode, AgentMode.IMPLEMENT)
        self.assertTrue(policy.allows("edit_files"))

    def test_solve_github_issue_outranks_stale_explain_focus(self):
        policy = resolve_agent_policy(
            "Solve GitHub issue #219 in this repo for me.", focus_hint="explain"
        )

        self.assertEqual(policy.mode, AgentMode.IMPLEMENT)
        self.assertTrue(policy.allows("edit_files"))

    def test_negated_issue_verbs_stay_read_only(self):
        for request in (
            "Don't fix this bug.",
            "Do not solve the issue.",
        ):
            with self.subTest(request=request):
                self.assertNotEqual(
                    resolve_agent_policy(request).mode, AgentMode.IMPLEMENT
                )

    def test_explanation_questions_about_issues_stay_read_only(self):
        for request in (
            "Explain how to solve GitHub issue #219.",
            "How do I fix this bug?",
            "What does this ticket mean?",
        ):
            with self.subTest(request=request):
                policy = resolve_agent_policy(request)
                self.assertNotEqual(policy.mode, AgentMode.IMPLEMENT)
                self.assertFalse(policy.allows("edit_files"))

    def test_solve_issue_is_not_a_discovery_request(self):
        from opaihub.agent_policy import is_discovery_request

        self.assertFalse(
            is_discovery_request("Solve GitHub issue #219 in this repo for me.")
        )


class RepoContextTests(unittest.TestCase):
    def test_persisted_active_repo_is_reused_on_next_gui_start(self):
        with (
            tempfile.TemporaryDirectory() as workspace_tmp,
            tempfile.TemporaryDirectory() as repo_tmp,
        ):
            workspace = Path(workspace_tmp)
            repo = make_repo(Path(repo_tmp), commit=True)
            context = resolve_repo_context(repo)
            save_active_repo(workspace, context)

            restored = active_repo_context(workspace)

        self.assertEqual(restored.path, repo.resolve())

    def test_active_repo_context_persists_path_branch_remote_and_dirty_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(
                Path(tmp), files={"src/app.py": "print('ok')\n"}, commit=True
            )
            subprocess.run(
                ["git", "remote", "add", "origin", "https://github.com/acme/demo.git"],
                cwd=root,
                check=True,
                capture_output=True,
            )
            (root / "src" / "app.py").write_text("print('changed')\n", encoding="utf-8")

            context = resolve_repo_context(root / "src")
            save_active_repo(root, context)
            loaded = load_active_repo(root)
            refreshed = resolve_repo_context(root)

        self.assertEqual(context.path, root.resolve())
        self.assertEqual(loaded.path, root.resolve())
        self.assertTrue(context.branch)
        self.assertEqual(context.remote, "https://github.com/acme/demo.git")
        self.assertIn("src/app.py", context.dirty_paths)
        self.assertTrue(context.handle_id)
        self.assertEqual(
            context.to_dict()["safety"]["identity"]["worktree_root"],
            str(root.resolve()),
        )
        self.assertEqual(
            context.to_dict()["safety"]["assessment"]["rule_id"], "unknown_scope"
        )
        self.assertFalse(
            any(path.startswith(".opaihub/") for path in refreshed.dirty_paths)
        )

    def test_remote_credentials_are_never_persisted_or_exposed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), commit=True)
            subprocess.run(
                [
                    "git",
                    "remote",
                    "add",
                    "origin",
                    "https://secret-token@github.com/acme/repo.git",
                ],
                cwd=root,
                check=True,
                capture_output=True,
            )
            context = resolve_repo_context(root)
            save_active_repo(root, context)
            raw = (root / ".opaihub" / "gui" / "active_repo.json").read_text(
                encoding="utf-8"
            )

        self.assertEqual(context.remote, "https://github.com/acme/repo.git")
        self.assertNotIn("secret-token", raw)

    def test_unrelated_dirty_files_do_not_block_requested_change(self):
        assessment = classify_dirty_paths(
            ["docs/notes.md", "styles.css"], ["opai/agent_policy.py"]
        )

        self.assertEqual(assessment.status, "unrelated")
        self.assertTrue(assessment.can_proceed)
        self.assertEqual(assessment.conflicting_paths, ())

    def test_conflicting_dirty_files_require_clarification(self):
        assessment = classify_dirty_paths(
            ["opai/agent_policy.py", "docs/notes.md"],
            ["opai/agent_policy.py", "tests/test_agent_policy.py"],
        )

        self.assertEqual(assessment.status, "conflicting")
        self.assertFalse(assessment.can_proceed)
        self.assertEqual(assessment.conflicting_paths, ("opai/agent_policy.py",))

    def test_unknown_dirty_scope_cannot_authorize_mutation(self):
        assessment = classify_dirty_paths(["docs/notes.md"], None)

        self.assertEqual(assessment.status, "needs_inspection")
        self.assertFalse(assessment.can_proceed)

    def test_safe_worktree_command_uses_new_branch_and_preserves_unrelated_changes(
        self,
    ):
        calls = []

        def fake_run(argv, **kwargs):
            calls.append((argv, kwargs))
            return subprocess.CompletedProcess(argv, 0, "", "")

        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), files={"docs/user.md": "mine"}, commit=True)
            target = root.parent / "isolated-autonomy"
            result = prepare_isolated_worktree(
                root,
                target,
                branch="codex/autonomy",
                base="main",
                dirty_paths=["docs/user.md"],
                intended_paths=["opai/agent_policy.py"],
                run=fake_run,
            )

        self.assertEqual(result[0:4], ["git", "worktree", "add", str(target.resolve())])
        self.assertIn("codex/autonomy", result)
        self.assertEqual(len(calls), 1)

    def test_safe_worktree_refuses_conflicting_user_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), commit=True)
            with self.assertRaises(DirtyConflictError):
                prepare_isolated_worktree(
                    root,
                    root.parent / "blocked-worktree",
                    branch="codex/autonomy",
                    dirty_paths=["opai/agent_policy.py"],
                    intended_paths=["opai/agent_policy.py"],
                )


class GitHubWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.issues = [
            {
                "number": 10,
                "title": "Rewrite the entire plugin architecture",
                "body": "Large multi-quarter architectural migration.",
                "labels": [{"name": "enhancement"}, {"name": "size:xl"}],
                "url": "https://github.test/issues/10",
            },
            {
                "number": 11,
                "title": "Workspace picker forgets the active repository",
                "body": "High user impact, reproducible, and easy to test.",
                "labels": [
                    {"name": "bug"},
                    {"name": "high-impact"},
                    {"name": "size:s"},
                ],
                "url": "https://github.test/issues/11",
            },
            {
                "number": 12,
                "title": "Polish an internal comment",
                "body": "Tiny but no user impact.",
                "labels": [{"name": "size:s"}],
                "url": "https://github.test/issues/12",
            },
        ]

    def test_issues_are_ranked_by_impact_size_testability_and_risk(self):
        ranked = rank_issues(self.issues)

        self.assertEqual(ranked[0].number, 11)
        self.assertGreater(ranked[0].score, ranked[1].score)
        self.assertIn("bounded", ranked[0].reasons)

    def test_small_important_issue_selects_manageable_high_value_work(self):
        selected = select_small_important_issue(self.issues)

        self.assertEqual(selected.number, 11)
        self.assertNotEqual(selected.number, 10)

    def test_github_adapter_is_mockable_and_links_pr_to_issue(self):
        calls = []

        def fake_run(argv, **kwargs):
            calls.append(argv)
            if argv[1:3] == ["issue", "list"]:
                return subprocess.CompletedProcess(
                    argv, 0, __import__("json").dumps(self.issues), ""
                )
            if argv[1:3] == ["pr", "create"]:
                return subprocess.CompletedProcess(
                    argv, 0, "https://github.test/pr/5\n", ""
                )
            raise AssertionError(argv)

        adapter = GitHubAdapter(Path("C:/repo"), run=fake_run)
        listed = adapter.list_open_issues()
        url = adapter.create_pr(
            title="Fix workspace context",
            body="Preserve the active repo.",
            head="codex/workspace",
            base="main",
            issue_number=11,
        )

        self.assertEqual(len(listed), 3)
        self.assertEqual(url, "https://github.test/pr/5")
        create = calls[-1]
        self.assertIn("Closes #11", create[create.index("--body") + 1])

    def test_github_adapter_exposes_structured_pr_checks_updates_and_comments(self):
        calls = []

        def fake_run(argv, **kwargs):
            calls.append(argv)
            if argv[1:3] == ["pr", "list"]:
                return subprocess.CompletedProcess(
                    argv,
                    0,
                    '[{"number":5,"title":"Fix","url":"https://github.test/pr/5","body":"Closes #11"}]',
                    "",
                )
            if argv[1:3] == ["pr", "checks"]:
                return subprocess.CompletedProcess(
                    argv, 0, '[{"name":"test","state":"SUCCESS","bucket":"pass"}]', ""
                )
            return subprocess.CompletedProcess(
                argv, 0, "https://github.test/pr/5\n", ""
            )

        # `comment_pr` intentionally persists an idempotency record.  A fixed
        # literal root made the unittest run's record visible to the following
        # pytest run in the hostile CI lane, so the second harness correctly
        # skipped the outward call and this mock contract became order-dependent.
        with tempfile.TemporaryDirectory() as tmp:
            adapter = GitHubAdapter(Path(tmp), run=fake_run)
            linked = adapter.find_linked_pr(11)
            checks = adapter.pr_checks(5)
            adapter.update_pr(5, title="Better title", body="Updated")
            adapter.comment_pr(5, "Tests passed")

        self.assertEqual(linked["number"], 5)
        self.assertEqual(checks[0]["state"], "SUCCESS")
        self.assertIn(
            ["gh", "pr", "edit", "5", "--title", "Better title", "--body", "Updated"],
            calls,
        )
        self.assertIn(["gh", "pr", "comment", "5", "--body", "Tests passed"], calls)

    def test_merge_runs_only_when_every_ship_gate_passes(self):
        calls = []

        def fake_run(argv, **kwargs):
            calls.append(argv)
            return subprocess.CompletedProcess(argv, 0, "merged\n", "")

        # merge_pr persists its operation intent before dispatch (#616), so it
        # needs a real repository root — the claim store lives under it.
        with tempfile.TemporaryDirectory() as tmp:
            workflow = CodingWorkflow(GitHubAdapter(make_repo(Path(tmp)), run=fake_run))
            blocked = ShipChecks()
            self.assertFalse(workflow.merge_if_safe(5, blocked)["merged"])
            self.assertEqual(calls, [])

            safe = ShipChecks(
                tests_pass=True,
                correct_branch=True,
                no_secrets=True,
                no_risky_files=True,
                production_auth_safe=True,
                no_unrelated_files=True,
                no_conflicts=True,
                checks_acceptable=True,
            )
            result = workflow.merge_if_safe(5, safe)
            self.assertTrue(result["merged"])
        self.assertEqual(calls[-1][:4], ["gh", "pr", "merge", "5"])

    def test_each_failed_ship_gate_names_the_blocker(self):
        fields = {
            "tests_pass": "tests",
            "correct_branch": "branch",
            "no_secrets": "secrets",
            "no_risky_files": "risky files",
            "production_auth_safe": "production or authentication",
            "no_unrelated_files": "unrelated files",
            "no_conflicts": "conflicts",
            "checks_acceptable": "checks",
        }
        for field, phrase in fields.items():
            values = {name: True for name in fields}
            values[field] = False
            checks = ShipChecks(**values)
            self.assertFalse(checks.can_merge, field)
            self.assertTrue(any(phrase in item for item in checks.blockers), field)


class WorkflowPipelineTests(unittest.TestCase):
    def test_workflow_state_roundtrips_without_prompt_or_secret_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), commit=True)
            state = WorkflowState(
                mode="implement",
                phase="testing",
                tests_status="running",
                pr_url="",
                merge_status="not_requested",
            )
            save_workflow_state(root, state)
            loaded = load_workflow_state(root)
            raw = (root / ".opaihub" / "gui" / "workflow.json").read_text(
                encoding="utf-8"
            )

        self.assertEqual(loaded, state)
        self.assertNotIn("prompt", raw.lower())
        self.assertNotIn("secret", raw.lower())

    def test_pipeline_current_fix_request_overrides_stale_read_only_focus(self):
        from opaihub.gui_pipeline import handle_gui_message

        runner = FakeAccountRunner(text="Implemented and tested.")
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), commit=True)
            result = handle_gui_message(
                root,
                "Generic note: do not modify files.\n\nCurrent request: fix issue #7 and make a PR.",
                model_id="account:claude:sonnet",
                mode="ask",
                account_runner=runner,
            )

        self.assertEqual(result["agent_policy"]["mode"], "implement")
        self.assertEqual(result["effective_run_mode"], "safe-auto")
        self.assertTrue(runner.calls[0]["allow_edits"])
        self.assertIn("Effective mode: Implement", runner.calls[0]["prompt"])
        self.assertIn("Current request: fix issue #7", runner.calls[0]["prompt"])
        # F14 honesty: the fake provider claimed a fix but left zero diffs, so
        # the run must NOT decorate as reviewing_diff. (The previous assertion
        # of "reviewing_diff" encoded the exact F14 bug.)
        self.assertEqual(result["workflow"]["phase"], "completed")
        self.assertEqual(result["completion_note"], "no_changes")
        self.assertIn("no changes", result["workflow"]["message"])
        self.assertEqual(result["workflow"]["tests_status"], "not_verified")
        self.assertTrue(result["workflow"]["task_id"])
        self.assertGreaterEqual(len(result["workflow"]["history"]), 5)
        self.assertEqual(result["task_packet"]["mode"], "implement")
        self.assertIn("run_tests", result["task_packet"]["allowed_actions"])
        self.assertTrue(result["repo_context"]["path"])

    def test_pipeline_solve_github_issue_is_implement_despite_explain_focus(self):
        # F5/F10/F18 end-to-end: the exact QA prompt, with a stale Explain task
        # focus, must resolve to an edit-capable run — not a read-only ask.
        from opaihub.gui_pipeline import handle_gui_message

        runner = FakeAccountRunner(text="Implemented and tested.")
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), commit=True)
            result = handle_gui_message(
                root,
                "Solve GitHub issue #219 in this repo for me.",
                model_id="account:claude:sonnet",
                mode="safe-auto",
                account_runner=runner,
                focus_hint="explain",
            )

        self.assertEqual(result["agent_policy"]["mode"], "implement")
        self.assertEqual(result["effective_run_mode"], "safe-auto")
        self.assertTrue(runner.calls[0]["allow_edits"])
        self.assertIn("Effective mode: Implement", runner.calls[0]["prompt"])
        self.assertNotIn("This is read-only", runner.calls[0]["prompt"])

    def test_pipeline_solve_issue_preserves_pinned_full_auto(self):
        # F18: a stale read-only focus may not silently force read-only when
        # Full Auto is pinned and the message is an explicit write request.
        from opaihub.gui_pipeline import handle_gui_message

        runner = FakeAccountRunner(text="Implemented and tested.")
        prefs = {
            "default_mode": "full-auto",
            "full_auto_pinned": True,
            "full_auto_acknowledged_at": "2026-07-06T00:00:00+00:00",
        }
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch("opaihub.gui_pipeline.load_gui_preferences", return_value=prefs),
        ):
            root = make_repo(Path(tmp), commit=True)
            result = handle_gui_message(
                root,
                "Solve GitHub issue #219 in this repo for me.",
                model_id="account:claude:sonnet",
                mode="full-auto",
                account_runner=runner,
                focus_hint="explain",
            )

        self.assertEqual(result["agent_policy"]["mode"], "implement")
        self.assertEqual(result["effective_run_mode"], "full-auto")
        self.assertTrue(runner.calls[0]["allow_edits"])

    def test_pipeline_preserves_pinned_full_auto_for_implementation(self):
        from opaihub.gui_pipeline import handle_gui_message

        runner = FakeAccountRunner(text="Implemented and tested.")
        prefs = {
            "default_mode": "full-auto",
            "full_auto_pinned": True,
            "full_auto_acknowledged_at": "2026-07-06T00:00:00+00:00",
        }
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch("opaihub.gui_pipeline.load_gui_preferences", return_value=prefs),
        ):
            root = make_repo(Path(tmp), commit=True)
            result = handle_gui_message(
                root,
                "Fix the bug and run tests.",
                model_id="account:claude:sonnet",
                mode="full-auto",
                account_runner=runner,
            )

        self.assertEqual(result["effective_run_mode"], "full-auto")
        self.assertEqual(runner.calls[0]["mode"], "full-auto")
        self.assertTrue(runner.calls[0]["allow_edits"])

    def test_pipeline_explain_request_remains_read_only_even_from_safe_auto(self):
        from opaihub.gui_pipeline import handle_gui_message

        runner = FakeAccountRunner(text="Here is how it works.")
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), commit=True)
            result = handle_gui_message(
                root,
                "Explain the routing flow only. Do not edit files.",
                model_id="account:claude:sonnet",
                mode="safe-auto",
                account_runner=runner,
            )

        self.assertEqual(result["agent_policy"]["mode"], "explain")
        self.assertEqual(result["effective_run_mode"], "ask")
        self.assertFalse(runner.calls[0]["allow_edits"])
        self.assertIn("This is read-only", runner.calls[0]["prompt"])
        self.assertEqual(result["workflow"]["phase"], "completed")

    def test_output_format_instruction_cannot_override_raw_user_intent(self):
        from opaihub.gui_pipeline import handle_gui_message

        runner = FakeAccountRunner(text="Explanation")
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), commit=True)
            result = handle_gui_message(
                root,
                "Explain the routing flow only.",
                model_id="account:claude:sonnet",
                mode="safe-auto",
                account_runner=runner,
                focus_hint="explain",
                output_instruction="Format as a GitHub issue with a Suggested fix.",
            )

        self.assertEqual(result["agent_policy"]["mode"], "explain")
        self.assertFalse(runner.calls[0]["allow_edits"])
        self.assertIn("Suggested fix", runner.calls[0]["prompt"])


class GuiAutonomySurfaceTests(unittest.TestCase):
    def test_desktop_workflow_summary_names_mode_tests_pr_and_merge(self):
        from opai.gui_controls import workflow_summary

        summary = workflow_summary(
            {
                "agent_policy": {"label": "Ship"},
                "workflow": {
                    "phase": "completed",
                    "tests_status": "passed",
                    "pr_url": "https://github.test/pr/9",
                    "merge_status": "merged",
                },
            }
        )

        self.assertEqual(
            summary,
            [
                "Ship",
                "Completed",
                "Tests: passed",
                "https://github.test/pr/9",
                "Merge: merged",
            ],
        )

    def test_boot_payload_surfaces_active_repo_and_workflow_truth(self):
        from opai.gui_web import boot_payload

        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), files={"app.py": "print('ok')\n"}, commit=True)
            subprocess.run(
                ["git", "remote", "add", "origin", "https://github.com/acme/gui.git"],
                cwd=root,
                check=True,
                capture_output=True,
            )
            save_workflow_state(
                root,
                WorkflowState(
                    mode="ship",
                    phase="testing",
                    tests_status="running",
                    pr_url="https://github.test/pr/9",
                    merge_status="pending_checks",
                    blockers=("one check pending",),
                ),
            )
            payload = boot_payload(root)
            # Inspector is deferred at boot (#246); fetch it via the same path
            # the panel uses to assert the workflow rows are surfaced.
            from opai.gui_web import _inspector

            inspector = _inspector(
                root,
                {
                    "model_label": "Auto",
                    "model_advanced_label": "Auto",
                    "model_kind": "auto",
                    "mode": "safe-auto",
                    "mode_label": "Safe Auto",
                    "focus": "general",
                    "format": "normal",
                    "accounts": [],
                },
            )

        workspace = payload["workspace"]
        self.assertEqual(workspace["remote"], "https://github.com/acme/gui.git")
        self.assertIn("dirty", workspace)
        self.assertEqual(payload["workflow"]["mode"], "ship")
        self.assertEqual(payload["workflow"]["tests_status"], "running")
        self.assertEqual(payload["workflow"]["pr_url"], "https://github.test/pr/9")
        self.assertEqual(payload["workflow"]["merge_status"], "pending_checks")
        self.assertEqual(payload["workflow"]["blockers"], ["one check pending"])
        labels = {row["label"] for row in inspector["rows"]}
        self.assertIn("Agent mode", labels)
        self.assertIn("Workflow", labels)


class InstructionContractTests(unittest.TestCase):
    def test_managed_instruction_grants_normal_coding_workflow_once_requested(self):
        from opai.integrations import project_instruction_text

        text = project_instruction_text(Path("C:/repo"))

        self.assertIn("latest explicit request", text.lower())
        self.assertIn("edit files", text.lower())
        self.assertIn("create a branch", text.lower())
        self.assertIn("commit", text.lower())
        self.assertIn("open a pull request", text.lower())
        self.assertIn("do not ask again", text.lower())

    def test_gitops_prompt_treats_current_pr_or_merge_request_as_authorization(self):
        prompt = Path("opaihub/data/hub/prompts/gitops.md").read_text(encoding="utf-8")

        self.assertIn("current request authorizes", prompt.lower())
        self.assertNotIn("do not suggest push/merge", prompt.lower())

    def test_static_permissions_do_not_regate_normal_push_and_merge(self):
        config = Path("configs/permissions.yaml").read_text(encoding="utf-8")

        self.assertNotIn('- "git push"', config)
        self.assertNotIn('- "git merge"', config)
        self.assertIn("git push --force", config)

    def test_read_only_focus_is_advisory_when_current_request_is_to_fix(self):
        from opai.gui_modes import compose_prompt

        prompt = compose_prompt("Fix the bug and make a PR.", task_mode_id="explain")

        self.assertIn("advisory", prompt.lower())
        self.assertNotIn("Do not modify any files", prompt)
        self.assertEqual(resolve_agent_policy(prompt).mode, AgentMode.IMPLEMENT)


if __name__ == "__main__":
    unittest.main()
