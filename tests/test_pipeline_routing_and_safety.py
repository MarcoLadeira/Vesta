"""Send-pipeline correctness: Safe Auto scope + block copy (#142), and the
selected local model actually running (#143).

All hermetic — no real CLI, model, or network is touched.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from _helpers import FakeAccountRunner, FakeLocalRunner, make_repo

from vestahub.gui_pipeline import (
    handle_gui_message,
    repo_fingerprint,
    request_tool_authority,
)
from vestahub.intent_router import safety_warnings
from vestahub.verification_policy import PolicyArtifactRef


# ---------------------------------------------------------------------------
# #142 — a question ABOUT a risky command is not a request to RUN it, and the
# block never nudges the user toward Full Auto. The pre-flight scan still fires
# for genuine imperative destructive prompts (the paid/exec cost-firewall gate).
# ---------------------------------------------------------------------------
class SafeAutoInquiryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = make_repo(Path(self._tmp.name))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_explain_question_is_not_flagged(self):
        warnings = safety_warnings(
            self.root, "explain what `git reset --hard` does", mode="safe-auto"
        )
        self.assertEqual(warnings, [])

    def test_how_question_is_not_flagged(self):
        warnings = safety_warnings(self.root, "how does rm -rf work", mode="safe-auto")
        self.assertEqual(warnings, [])

    def test_what_does_question_with_qmark_is_not_flagged(self):
        warnings = safety_warnings(
            self.root, "what does `git reset --hard` actually do?", mode="ask"
        )
        self.assertEqual(warnings, [])

    def test_imperative_destructive_is_still_flagged(self):
        warnings = safety_warnings(
            self.root, "delete the repository with rm -rf", mode="safe-auto"
        )
        self.assertTrue(warnings)

    def test_imperative_destructive_flagged_even_in_ask_mode(self):
        # Preserved behaviour: an account/proxy paid call in ask mode is still
        # gated before spend — only the *question* case changed.
        warnings = safety_warnings(self.root, "rm -rf the whole project", mode="ask")
        self.assertTrue(warnings)

    def test_full_auto_is_exempt(self):
        warnings = safety_warnings(self.root, "rm -rf everything", mode="full-auto")
        self.assertEqual(warnings, [])

    def test_ask_mode_risky_question_is_answered_not_blocked(self):
        with mock.patch(
            "vestahub.ask.run_ask",
            return_value={"status": "answered_locally", "answer": "It resets HEAD."},
        ):
            res = handle_gui_message(
                self.root,
                "what does `git reset --hard` actually do?",
                model_id="auto",
                mode="ask",
            )
        self.assertNotEqual(res["status"], "blocked")
        self.assertEqual(res["status"], "answered")

    def test_blocked_message_does_not_recommend_full_auto(self):
        res = handle_gui_message(
            self.root,
            "delete the repository with rm -rf",
            model_id="account:claude:sonnet",
            mode="safe-auto",
            account_runner=FakeAccountRunner(),
        )
        self.assertEqual(res["status"], "blocked")
        haystack = (res["answer"] + " " + " ".join(res["next_actions"])).lower()
        self.assertNotIn("full auto", haystack)
        # ...and it should point somewhere safe instead.
        self.assertTrue(
            "ask" in haystack or "plan" in haystack or "rephrase" in haystack
        )

    def test_blocked_flow_never_invokes_the_model(self):
        fake = FakeAccountRunner(text="should not run")
        res = handle_gui_message(
            self.root,
            "wipe it: delete everything with rm -rf",
            model_id="account:claude:sonnet",
            mode="safe-auto",
            account_runner=fake,
        )
        self.assertEqual(res["status"], "blocked")
        self.assertEqual(fake.calls, [])


# ---------------------------------------------------------------------------
# #143 — a concrete local model id runs THAT model, not the first discovered.
# ---------------------------------------------------------------------------
class SelectedLocalModelTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = make_repo(Path(self._tmp.name))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_selected_local_model_answer_comes_from_that_runner(self):
        # A provided runner is used before any tier gate, so this proves the
        # picked model — not detect_local_runner's first hit — produced the text.
        selected = FakeLocalRunner(model="qwen2.5-coder:7b", answer="SELECTED-ANSWER")
        with mock.patch(
            "vestahub.local_runner.runner_for_model", return_value=selected
        ) as resolver:
            res = handle_gui_message(
                self.root,
                "summarize this file",
                model_id="ollama:qwen2.5-coder:7b",
                mode="ask",
            )
        resolver.assert_called_once()
        self.assertEqual(resolver.call_args.args[0], "ollama:qwen2.5-coder:7b")
        self.assertEqual(res["status"], "answered")
        self.assertIn("SELECTED-ANSWER", res["answer"])

    def test_selected_runner_is_threaded_into_run_ask(self):
        captured: dict = {}

        def fake_run_ask(root, task, **kwargs):
            captured.update(kwargs)
            return {"status": "answered_locally", "answer": "ok"}

        selected = FakeLocalRunner(answer="ok")
        with (
            mock.patch("vestahub.local_runner.runner_for_model", return_value=selected),
            mock.patch("vestahub.ask.run_ask", side_effect=fake_run_ask),
        ):
            handle_gui_message(
                self.root, "task", model_id="ollama:llama3.2", mode="ask"
            )
        self.assertIs(captured["runner"], selected)
        self.assertEqual(captured["selected_model_id"], "ollama:llama3.2")

    def test_auto_resolves_no_specific_runner(self):
        captured: dict = {}

        def fake_run_ask(root, task, **kwargs):
            captured.update(kwargs)
            return {"status": "answered_locally", "answer": "ok"}

        with (
            mock.patch("vestahub.local_runner.runner_for_model") as resolver,
            mock.patch("vestahub.ask.run_ask", side_effect=fake_run_ask),
        ):
            handle_gui_message(self.root, "task", model_id="auto", mode="ask")
        resolver.assert_not_called()
        self.assertIsNone(captured["runner"])
        self.assertIsNone(captured["selected_model_id"])

    def test_unavailable_selected_model_maps_to_needs_model(self):
        with (
            mock.patch(
                "vestahub.local_runner.runner_for_model",
                return_value=FakeLocalRunner(available=False),
            ),
            mock.patch(
                "vestahub.ask.run_ask",
                return_value={"status": "no_local_model", "hint": "start ollama"},
            ),
        ):
            res = handle_gui_message(
                self.root, "task", model_id="ollama:not-running", mode="ask"
            )
        self.assertEqual(res["status"], "needs_model")

    def test_local_edit_capability_mismatch_is_not_answered_or_receipted(self):
        mismatch = {
            "status": "capability_mismatch",
            "capability": "edit_files",
            "reason": "The selected local runner cannot edit files safely.",
            "hint": "Choose a provider with bounded repository tools.",
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), files={"app.py": "value = 1\n"}, commit=True)
            with mock.patch("vestahub.ask.run_ask", return_value=mismatch) as run:
                result = handle_gui_message(
                    root,
                    "Fix app.py",
                    model_id="ollama:qwen-coder",
                    mode="safe-auto",
                )

        self.assertEqual(result["status"], "capability_mismatch")
        self.assertEqual(result["receipt"], {})
        self.assertEqual(result["changed_files"], [])
        self.assertEqual(result["workflow"]["phase"], "blocked")
        self.assertTrue(run.call_args.kwargs["allow_edits"])


class VerificationPolicyPipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = make_repo(
            Path(self._tmp.name), files={"app.py": "value = 1\n"}, commit=True
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_edit_pipeline_persists_policy_before_provider_dispatch(self):
        order: list[str] = []
        artifact = PolicyArtifactRef(
            path=self.root / ".vestahub" / "verification-policies" / "task" / "run.json",
            digest="0" * 64,
        )
        mismatch = {
            "status": "capability_mismatch",
            "capability": "edit_files",
            "reason": "The selected local runner cannot edit files safely.",
            "hint": "Choose a provider with bounded repository tools.",
        }

        def persist(*args, **kwargs):
            order.append("policy")
            return artifact

        def run_provider(*args, **kwargs):
            order.append("provider")
            return mismatch

        with (
            mock.patch(
                "vestahub.gui_pipeline.persist_effective_policy", side_effect=persist
            ),
            mock.patch("vestahub.ask.run_ask", side_effect=run_provider),
        ):
            result = handle_gui_message(
                self.root,
                "Fix app.py and run tests.",
                model_id="ollama:qwen-coder",
                mode="safe-auto",
            )

        self.assertEqual(order, ["policy", "provider"])
        self.assertEqual(result["verification_policy"]["artifact"], artifact.to_dict())

    def test_malformed_policy_blocks_before_provider_dispatch(self):
        (self.root / "vesta-verification-policy.yaml").write_text(
            "checks: [", encoding="utf-8"
        )
        mismatch = {"status": "answered", "answer": "should not run"}

        with mock.patch("vestahub.ask.run_ask", return_value=mismatch) as provider:
            result = handle_gui_message(
                self.root,
                "Fix app.py",
                model_id="ollama:qwen-coder",
                mode="safe-auto",
            )

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["verification_policy"]["status"], "blocked")
        provider.assert_not_called()


class HonestCompletionTests(unittest.TestCase):
    """Task 7: a run that streamed text but did not finish never shows completed."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = make_repo(Path(self._tmp.name))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run(self, ask_result):
        events: list[dict] = []
        selected = FakeLocalRunner(answer=ask_result.get("answer", ""))
        with (
            mock.patch("vestahub.local_runner.runner_for_model", return_value=selected),
            mock.patch("vestahub.ask.run_ask", return_value=ask_result),
        ):
            result = handle_gui_message(
                self.root,
                "do the thing",
                model_id="ollama:llama3.2",
                mode="ask",
                on_event=events.append,
            )
        return result, events

    def test_stuck_run_is_not_reported_completed(self):
        result, events = self._run(
            {
                "status": "answered_locally",
                "answer": "I read some files but could not finish.",
                "stopped_reason": "no_progress",
                "completion_state": "stuck_no_progress",
            }
        )
        titles = [str(e.get("title") or "") for e in events]
        self.assertNotIn("Vesta completed", titles)
        self.assertTrue(any("without finishing" in t for t in titles))
        self.assertEqual(result["checkpoint"]["completion_state"], "failed")
        self.assertEqual(result["completion_verdict"]["verdict"], "failed")

    def test_genuinely_completed_run_still_reports_completed(self):
        result, events = self._run(
            {
                "status": "answered_locally",
                "answer": "Done.",
                "completion_state": "completed",
            }
        )
        titles = [str(e.get("title") or "") for e in events]
        self.assertTrue(any(title.startswith("Response received") for title in titles))
        self.assertFalse(any("objective verified" in title.lower() for title in titles))
        self.assertEqual(result["completion_verdict"]["verdict"], "completed")
        self.assertEqual(
            result["completion_verdict"]["reason_code"], "answer_delivered"
        )
        self.assertEqual(result["checkpoint"]["completion_state"], "read_only")


