"""Semantic action fingerprint contract for issue #649.

The corpus intentionally names each equivalence family from the issue.  These
tests are the compatibility boundary: derivation may improve under a new
version, but a version must remain deterministic, privacy-safe, and conservative
about side effects and stale evidence.
"""

from __future__ import annotations

import json
from pathlib import Path
import random
import tempfile
import unittest

from vestahub.action_fingerprint import (
    EquivalenceLevel,
    SideEffectClass,
    compare_actions,
    fingerprint_action,
)
from vestahub.completion import CompletionState
from vestahub.tool_loop import ChatTurn, ToolLoopController, ToolLoopPolicy


class ActionFingerprintCorpusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def fingerprint(
        self,
        tool: str,
        arguments: dict | str,
        *,
        freshness: str = "repo-state-a",
        failure_class: str = "",
    ):
        return fingerprint_action(
            tool,
            arguments,
            project_root=self.root,
            repository_digest=freshness,
            failure_class=failure_class,
        )

    # Nine cross-tool read/search scenarios, including Windows path syntax.
    def test_read_file_and_cat_share_file_read_identity(self):
        native = self.fingerprint("read_file", {"path": "src/app.py"})
        shell = self.fingerprint("run_command", {"command": "cat src/app.py"})
        self.assertIs(
            compare_actions(native, shell), EquivalenceLevel.SEMANTICALLY_EQUIVALENT
        )

    def test_read_file_and_cat_with_windows_path_share_identity(self):
        native = self.fingerprint("read_file", {"path": "src/app.py"})
        shell = self.fingerprint("run_command", {"command": 'cat "src\\app.py"'})
        self.assertIs(
            compare_actions(native, shell), EquivalenceLevel.SEMANTICALLY_EQUIVALENT
        )

    def test_read_file_and_get_content_share_file_read_identity(self):
        native = self.fingerprint("read_file", {"path": "src/app.py"})
        shell = self.fingerprint(
            "run_command", {"command": "Get-Content -Path src/app.py"}
        )
        self.assertIs(
            compare_actions(native, shell), EquivalenceLevel.SEMANTICALLY_EQUIVALENT
        )

    def test_read_file_and_python_open_share_file_read_identity(self):
        native = self.fingerprint("read_file", {"path": "src/app.py"})
        python = self.fingerprint(
            "run_command",
            {"command": "python -c \"print(open('src/app.py').read())\""},
        )
        self.assertIs(
            compare_actions(native, python), EquivalenceLevel.SEMANTICALLY_EQUIVALENT
        )

    def test_search_code_and_rg_share_search_identity(self):
        native = self.fingerprint("search_code", {"query": "needle", "paths": ["src"]})
        shell = self.fingerprint("run_command", {"command": "rg needle src"})
        self.assertIs(
            compare_actions(native, shell), EquivalenceLevel.SEMANTICALLY_EQUIVALENT
        )

    def test_search_code_and_grep_share_search_identity(self):
        native = self.fingerprint("search_code", {"query": "needle", "paths": ["src"]})
        shell = self.fingerprint("run_command", {"command": "grep -R needle src"})
        self.assertIs(
            compare_actions(native, shell), EquivalenceLevel.SEMANTICALLY_EQUIVALENT
        )

    def test_search_code_and_select_string_share_search_identity(self):
        native = self.fingerprint("search_code", {"query": "needle", "paths": ["src"]})
        shell = self.fingerprint(
            "run_command",
            {"command": "Select-String -Path src -Pattern needle"},
        )
        self.assertIs(
            compare_actions(native, shell), EquivalenceLevel.SEMANTICALLY_EQUIVALENT
        )

    def test_git_status_aliases_share_repository_read_identity(self):
        native = self.fingerprint("git_status", {})
        shell = self.fingerprint("run_command", {"command": "git --no-pager status -s"})
        self.assertIs(
            compare_actions(native, shell), EquivalenceLevel.SEMANTICALLY_EQUIVALENT
        )

    def test_github_issue_tool_and_gh_cli_share_remote_read_identity(self):
        native = self.fingerprint("github_get_issue", {"number": 649})
        shell = self.fingerprint(
            "run_command", {"command": "gh issue view 649 --comments"}
        )
        self.assertIs(
            compare_actions(native, shell), EquivalenceLevel.SEMANTICALLY_EQUIVALENT
        )

    # Four verification/freshness scenarios.
    def test_unchanged_test_verification_is_equivalent(self):
        first = self.fingerprint(
            "run_tests", {"command_id": "python-unittest", "scope": "tests/unit"}
        )
        second = self.fingerprint(
            "run_tests", {"scope": "tests/unit", "command_id": "python-unittest"}
        )
        self.assertIs(compare_actions(first, second), EquivalenceLevel.EXACT_DUPLICATE)

    def test_repository_change_invalidates_verification_equivalence(self):
        before = self.fingerprint(
            "run_tests", {"command_id": "npm-test"}, freshness="repo-state-a"
        )
        after = self.fingerprint(
            "run_tests", {"command_id": "npm-test"}, freshness="repo-state-b"
        )
        self.assertIs(
            compare_actions(before, after),
            EquivalenceLevel.RELATED_MATERIALLY_DIFFERENT,
        )

    def test_verification_scope_change_is_materially_different(self):
        focused = self.fingerprint(
            "run_tests", {"command_id": "python-unittest", "scope": "tests.unit"}
        )
        full = self.fingerprint(
            "run_tests", {"command_id": "python-unittest", "scope": "all"}
        )
        self.assertIs(
            compare_actions(focused, full),
            EquivalenceLevel.RELATED_MATERIALLY_DIFFERENT,
        )

    def test_unchanged_lint_aliases_are_equivalent(self):
        direct = self.fingerprint("run_command", {"command": "ruff check ."})
        module = self.fingerprint(
            "run_command", {"command": "python -m ruff check . --no-cache"}
        )
        self.assertIs(
            compare_actions(direct, module), EquivalenceLevel.SEMANTICALLY_EQUIVALENT
        )

    # Four canonicalization scenarios.
    def test_relative_and_absolute_repository_paths_are_equivalent(self):
        relative = self.fingerprint("read_file", {"path": "src/app.py"})
        absolute = self.fingerprint(
            "read_file", {"path": str(self.root / "src" / "app.py")}
        )
        self.assertEqual(relative.target_digest, absolute.target_digest)

    def test_dot_segments_and_path_separators_are_normalized(self):
        simple = self.fingerprint("read_file", {"path": "src/app.py"})
        noisy = self.fingerprint("read_file", {"path": ".\\src\\.\\app.py"})
        self.assertEqual(simple.target_digest, noisy.target_digest)

    def test_safe_git_presentation_flags_do_not_change_identity(self):
        short = self.fingerprint("run_command", {"command": "git status --short"})
        alias = self.fingerprint("run_command", {"command": "git --no-pager status -s"})
        self.assertEqual(short.semantic_digest, alias.semantic_digest)

    def test_argument_object_order_is_deterministic(self):
        first = self.fingerprint(
            "search_code", {"query": "needle", "paths": ["src"], "limit": 20}
        )
        second = self.fingerprint(
            "search_code", {"limit": 20, "paths": ["src"], "query": "needle"}
        )
        self.assertEqual(first.exact_digest, second.exact_digest)

    # Repeated provider/plan strategies.
    def test_provider_prompt_cosmetic_wording_is_equivalent_with_plan_id(self):
        first = self.fingerprint(
            "provider_query",
            {"prompt": "Find   the build status", "plan_id": "release-check"},
        )
        second = self.fingerprint(
            "provider_query",
            {"prompt": "find the build status.", "plan_id": "release-check"},
        )
        self.assertIs(
            compare_actions(first, second), EquivalenceLevel.SEMANTICALLY_EQUIVALENT
        )

    def test_failed_plan_cannot_evade_bounds_with_cosmetic_wording(self):
        first = self.fingerprint(
            "provider_query",
            {"prompt": "Inspect auth config", "hypothesis_id": "auth-path"},
            failure_class="provider_timeout",
        )
        second = self.fingerprint(
            "provider_query",
            {"prompt": "inspect  auth config!", "hypothesis_id": "auth-path"},
            failure_class="PROVIDER_TIMEOUT",
        )
        self.assertEqual(first.semantic_digest, second.semantic_digest)

    # Privacy, collision, and conservative safety scenarios.
    def test_public_fingerprint_contains_no_raw_path_prompt_or_secret(self):
        secret = "sk-private-never-store-this"  # pragma: allowlist secret
        action = self.fingerprint(
            "provider_query",
            {
                "prompt": f"Read {self.root / 'private.py'} with {secret}",
                "plan_id": "private-plan",
            },
        )
        encoded = json.dumps(action.to_dict(), sort_keys=True)
        self.assertNotIn(secret, encoded)
        self.assertNotIn("private.py", encoded)
        self.assertNotIn("Read", encoded)

    def test_distinct_search_queries_do_not_collide(self):
        first = self.fingerprint("search_code", {"query": "alpha", "paths": ["src"]})
        second = self.fingerprint("search_code", {"query": "beta", "paths": ["src"]})
        self.assertNotEqual(first.semantic_digest, second.semantic_digest)

    def test_overlapping_file_ranges_are_classified_as_overlapping(self):
        larger = self.fingerprint(
            "read_file", {"path": "src/app.py", "start_line": 1, "end_line": 100}
        )
        smaller = self.fingerprint(
            "read_file", {"path": "src/app.py", "start_line": 20, "end_line": 40}
        )
        self.assertIs(
            compare_actions(larger, smaller), EquivalenceLevel.OVERLAPPING_OR_SUBSUMING
        )

    def test_disjoint_file_ranges_are_related_but_materially_different(self):
        first = self.fingerprint(
            "read_file", {"path": "src/app.py", "start_line": 1, "end_line": 10}
        )
        second = self.fingerprint(
            "read_file", {"path": "src/app.py", "start_line": 20, "end_line": 30}
        )
        self.assertIs(
            compare_actions(first, second),
            EquivalenceLevel.RELATED_MATERIALLY_DIFFERENT,
        )

    def test_mutation_fingerprints_are_never_semantically_suppressible(self):
        action = self.fingerprint(
            "write_file", {"path": "src/app.py", "content": "secret source"}
        )
        self.assertIs(action.side_effect_class, SideEffectClass.LOCAL_MUTATION)
        self.assertFalse(action.suppressible)

    def test_unknown_actions_are_never_semantically_suppressible(self):
        action = self.fingerprint("future_safety_check", {"scope": "all"})
        self.assertIs(action.side_effect_class, SideEffectClass.UNKNOWN)
        self.assertFalse(action.suppressible)

    def test_equivalent_cross_tool_action_is_not_an_exact_duplicate(self):
        native = self.fingerprint("read_file", {"path": "src/app.py"})
        shell = self.fingerprint("run_command", {"command": "cat src/app.py"})
        self.assertNotEqual(native.exact_digest, shell.exact_digest)
        self.assertEqual(native.semantic_digest, shell.semantic_digest)

    def test_mapping_order_property_is_stable_across_shuffles(self):
        items = [("query", "needle"), ("paths", ["src", "tests"]), ("limit", 20)]
        expected = self.fingerprint("search_code", dict(items)).exact_digest
        generator = random.Random(649)
        for _ in range(50):
            generator.shuffle(items)
            self.assertEqual(
                expected,
                self.fingerprint("search_code", dict(items)).exact_digest,
            )

    def test_random_private_values_never_appear_in_public_fingerprint(self):
        generator = random.Random(650)
        for _ in range(50):
            private = "private-" + "".join(
                generator.choice("abcdef0123456789") for _ in range(32)
            )
            action = self.fingerprint("provider_query", {"prompt": private})
            self.assertNotIn(private, json.dumps(action.to_dict()))


