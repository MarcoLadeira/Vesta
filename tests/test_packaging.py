import tempfile
import unittest
from pathlib import Path

from opaihub.loader import hub_root, packaged_hub_root, registry_items


class PackagedHubTests(unittest.TestCase):
    def test_packaged_hub_data_contains_required_registries(self):
        root = packaged_hub_root()

        self.assertTrue((root / "registry" / "tools.yaml").exists())
        self.assertTrue((root / "registry" / "agents.yaml").exists())
        self.assertTrue((root / "registry" / "workflows.yaml").exists())
        self.assertTrue((root / "registry" / "mcp_servers.yaml").exists())
        self.assertTrue((root / "registry" / "models.yaml").exists())

    def test_hub_root_falls_back_to_packaged_data_outside_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = hub_root(Path(tmp))

        self.assertEqual(root, packaged_hub_root())
        self.assertTrue((root / "registry" / "tools.yaml").exists())

    def test_registry_items_load_from_packaged_data_outside_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            tools = registry_items("tools", Path(tmp))

        self.assertIn("opai-cli", {tool["id"] for tool in tools})


if __name__ == "__main__":
    unittest.main()
