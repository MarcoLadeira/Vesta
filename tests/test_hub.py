import unittest
from pathlib import Path

from opaihub.loader import hub_root, registry_items


class HubRegistryTests(unittest.TestCase):
    def test_hub_root_exists(self):
        self.assertTrue((hub_root(Path.cwd()) / "registry").exists())

    def test_tool_registry_loads(self):
        tools = registry_items("tools", Path.cwd())
        ids = {tool["id"] for tool in tools}
        self.assertIn("opcoding-cli", ids)
        self.assertIn("morph-fast-apply", ids)

    def test_agent_registry_contains_router(self):
        agents = registry_items("agents", Path.cwd())
        self.assertIn("hub-router", {agent["id"] for agent in agents})

    def test_workflows_include_security_audit(self):
        workflows = registry_items("workflows", Path.cwd())
        self.assertIn("security_audit", {workflow["id"] for workflow in workflows})


if __name__ == "__main__":
    unittest.main()
