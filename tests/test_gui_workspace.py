"""Tests for workspace switching persistence (Qt-free).

Uses isolated_home so the developer's real ~/.opai/gui_workspaces.json is never
touched and the recents file is hermetic.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from _helpers import isolated_home

from opai.gui_workspace import (
    add_recent_workspace,
    is_valid_workspace,
    load_recent_workspaces,
    resolve_gui_workspace,
    workspace_label,
)
from opaihub.app_scaffold import scaffold_app


class ValidationTests(unittest.TestCase):
    def test_valid_for_dir_invalid_for_file_or_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertTrue(is_valid_workspace(root))
            f = root / "a.txt"
            f.write_text("x", encoding="utf-8")
            self.assertFalse(is_valid_workspace(f))
            self.assertFalse(is_valid_workspace(root / "missing"))

    def test_label_uses_parent_and_name(self):
        self.assertEqual(workspace_label("/home/me/project"), "me/project")

    def test_scaffolded_app_remains_selected_inside_an_enclosing_git_repo(self):
        from _helpers import make_repo

        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(Path(tmp))
            app = Path(scaffold_app(repo, "a notes app").root)

            self.assertEqual(resolve_gui_workspace(app), app.resolve())

    def test_ordinary_nested_folder_still_resolves_to_the_git_workspace(self):
        from _helpers import make_repo

        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(Path(tmp))
            nested = repo / "packages" / "plain"
            nested.mkdir(parents=True)

            self.assertEqual(resolve_gui_workspace(nested), repo.resolve())


class RecentsTests(unittest.TestCase):
    def test_add_then_load_roundtrips(self):
        with isolated_home(), tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            add_recent_workspace(root)
            recents = load_recent_workspaces()
        self.assertIn(str(root), recents)

    def test_most_recent_first_and_deduped(self):
        with (
            isolated_home(),
            tempfile.TemporaryDirectory() as a,
            tempfile.TemporaryDirectory() as b,
        ):
            ra, rb = str(Path(a).resolve()), str(Path(b).resolve())
            add_recent_workspace(ra)
            add_recent_workspace(rb)
            add_recent_workspace(ra)  # re-adding moves it to the front, no dup
            recents = load_recent_workspaces()
        self.assertEqual(recents[0], ra)
        self.assertEqual(recents.count(ra), 1)

    def test_invalid_path_is_ignored(self):
        with isolated_home():
            before = load_recent_workspaces()
            add_recent_workspace("/definitely/not/here/xyz")
            after = load_recent_workspaces()
        self.assertEqual(before, after)

    def test_load_drops_paths_that_no_longer_exist(self):
        with isolated_home():
            with tempfile.TemporaryDirectory() as tmp:
                root = str(Path(tmp).resolve())
                add_recent_workspace(root)
                self.assertIn(root, load_recent_workspaces())
            # tmp now deleted -> pruned on next load
            self.assertNotIn(root, load_recent_workspaces())


if __name__ == "__main__":
    unittest.main()
