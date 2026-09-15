"""Data written before the rename to Vesta is still read the same way.

The rename changed names that also live on disk: policy files committed to
repositories, keys inside saved benchmark scores, and the hash domain of
change-attribution evidence. Each test below starts from data exactly as the
pre-rename version wrote it and proves it still loads, verifies and counts.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from vesta import legacy
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


if __name__ == "__main__":
    unittest.main()
