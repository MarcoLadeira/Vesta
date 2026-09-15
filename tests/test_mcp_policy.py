"""Generated MCP configs enforce path and write policy (#15)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from vestahub.mcp import (
    render_mcp_config,
    validate_mcp_config,
    write_mcp_config,
)
from vestahub.state import set_mcp

from tests._helpers import make_repo


class FilesystemPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = make_repo(Path(self._tmp.name))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _filesystem(self) -> dict:
        config = render_mcp_config(self.root)
        return config["mcpServers"]["filesystem"]

    def test_filesystem_server_is_read_only_by_default(self):
        fs = self._filesystem()
        self.assertTrue(fs["policy"]["pathScoped"])
        self.assertTrue(fs["policy"]["readOnly"])
        self.assertFalse(fs["policy"]["writeEnabled"])

    def test_git_env_secrets_and_caches_are_blocked(self):
        forbidden = self._filesystem()["policy"]["forbiddenPaths"]
        names = {Path(item).name for item in forbidden} | set(forbidden)
        for blocked in (".git", ".env", "node_modules", ".vestahub", ".opcoding"):
            self.assertIn(blocked, names, blocked)

    def test_filesystem_roots_are_scoped_to_the_project(self):
        fs = self._filesystem()
        # The last arg(s) are the accessible roots — must be the project root,
        # never a system-wide directory.
        roots = [a for a in fs["args"] if a.startswith(str(self.root.resolve()))]
        self.assertTrue(roots)
        self.assertNotIn("/", fs["args"])
        self.assertNotIn("C:\\", fs["args"])

    def test_write_enabled_overlay_flips_read_only_off(self):
        # A project can opt into writes explicitly; nothing else can.
        config = render_mcp_config(self.root)
        # Simulate an overlay entry with write_enabled by validating the policy
        # logic directly through a crafted config.
        fs = config["mcpServers"]["filesystem"]
        fs["policy"]["writeEnabled"] = True
        fs["policy"]["readOnly"] = False
        report = validate_mcp_config(config)
        self.assertTrue(report["ok"], report["violations"])

    def test_generated_config_passes_its_own_validator(self):
        report = validate_mcp_config(render_mcp_config(self.root))
        self.assertTrue(report["ok"], report["violations"])


class ValidatorTests(unittest.TestCase):
    def test_writable_path_server_without_opt_in_is_a_violation(self):
        config = {
            "mcpServers": {
                "filesystem": {
                    "command": "npx",
                    "args": [],
                    "policy": {
                        "pathScoped": True,
                        "readOnly": False,
                        "writeEnabled": False,
                        "forbiddenPaths": [".git", ".env"],
                    },
                }
            }
        }
        report = validate_mcp_config(config)
        self.assertFalse(report["ok"])
        codes = {v["code"] for v in report["violations"]}
        self.assertIn("WRITE_WITHOUT_OPT_IN", codes)

    def test_missing_git_or_env_block_is_a_violation(self):
        config = {
            "mcpServers": {
                "filesystem": {
                    "command": "npx",
                    "args": [],
                    "policy": {
                        "pathScoped": True,
                        "readOnly": True,
                        "writeEnabled": False,
                        "forbiddenPaths": ["node_modules"],
                    },
                }
            }
        }
        report = validate_mcp_config(config)
        self.assertFalse(report["ok"])
        codes = {v["code"] for v in report["violations"]}
        self.assertIn("MISSING_FORBIDDEN_PATH", codes)

    def test_non_path_scoped_servers_are_not_flagged(self):
        config = {
            "mcpServers": {
                "web-search": {
                    "command": "x",
                    "args": [],
                    "policy": {"pathScoped": False, "readOnly": False},
                }
            }
        }
        self.assertTrue(validate_mcp_config(config)["ok"])


class RenderingTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = make_repo(Path(self._tmp.name))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_every_enabled_server_carries_a_policy_block(self):
        config = render_mcp_config(self.root)
        self.assertTrue(config["mcpServers"])
        for server in config["mcpServers"].values():
            self.assertIn("policy", server)
            self.assertIn("permissionLevel", server["policy"])

    def test_requires_confirmation_is_preserved_not_dropped(self):
        # git MCP declares confirmation-required actions; they must survive.
        set_mcp(self.root, "git", enabled=True)
        git = render_mcp_config(self.root)["mcpServers"]["git"]
        self.assertIn("push", git["policy"]["requiresConfirmation"])

    def test_written_config_is_valid_on_disk(self):
        import json

        path = write_mcp_config(self.root)
        config = json.loads(path.read_text(encoding="utf-8"))
        self.assertTrue(validate_mcp_config(config)["ok"])
        # The header policy names the enforcement.
        self.assertIn("read-only", config["policy"])


if __name__ == "__main__":
    unittest.main()
