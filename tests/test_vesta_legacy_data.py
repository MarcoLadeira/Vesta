"""Data written before the rename to Vesta is still read the same way.

The rename changed names that also live on disk: policy files committed to
repositories, keys inside saved benchmark scores, and the hash domain of
change-attribution evidence. Each test below starts from data exactly as the
pre-rename version wrote it and proves it still loads, verifies and counts.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from vesta import gui_theme, legacy
from vesta.update import factory
from vesta.update.models import UpdateOwner
from vesta.update.storage import UpdaterPaths, UpdateStore
from vestahub import benchmark, change_attribution, team_policy, verification_policy


class LegacyPolicyFileTests(unittest.TestCase):
    def test_a_team_policy_committed_under_the_old_name_stays_in_force(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / legacy.LEGACY_TEAM_POLICY_FILE).write_text(
                "budget:\n  daily_usd: 1.5\n", encoding="utf-8"
            )

            path = team_policy.team_policy_path(root)
            policy = team_policy.load_team_policy(root)

        self.assertEqual(path.name, legacy.LEGACY_TEAM_POLICY_FILE)
        self.assertIsNotNone(policy)

    def test_the_new_name_wins_once_both_exist(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / legacy.LEGACY_TEAM_POLICY_FILE).write_text("{}\n", encoding="utf-8")
            (root / team_policy.TEAM_POLICY_FILE).write_text("{}\n", encoding="utf-8")

            path = team_policy.team_policy_path(root)

        self.assertEqual(path.name, team_policy.TEAM_POLICY_FILE)

    def test_a_verification_policy_under_the_old_name_is_still_applied(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / legacy.LEGACY_VERIFICATION_POLICY_FILE).write_text(
                "schema_version: 1\n", encoding="utf-8"
            )
            overlay, _finding = verification_policy._overlay_from_repository(root)
            missing, _ = verification_policy._overlay_from_repository(
                Path(tempfile.mkdtemp())
            )

        self.assertTrue(overlay is not None or _finding is not None)
        self.assertIsNone(missing)


class LegacyBenchmarkKeyTests(unittest.TestCase):
    def test_a_score_saved_before_the_rename_keeps_its_effectiveness_index(self):
        saved = json.loads('{"opai_effectiveness_index": 62.5}')

        self.assertEqual(benchmark.score_effectiveness_index(saved), 62.5)
        self.assertEqual(
            benchmark.score_effectiveness_index({"vesta_effectiveness_index": 70}), 70
        )
        self.assertIsNone(benchmark.score_effectiveness_index({}))


class FrozenAttributionDigestTests(unittest.TestCase):
    def test_digests_match_evidence_written_before_the_rename(self):
        value = {"path": "a.py", "complete": True}
        canonical = json.dumps(
            value, ensure_ascii=True, sort_keys=True, separators=(",", ":")
        )
        before_rename = hashlib.sha256(
            f"opai:path-identity:v{change_attribution.SCHEMA_VERSION}\0{canonical}".encode(
                "utf-8"
            )
        ).hexdigest()

        self.assertEqual(
            change_attribution._content_digest("path-identity", value), before_rename
        )


class LegacyUpdatePolicyTests(unittest.TestCase):
    def _store_with_saved_policy(self, home: Path) -> UpdateStore:
        root = home / ".vesta" / "updater"
        root.mkdir(parents=True)
        saved = {
            "schema_version": 1,
            "owner": "opai",
            "channel": "beta",
            "automatic_downloads": True,
        }
        (root / "policy.json").write_text(json.dumps(saved), encoding="utf-8")
        return UpdateStore(UpdaterPaths.for_home(home))

    def test_a_policy_saved_before_the_rename_keeps_its_choices(self):
        with tempfile.TemporaryDirectory() as tmp:
            policy = self._store_with_saved_policy(Path(tmp)).load_policy()

        self.assertIs(policy.owner, UpdateOwner.VESTA)
        self.assertEqual(policy.channel, "beta")
        self.assertTrue(policy.automatic_downloads)

    def test_update_settings_can_still_be_changed_and_are_saved_under_the_new_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store_with_saved_policy(Path(tmp))
            policy = store.update_policy(automatic_downloads=False)
            written = json.loads(store.paths.policy.read_text(encoding="utf-8"))

        self.assertFalse(policy.automatic_downloads)
        self.assertEqual(written["owner"], "vesta")
        self.assertEqual(written["channel"], "beta")

    def test_a_machine_policy_deployed_under_the_old_name_still_binds(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(factory.platform, "system", return_value="Windows"),                     mock.patch.dict(os.environ, {"ProgramData": tmp}):
                paths = factory._managed_policy_paths()
            legacy_policy = Path(tmp) / "OPai" / "update-policy.json"
            legacy_policy.parent.mkdir()
            legacy_policy.write_text(
                json.dumps({"schema_version": 1, "disableUpdateChecks": True}),
                encoding="utf-8",
            )
            managed = factory.load_managed_update_configuration(paths=paths)

        self.assertEqual(paths[0], Path(tmp) / "Vesta" / "update-policy.json")
        self.assertIs(managed.get("disable_update_checks"), True)


class LegacyThemeFileTests(unittest.TestCase):
    def test_the_theme_is_read_from_the_old_home_while_it_could_not_be_moved(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".vesta").mkdir()
            (home / ".opai").mkdir()
            (home / ".opai" / "gui_theme.json").write_text(
                json.dumps({"theme": "light"}), encoding="utf-8"
            )
            with mock.patch.object(Path, "home", return_value=home):
                path = gui_theme.theme_path()

        self.assertEqual(path, home / ".opai" / "gui_theme.json")


if __name__ == "__main__":
    unittest.main()
