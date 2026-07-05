from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from opaihub.aci import AgentComputerInterface, Observation
from opaihub.agent_runtime import AgentRuntime, AgentWorkbench, RuntimePhase
from opaihub.diff_review import (
    build_diff_review,
    parse_unified_diff,
    record_diff_decision,
)
from opaihub.mcp_runtime import MCPRuntime
from opaihub.semantic_index import LocalSemanticIndex
from opaihub.workflow_state import (
    WorkflowState,
    load_workflow_state,
    save_workflow_state,
)
from tests._helpers import FakeAccountRunner, make_repo


class SemanticIndexTests(unittest.TestCase):
    def test_index_is_local_deterministic_and_persists_no_source_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(
                Path(tmp),
                files={
                    "src/auth.py": (
                        "def validate_auth_token(token):\n"
                        "    distinctive_secret_free_source = 'session validator'\n"
                        "    return bool(token)\n"
                    ),
                    ".env": "API_KEY=not-for-indexing\n",
                    "node_modules/pkg/index.js": "authentication token\n",
                },
                commit=True,
            )
            index = LocalSemanticIndex(root, dimension=64, chunk_lines=20)
            first = index.build()
            raw = index.path.read_text(encoding="utf-8")
            second = index.build()

        self.assertEqual(first["model"], "opai-local-hash-v1")
        self.assertGreaterEqual(first["chunks"], 1)
        self.assertEqual(first["index_hash"], second["index_hash"])
        self.assertNotIn("distinctive_secret_free_source", raw)
        self.assertNotIn("not-for-indexing", raw)
        self.assertIn('"path": "src/auth.py"', raw)

    def test_semantic_search_returns_bounded_current_source_with_provenance(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(
                Path(tmp),
                files={
                    "src/auth.py": "def validate_auth_token(token):\n    return token is not None\n",
                    "src/cache.py": "def evict_cache_entry(key):\n    return key\n",
                },
                commit=True,
            )
            index = LocalSemanticIndex(root, dimension=64, chunk_lines=20)
            index.build()
            matches = index.search(
                "authentication token validation", limit=1, max_chars=80
            )

        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["path"], "src/auth.py")
        self.assertEqual(matches[0]["start_line"], 1)
        self.assertGreater(matches[0]["score"], 0)
        self.assertLessEqual(len(matches[0]["text"]), 80)
        self.assertEqual(matches[0]["model"], "opai-local-hash-v1")
        self.assertTrue(matches[0]["content_hash"])

    def test_stale_chunks_are_not_returned_until_reindexed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(
                Path(tmp),
                files={"auth.py": "def auth_token():\n    return True\n"},
                commit=True,
            )
            index = LocalSemanticIndex(root, dimension=64)
            index.build()
            (root / "auth.py").write_text(
                "def billing_invoice():\n    return True\n", encoding="utf-8"
            )

            self.assertEqual(index.search("auth token", limit=3), [])
            index.build()
            matches = index.search("billing invoice", limit=3)

        self.assertEqual(matches[0]["path"], "auth.py")

    def test_aci_exposes_index_and_search_as_structured_observations(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(
                Path(tmp),
                files={"router.py": "def route_request():\n    pass\n"},
                commit=True,
            )
            aci = AgentComputerInterface(root)
            built = aci.build_semantic_index()
            found = aci.semantic_search("request routing", limit=2)

        self.assertIsInstance(built, Observation)
        self.assertEqual(built.kind, "semantic_index")
        self.assertTrue(built.ok)
        self.assertEqual(found.kind, "semantic_search")
        self.assertEqual(found.data["matches"][0]["path"], "router.py")


class FakeMCPClient:
    def __init__(self, tools, result=None):
        self.tools = tools
        self.result = result or {"content": [{"type": "text", "text": "ok"}]}
        self.calls = []

    def list_tools(self, *, cancel=None):
        return self.tools

    def call_tool(self, name, arguments, *, cancel=None):
        self.calls.append((name, arguments, cancel))
        return self.result


def mcp_server(server_id="filesystem", *, enabled=True, transport="stdio"):
    return {
        "id": server_id,
        "name": server_id.title(),
        "description": "test server",
        "transport": transport,
        "effective_enabled": enabled,
        "permission_level": "medium",
        "requires_confirmation": [],
    }


class MCPRuntimeTests(unittest.TestCase):
    def test_discovery_only_exposes_enabled_team_approved_tools(self):
        approved = FakeMCPClient(
            [
                {
                    "name": "read_file",
                    "description": "Read",
                    "annotations": {"readOnlyHint": True},
                }
            ]
        )
        disabled = FakeMCPClient(
            [{"name": "read_secret", "annotations": {"readOnlyHint": True}}]
        )
        with tempfile.TemporaryDirectory() as tmp:
            runtime = MCPRuntime(
                Path(tmp),
                clients={"filesystem": approved, "other": disabled},
                servers=[mcp_server(), mcp_server("other", enabled=False)],
                team_policy={"approved_mcp_servers": ["filesystem"]},
            )
            result = runtime.discover()

        self.assertTrue(result["ok"])
        self.assertEqual([item["name"] for item in result["tools"]], ["read_file"])
        self.assertTrue(result["tools"][0]["read_only"])
        self.assertEqual(result["blocked_servers"], ["other"])

    def test_read_tool_is_mockable_and_result_is_recursively_redacted(self):
        fake_token = "ghp_" + ("a" * 28)
        client = FakeMCPClient(
            [{"name": "read_file", "annotations": {"readOnlyHint": True}}],
            result={"content": [{"text": f"token={fake_token}"}]},
        )
        with tempfile.TemporaryDirectory() as tmp:
            runtime = MCPRuntime(
                Path(tmp), clients={"filesystem": client}, servers=[mcp_server()]
            )
            result = runtime.invoke("filesystem", "read_file", {"path": "app.py"})

        self.assertTrue(result["ok"])
        self.assertEqual(client.calls[0][0], "read_file")
        self.assertNotIn(fake_token, json.dumps(result))

    def test_write_destructive_remote_and_cancelled_calls_fail_closed(self):
        tools = [
            {"name": "write_file", "annotations": {"readOnlyHint": False}},
            {
                "name": "delete_file",
                "annotations": {"readOnlyHint": False, "destructiveHint": True},
            },
        ]
        local = FakeMCPClient(tools)
        remote = FakeMCPClient(
            [{"name": "list_issues", "annotations": {"readOnlyHint": True}}]
        )
        with tempfile.TemporaryDirectory() as tmp:
            runtime = MCPRuntime(
                Path(tmp),
                clients={"filesystem": local, "github": remote},
                servers=[mcp_server(), mcp_server("github", transport="http")],
            )
            write = runtime.invoke("filesystem", "write_file", {})
            destructive = runtime.invoke(
                "filesystem", "delete_file", {}, allow_write=True
            )
            remote_result = runtime.invoke("github", "list_issues", {})
            cancel = threading.Event()
            cancel.set()
            cancelled = runtime.invoke(
                "filesystem", "write_file", {}, allow_write=True, cancel=cancel
            )

        self.assertEqual(write["error_code"], "MCP_WRITE_CONFIRMATION_REQUIRED")
        self.assertEqual(destructive["error_code"], "MCP_DESTRUCTIVE_TOOL")
        self.assertEqual(
            remote_result["error_code"], "MCP_REMOTE_CONFIRMATION_REQUIRED"
        )
        self.assertEqual(cancelled["error_code"], "CANCELLED")
        self.assertEqual(local.calls, [])
        self.assertEqual(remote.calls, [])

    def test_aci_wraps_mcp_discovery_and_invocation_observations(self):
        client = FakeMCPClient(
            [{"name": "read_file", "annotations": {"readOnlyHint": True}}]
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = MCPRuntime(
                root, clients={"filesystem": client}, servers=[mcp_server()]
            )
            aci = AgentComputerInterface(root)
            discovered = aci.discover_mcp_tools(runtime)
            invoked = aci.invoke_mcp_tool(
                runtime, "filesystem", "read_file", {"path": "a.py"}
            )

        self.assertEqual(discovered.kind, "mcp_tools")
        self.assertEqual(invoked.kind, "mcp_tool")
        self.assertTrue(invoked.ok)

    def test_agent_runtime_moves_to_testing_after_mcp_write_observation(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = AgentRuntime(Path(tmp), task="implement")
            runtime.transition(RuntimePhase.INTENT_RESOLVED, message="intent")
            runtime.transition(RuntimePhase.REPO_RESOLVED, message="repo")
            runtime.transition(RuntimePhase.CONTEXT_GATHERING, message="context")
            runtime.transition(RuntimePhase.IMPLEMENTING, message="implement")
            decision = AgentWorkbench(runtime).observe(
                Observation(
                    "mcp_tool",
                    True,
                    {
                        "server_id": "filesystem",
                        "tool": {"name": "write_file", "read_only": False},
                    },
                )
            )

        self.assertEqual(decision.action, "run_focused_tests")
        self.assertEqual(runtime.state.phase, RuntimePhase.TESTING)


class DiffReviewTests(unittest.TestCase):
    def test_unified_diff_becomes_bounded_file_and_hunk_evidence(self):
        parsed = parse_unified_diff(
            "diff --git a/app.py b/app.py\n"
            "--- a/app.py\n+++ b/app.py\n"
            "@@ -1,2 +1,3 @@ route\n"
            "-old = 1\n+new = 2\n+added = True\n context\n"
            "diff --git a/configs/permissions.yaml b/configs/permissions.yaml\n"
            "--- a/configs/permissions.yaml\n+++ b/configs/permissions.yaml\n"
            "@@ -1 +1 @@\n-old\n+new\n",
            max_lines_per_hunk=3,
        )

        self.assertEqual(parsed["summary"]["files"], 2)
        self.assertEqual(parsed["files"][0]["hunks"][0]["new_start"], 1)
        self.assertTrue(parsed["files"][0]["hunks"][0]["truncated"])
        self.assertTrue(parsed["files"][1]["risky"])
        self.assertIn("permissions", parsed["files"][1]["risk_reasons"])

    def test_sensitive_diff_content_is_never_persisted_for_review(self):
        parsed = parse_unified_diff(
            "diff --git a/.env b/.env\n"
            "--- a/.env\n+++ b/.env\n"
            "@@ -0,0 +1 @@\n+API_KEY=short-secret\n"
        )

        self.assertNotIn("short-secret", json.dumps(parsed))
        self.assertTrue(parsed["files"][0]["sensitive"])
        self.assertEqual(
            parsed["files"][0]["hunks"][0]["lines"],
            ["[sensitive diff hidden]"],
        )

    def test_diff_preview_has_a_total_character_budget(self):
        parsed = parse_unified_diff(
            "diff --git a/app.py b/app.py\n"
            "--- a/app.py\n+++ b/app.py\n"
            "@@ -0,0 +1,2 @@\n+" + ("a" * 200) + "\n+" + ("b" * 200) + "\n",
            max_chars=50,
        )
        preview = "".join(parsed["files"][0]["hunks"][0]["lines"])

        self.assertLessEqual(len(preview), 50)
        self.assertTrue(parsed["summary"]["truncated"])

    def test_review_filters_unrelated_changes_and_previews_untracked_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(
                Path(tmp),
                files={"app.py": "print(1)\n", "notes.md": "mine\n"},
                commit=True,
            )
            (root / "app.py").write_text("print(2)\n", encoding="utf-8")
            (root / "notes.md").write_text("unrelated\n", encoding="utf-8")
            (root / "new.py").write_text(
                "def created():\n    return True\n", encoding="utf-8"
            )
            review = build_diff_review(root, include_paths=("app.py", "new.py"))

        paths = [item["path"] for item in review["files"]]
        self.assertEqual(paths, ["app.py", "new.py"])
        self.assertNotIn("notes.md", json.dumps(review))
        self.assertTrue(
            next(item for item in review["files"] if item["path"] == "new.py")[
                "untracked"
            ]
        )

    def test_empty_attribution_does_not_surface_preexisting_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(
                Path(tmp), files={"user-notes.md": "original\n"}, commit=True
            )
            (root / "user-notes.md").write_text("user work\n", encoding="utf-8")

            review = build_diff_review(root, include_paths=())

        self.assertEqual(review["files"], [])
        self.assertEqual(review["summary"]["files"], 0)

    def test_review_decisions_persist_and_rejection_blocks_ship(self):
        review = {
            "files": [
                {"path": "app.py", "decision": "pending", "hunks": []},
                {"path": "tests/test_app.py", "decision": "pending", "hunks": []},
            ],
            "summary": {"files": 2, "pending": 2, "approved": 0, "rejected": 0},
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            save_workflow_state(root, WorkflowState(mode="ship", diff_review=review))
            rejected = record_diff_decision(root, "app.py", "rejected")
            approved = record_diff_decision(root, "app.py", "approved")
            record_diff_decision(root, "tests/test_app.py", "approved")
            final = load_workflow_state(root)

        self.assertTrue(rejected["ok"])
        self.assertIn("Diff review rejected", rejected["workflow"]["blocker"])
        self.assertEqual(approved["workflow"]["diff_review"]["summary"]["approved"], 1)
        self.assertEqual(final.diff_review["summary"]["pending"], 0)
        self.assertEqual(final.diff_review["summary"]["approved"], 2)
        self.assertNotIn("Diff review rejected", final.blocker)
        self.assertEqual(final.merge_status, "pending_checks")

    def test_pipeline_attaches_structured_diff_review_to_workflow(self):
        review = {
            "files": [{"path": "app.py", "decision": "pending", "hunks": []}],
            "summary": {"files": 1, "pending": 1, "approved": 0, "rejected": 0},
        }
        from opaihub.gui_pipeline import handle_gui_message

        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch("opaihub.gui_pipeline.build_diff_review", return_value=review),
        ):
            root = make_repo(Path(tmp), commit=True)
            result = handle_gui_message(
                root,
                "Fix the bug.",
                model_id="account:claude:sonnet",
                mode="safe-auto",
                account_runner=FakeAccountRunner(text="Fixed."),
            )

        self.assertEqual(result["workflow"]["diff_review"]["summary"]["files"], 1)


if __name__ == "__main__":
    unittest.main()
