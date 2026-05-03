import tempfile
import tomllib
import unittest
from pathlib import Path

from opai.integrations import activate_project, project_status
from opai.publish import publish_status


class PublishReadinessTests(unittest.TestCase):
    def test_op_command_is_the_opai_cli(self):
        data = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
        scripts = data["project"]["scripts"]

        self.assertEqual(scripts["op"], "opai.cli:main")
        self.assertEqual(scripts["opai"], "opai.cli:main")
        self.assertEqual(scripts["opcoding"], "opcoding.cli:main")

    def test_activation_dry_run_does_not_write_project_files(self):
        with tempfile.TemporaryDirectory() as project_tmp:
            project = Path(project_tmp)
            result = activate_project(project, install_global=False, dry_run=True)

            self.assertEqual(result["status"], "planned")
            self.assertFalse((project / "AGENTS.md").exists())
            self.assertFalse((project / ".opaihub" / "project.json").exists())

    def test_project_status_reports_activation_and_superpowers(self):
        with (
            tempfile.TemporaryDirectory() as project_tmp,
            tempfile.TemporaryDirectory() as home_tmp,
        ):
            project = Path(project_tmp)
            home = Path(home_tmp)
            source = home / ".codex" / "superpowers" / "skills"
            (source / "using-superpowers").mkdir(parents=True)
            (source / "using-superpowers" / "SKILL.md").write_text(
                "---\nname: using-superpowers\n---\n", encoding="utf-8"
            )

            activate_project(project, home=home, install_global=True)
            status = project_status(project, home=home)

            self.assertTrue(status["project"]["activated"])
            self.assertTrue(status["global"]["installed"])
            self.assertTrue(status["superpowers"]["enabled"])

    def test_publish_status_detects_missing_local_git_root(self):
        with tempfile.TemporaryDirectory() as project_tmp:
            status = publish_status(Path(project_tmp))

            self.assertFalse(status["git"]["is_repo_root"])
            self.assertIn("git init -b main", status["next_steps"])


if __name__ == "__main__":
    unittest.main()