class DiscoveryDetectionTests(unittest.TestCase):
    def test_positive_discovery_phrasings(self):
        from vestahub.agent_policy import is_discovery_request

        for message in (
            "find me a git issue that we can solve",
            "search for an open issue to work on",
            "look for a good first issue",
            "pick a bug we can fix next",
            "find something to build",
        ):
            self.assertTrue(is_discovery_request(message), message)

    def test_editing_and_explaining_requests_are_not_discovery(self):
        from vestahub.agent_policy import is_discovery_request

        for message in (
            "fix the login bug in app.py",
            "add a dark-mode toggle",
            "explain how routing works",
            "find and replace the constant in config.py",  # a code find, not work-discovery
        ):
            self.assertFalse(is_discovery_request(message), message)


class SmallTalkRoutingTests(unittest.TestCase):
    """A bare greeting must be answered as chat, never forced into an edit run."""

    def test_greetings_and_pleasantries_are_smalltalk(self):
        from vestahub.agent_policy import is_smalltalk_request

        for message in (
            "hi",
            "hello",
            "hey there",
            "yo",
            "thanks",
            "thanks so much",
            "ok cool",
            "good morning",
            "bye",
        ):
            self.assertTrue(is_smalltalk_request(message), message)

    def test_real_requests_are_not_smalltalk(self):
        from vestahub.agent_policy import is_smalltalk_request

        for message in (
            "hi can you fix the login bug",
            "add a feature",
            "update the readme",
            "there is a bug in app.py",
            "cool now add tests",
            "explain the auth flow",
        ):
            self.assertFalse(is_smalltalk_request(message), message)

    def test_git_mutation_requests_route_to_implement_not_explain(self):
        # Bug 1: "run git add and git commit" / "delete X.md" were classified
        # read-only EXPLAIN, so the model was given a read-only contract, refused
        # the mutation, and the refusal answer was scored a green "Completed".
        # A git-mutation or file-delete request is edit intent.
        from vestahub.agent_policy import AgentMode, resolve_agent_policy

        for message in (
            "Run git add and git commit for vesta-test-notes.md",
            "commit the changes",
            "stage all files and commit them",
            "delete vesta-test-notes.md",
            "Create vesta-test-notes.md with the text 'Hello from Vesta QA test'",
        ):
            self.assertIs(
                resolve_agent_policy(message).mode, AgentMode.IMPLEMENT, message
            )
        # Read-only questions that merely mention git are still EXPLAIN.
        for message in (
            "explain the last commit",
            "what does this commit do?",
            "show me the commit history",
        ):
            self.assertIs(
                resolve_agent_policy(message).mode, AgentMode.EXPLAIN, message
            )

    def test_greeting_under_build_focus_is_explain_not_implement(self):
        # The exact reported bug: a Build focus (or Full Auto) turned "hi" into
        # an implement run that changed nothing and was marked failed.
        from vestahub.agent_policy import AgentMode, resolve_agent_policy

        self.assertIs(
            resolve_agent_policy("hi", focus_hint="build").mode, AgentMode.EXPLAIN
        )
        # A real instruction under the same focus still implements.
        self.assertIs(
            resolve_agent_policy("the login form", focus_hint="build").mode,
            AgentMode.IMPLEMENT,
        )

    def test_greeting_in_full_auto_answers_read_only_without_edit_failure(self):
        # End to end: "hi" in Full Auto with a Build focus is answered directly
        # (read-only), not run as an edit task that fails for changing nothing.
        with mock.patch(
            "vestahub.ask.run_ask",
            return_value={
                "status": "answered_locally",
                "answer": "Hello! How can I help?",
            },
        ):
            res = handle_gui_message(
                self.root,
                "hi",
                model_id="auto",
                mode="full-auto",
                focus_hint="build",
            )
        self.assertEqual(res["status"], "answered")
        self.assertEqual(res["agent_policy"]["mode"], "explain")
        self.assertNotEqual(res["run_state"], "failed")

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = make_repo(Path(self._tmp.name))

    def tearDown(self) -> None:
        self._tmp.cleanup()


