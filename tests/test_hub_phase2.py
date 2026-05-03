import tempfile
import unittest
from pathlib import Path

from opaihub.dashboard import build_dashboard
from opaihub.local_models import discover_local_models
from opaihub.mcp import render_mcp_config
from opaihub.registry_writer import add_tool_entry
from opaihub.state import (
    attach_project,
    effective_mcp_servers,
    effective_tools,
    load_state,
    set_mcp,
    set_tool,
)
from opaihub.validator import validate_all
from opaihub.workflow_runner import workflow_plan


class HubPhase2Tests(unittest.TestCase):
    def test_project_overlay_enable_disable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            attach_project(root)
            set_tool(root, "morph-fast-apply", enabled=False)
            set_tool(root, "playwright-optional", enabled=True)
            state = load_state(root)
            self.assertIn("morph-fast-apply", state["disabled_tools"])
            self.assertIn("playwright-optional", state["enabled_tools"])
            tools = {
                tool["id"]: tool["effective_enabled"] for tool in effective_tools(root)
            }
            self.assertFalse(tools["morph-fast-apply"])
            self.assertTrue(tools["playwright-optional"])

    def test_mcp_overlay_and_render(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            attach_project(root)
            set_mcp(root, "github", enabled=False)
            servers = {
                server["id"]: server["effective_enabled"]
                for server in effective_mcp_servers(root)
            }
            self.assertFalse(servers["github"])
            rendered = render_mcp_config(root)
            self.assertIn("filesystem", rendered["mcpServers"])
            self.assertNotIn("github", rendered["mcpServers"])

    def test_registry_validation_is_clean(self):
        result = validate_all(Path.cwd())
        self.assertTrue(result["ok"])

    def test_workflow_plan_is_safe_by_default(self):
        plan = workflow_plan(Path.cwd(), "new_project_onboarding")
        self.assertTrue(plan["ok"])
        self.assertTrue(any(step["executable"] for step in plan["steps"]))

    def test_dashboard_and_local_model_discovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            attach_project(root)
            dashboard = build_dashboard(root)
            self.assertTrue(dashboard.exists())
            models = discover_local_models(root)
            self.assertIn("available", models)

    def test_tool_add_writes_local_registry_without_duplicate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            registry = root / "hub" / "registry"
            registry.mkdir(parents=True)
            (registry / "tools.yaml").write_text(
                '{"schema_version": 1, "tools": []}\n', encoding="utf-8"
            )
            entry = {
                "id": "demo-tool",
                "name": "Demo Tool",
                "category": "miscellaneous",
                "description": "Demo",
                "status": "optional",
                "type": "local",
                "cost_level": "free",
                "permission_level": "low",
                "local_first": True,
                "open_source": True,
                "requires_api_key": False,
                "env_vars": [],
                "install_command": "",
                "run_command": "",
                "health_check": None,
                "inputs": [],
                "outputs": [],
                "tags": ["demo"],
                "docs_url": "",
                "notes": "",
                "enabled_by_default": False,
            }
            self.assertTrue(add_tool_entry(root, entry)["ok"])
            self.assertFalse(add_tool_entry(root, entry)["ok"])


if __name__ == "__main__":
    unittest.main()
