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

    def test_windows_installer_supports_one_command_remote_bootstrap(self):
        text = Path("install.ps1").read_text(encoding="utf-8")

        self.assertIn("OPAI_REPO_URL", text)
        self.assertIn(".opai", text)
        self.assertIn("source", text)
        self.assertIn("git clone", text)
        self.assertIn("--shell-aliases", text)
        self.assertIn("NoShellAliases", text)
        self.assertIn("NoSuperpowers", text)
        self.assertIn("OPAI_PROJECT_ROOT", text)
        self.assertIn('"install", "--project", $ProjectRoot', text)

    def test_posix_installer_supports_one_command_remote_bootstrap(self):
        text = Path("install.sh").read_text(encoding="utf-8")

        self.assertIn("OPAI_REPO_URL", text)
        self.assertIn(".opai/source", text)
        self.assertIn("OPAI_PROJECT_ROOT", text)
        self.assertIn("git clone", text)
        self.assertIn("--shell-aliases", text)
        self.assertIn("--no-shell-aliases", text)
        self.assertIn("--no-superpowers", text)
        self.assertIn('-m opai install --project "$OPAI_PROJECT_ROOT"', text)


if __name__ == "__main__":
    unittest.main()