class DiscoveryReadOnlyRoutingTests(unittest.TestCase):
    """Task 6: a discovery request gets read tools, never mutation tools."""

    def test_discovery_gets_read_tools_without_mutations(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), files={"app.py": "value = 1\n"}, commit=True)
            authority = request_tool_authority(
                "find me a git issue that we can solve",
                selected_mode="full-auto",
                repo_root=root,
                focus_hint="build",
            )
        self.assertFalse(authority.allow_edits)
        self.assertTrue(authority.is_discovery)
        self.assertIn("github_search_issues", authority.tool_names)
        self.assertFalse(
            {"write_file", "apply_patch", "run_command", "git_commit"}
            & set(authority.tool_names)
        )

    def test_editing_request_in_full_auto_keeps_mutation_tools(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), files={"app.py": "value = 1\n"}, commit=True)
            authority = request_tool_authority(
                "fix the bug in app.py",
                selected_mode="full-auto",
                repo_root=root,
            )
        self.assertTrue(authority.allow_edits)
        self.assertFalse(authority.is_discovery)
        self.assertIn("apply_patch", authority.tool_names)

    def test_plan_mode_is_read_only_even_for_an_edit_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), files={"app.py": "value = 1\n"}, commit=True)
            authority = request_tool_authority(
                "fix the bug in app.py", selected_mode="plan", repo_root=root
            )
        self.assertFalse(authority.allow_edits)
        self.assertNotIn("apply_patch", authority.tool_names)


