"""Per-message overhead is bounded and cached (#153)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from vestahub.intent_router import (
    clear_repo_work_cache,
    route_intents,
)
from vestahub.ledger import (
    clear_ledger_summary_cache,
    record_route_decision,
    summarize_ledger,
)

from tests._helpers import make_repo


class LedgerSummaryCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        clear_ledger_summary_cache()

    def tearDown(self) -> None:
        clear_ledger_summary_cache()

    def test_repeated_reads_parse_the_file_once(self):
        import vestahub.ledger as ledger_mod

        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp))
            record_route_decision(root, "task", model_tier="L1")
            calls = {"n": 0}
            real_read = ledger_mod.read_events

            def counting_read(*args, **kwargs):
                calls["n"] += 1
                return real_read(*args, **kwargs)

            with mock.patch.object(ledger_mod, "read_events", counting_read):
                first = summarize_ledger(root)
                second = summarize_ledger(root)
                third = summarize_ledger(root)
            self.assertEqual(calls["n"], 1)
            self.assertEqual(first, second)
            self.assertEqual(second, third)

    def test_cache_invalidates_after_a_new_append(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp))
            record_route_decision(root, "a", model_tier="L1")
            before = summarize_ledger(root)
            self.assertEqual(before["route_count"], 1)

            record_route_decision(root, "b", model_tier="L1")
            after = summarize_ledger(root)
            self.assertEqual(after["route_count"], 2)

    def test_cached_result_is_a_copy_not_shared_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp))
            record_route_decision(root, "task", model_tier="L1")
            first = summarize_ledger(root)
            first["route_count"] = 999
            second = summarize_ledger(root)
            self.assertEqual(second["route_count"], 1)

    def test_empty_ledger_is_handled(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp))
            summary = summarize_ledger(root)
            self.assertEqual(summary["route_count"], 0)


class RouteIntentsOverheadTests(unittest.TestCase):
    def setUp(self) -> None:
        clear_repo_work_cache()
        clear_ledger_summary_cache()

    def tearDown(self) -> None:
        clear_repo_work_cache()
        clear_ledger_summary_cache()

    def test_repo_walk_is_skipped_when_the_repo_is_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), commit=True)
            with mock.patch(
                "vestahub.context_engine.profile_context",
                return_value={"waste_share": 0.1, "estimated_tokens_wasted": 5},
            ) as profile:
                route_intents(root, "fix this bug", mode="safe-auto")
                route_intents(root, "fix another bug", mode="safe-auto")
                route_intents(root, "summarize the repo", mode="safe-auto")
            # Three messages that all trigger profiling, one actual walk.
            self.assertEqual(profile.call_count, 1)

    def test_repo_walk_reruns_after_the_repo_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), commit=True)
            with mock.patch(
                "vestahub.context_engine.profile_context",
                return_value={"waste_share": 0.1, "estimated_tokens_wasted": 5},
            ) as profile:
                route_intents(root, "fix this bug", mode="safe-auto")
                (root / "new_file.py").write_text("x = 1\n", encoding="utf-8")
                route_intents(root, "fix this bug", mode="safe-auto")
            self.assertEqual(profile.call_count, 2)

    def test_test_selection_is_also_gated(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), commit=True)
            with mock.patch(
                "vestahub.test_select.select_tests",
                return_value={"selected_tests": ["a"], "targeted_command": "pytest a"},
            ) as select:
                route_intents(root, "fix the failing test", mode="safe-auto")
                route_intents(root, "fix the bug in the test", mode="safe-auto")
            self.assertEqual(select.call_count, 1)

    def test_cached_row_still_carries_honest_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), commit=True)
            with mock.patch(
                "vestahub.context_engine.profile_context",
                return_value={"waste_share": 0.25, "estimated_tokens_wasted": 42},
            ):
                first = route_intents(root, "fix this bug", mode="safe-auto")
                second = route_intents(root, "fix this bug", mode="safe-auto")
            for trace in (first, second):
                row = next(a for a in trace if a["id"] == "context_profile")
                self.assertEqual(row["result"]["waste_share"], 0.25)
                self.assertEqual(row["result"]["tokens_wasted"], 42)

    def test_a_plain_message_never_profiles_the_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), commit=True)
            with (
                mock.patch("vestahub.context_engine.profile_context") as profile,
                mock.patch("vestahub.test_select.select_tests") as select,
            ):
                trace = route_intents(root, "say hello", mode="safe-auto")
            profile.assert_not_called()
            select.assert_not_called()
            ids = {a["id"] for a in trace}
            self.assertNotIn("context_profile", ids)
            self.assertNotIn("test_select", ids)


if __name__ == "__main__":
    unittest.main()