class FingerprintExecutor:
    def __init__(self, overrides: dict | None = None) -> None:
        self.overrides = overrides or {}
        self.invocations: list[dict] = []
        self.freshness = "repo-state-a"

    def schemas(self):
        return []

    def action_freshness_boundary(self) -> str:
        return self.freshness

    def invoke_call(self, call, *, cancel=None):
        self.invocations.append(call)
        function = call["function"]
        name = function["name"]
        if name in {"apply_patch", "write_file"}:
            self.freshness = "repo-state-b"
        result = dict(self.overrides.get(name, {"ok": True}))
        result.setdefault("duration_ms", 7)
        result.setdefault("content", json.dumps({"observed": name}))
        return result


def _turn(call_id: str, tool: str, arguments: dict) -> ChatTurn:
    return ChatTurn(
        tool_calls=(
            {
                "id": call_id,
                "function": {"name": tool, "arguments": json.dumps(arguments)},
            },
        )
    )


def _done() -> ChatTurn:
    return ChatTurn(
        content=json.dumps(
            {
                "vesta_decision_version": 1,
                "state": "completed",
                "summary": "done",
                "evidence": [],
            }
        )
    )


def _scripted(*turns: ChatTurn):
    iterator = iter(turns)

    def chat(messages, *, tools):
        return next(iterator)

    return chat