class RepoFingerprintTests(unittest.TestCase):
    """Round 2: proof a run changed the repo, for changes Vesta cannot see.

    An account provider CLI runs git in its own shell, so a real commit left no
    ``changed_files`` and no Vesta tool_trace entry — and committing *clears* the
    dirty paths the run created, so a genuinely successful commit was stamped
    "Partial — no changed-file or diff evidence" on verified work.
    """

    def test_fingerprint_moves_when_a_commit_lands(self):
        import subprocess

        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), files={"app.py": "value = 1\n"}, commit=True)
            before = repo_fingerprint(root)
            (root / "app.py").write_text("value = 2\n", encoding="utf-8")
            dirty = repo_fingerprint(root)
            for argv in (
                ["git", "add", "app.py"],
                ["git", "commit", "-m", "fix: bump the value"],
            ):
                subprocess.run(argv, cwd=root, check=True, capture_output=True)
            after = repo_fingerprint(root)

        # An edit alone moves it (dirty set), and so does the commit (HEAD).
        self.assertNotEqual(before, dirty)
        self.assertNotEqual(dirty, after)
        self.assertNotEqual(before[0], after[0])

    def test_untouched_repository_fingerprints_identically(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), files={"app.py": "value = 1\n"}, commit=True)
            self.assertEqual(repo_fingerprint(root), repo_fingerprint(root))

    def test_non_repository_yields_the_empty_fingerprint(self):
        # The empty fingerprint compares equal to itself, so a non-repo can only
        # ever withhold evidence — it can never manufacture it.
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(repo_fingerprint(Path(tmp)), ("", ()))


if __name__ == "__main__":
    unittest.main()