class SemanticRepeatControllerTests(unittest.TestCase):
    def test_controller_blocks_third_cross_tool_equivalent_read(self):
        executor = FingerprintExecutor()
        result = ToolLoopController().run(
            chat=_scripted(
                _turn("r1", "read_file", {"path": "src/app.py"}),
                _turn("r2", "run_command", {"command": "cat src/app.py"}),
                _turn("r3", "read_file", {"path": "src/app.py"}),
            ),
            executor=executor,
            base_messages=[{"role": "user", "content": "inspect app"}],
            allow_mutations=False,
        )
        self.assertIs(result.completion_state, CompletionState.STUCK_NO_PROGRESS)
        self.assertEqual(result.stopped_reason, "repeated_success")
        self.assertEqual(len(executor.invocations), 2)
        self.assertEqual(result.tool_trace[-1]["repeat_decision"], "blocked")
        self.assertEqual(result.repetition["blocked"], 1)
        self.assertEqual(result.repetition["estimated_avoided_latency_ms"], 7)

    def test_controller_requires_fresh_verification_after_repository_change(self):
        executor = FingerprintExecutor()
        result = ToolLoopController().run(
            chat=_scripted(
                _turn("t1", "run_tests", {"command_id": "npm-test"}),
                _turn("p1", "apply_patch", {"patch": "change"}),
                _turn("t2", "run_tests", {"command_id": "npm-test"}),
                _done(),
            ),
            executor=executor,
            base_messages=[{"role": "user", "content": "fix and verify"}],
        )
        self.assertIs(result.completion_state, CompletionState.COMPLETED)
        self.assertEqual(len(executor.invocations), 3)
        verification = result.tool_trace[-1]
        self.assertEqual(verification["repeat_decision"], "required_fresh")

    def test_explicit_repeat_override_is_visible_and_costed(self):
        executor = FingerprintExecutor()
        result = ToolLoopController().run(
            chat=_scripted(
                _turn("r1", "read_file", {"path": "src/app.py"}),
                _turn("r2", "read_file", {"path": "src/app.py"}),
                _turn("r3", "read_file", {"path": "src/app.py"}),
                _done(),
            ),
            executor=executor,
            base_messages=[{"role": "user", "content": "read again explicitly"}],
            allow_mutations=False,
            allow_semantic_repeats=True,
        )
        self.assertIs(result.completion_state, CompletionState.COMPLETED)
        self.assertEqual(len(executor.invocations), 3)
        self.assertEqual(result.repetition["overrides"], 1)
        self.assertEqual(result.repetition["repeated_latency_ms"], 14)
        self.assertEqual(result.tool_trace[-1]["repeat_decision"], "override_allowed")

    def test_semantically_similar_mutations_are_both_executed(self):
        executor = FingerprintExecutor()
        result = ToolLoopController().run(
            chat=_scripted(
                _turn("w1", "write_file", {"path": "src/app.py", "content": "one"}),
                _turn("w2", "write_file", {"path": "src/app.py", "content": "two"}),
                _done(),
            ),
            executor=executor,
            base_messages=[{"role": "user", "content": "edit app"}],
        )
        self.assertIs(result.completion_state, CompletionState.COMPLETED)
        self.assertEqual(len(executor.invocations), 2)
        self.assertTrue(
            all(
                row["repeat_decision"] == "exact_identity_only"
                for row in result.tool_trace
            )
        )

    def test_failed_cross_tool_strategy_cannot_evade_failure_budget(self):
        executor = FingerprintExecutor(
            {
                "search_code": {"ok": False, "error_code": "SEARCH_FAILED"},
                "run_command": {"ok": False, "error_code": "SEARCH_FAILED"},
            }
        )
        result = ToolLoopController(ToolLoopPolicy(max_identical_failures=2)).run(
            chat=_scripted(
                _turn("s1", "search_code", {"query": "needle", "paths": ["src"]}),
                _turn("s2", "run_command", {"command": "rg needle src"}),
            ),
            executor=executor,
            base_messages=[{"role": "user", "content": "find needle"}],
            allow_mutations=False,
        )
        self.assertIs(result.completion_state, CompletionState.STUCK_NO_PROGRESS)
        self.assertEqual(result.stopped_reason, "repeated_failure")
        self.assertEqual(len(executor.invocations), 2)
        self.assertEqual(result.repetition["failed_repeats"], 1)


if __name__ == "__main__":
    unittest.main()
