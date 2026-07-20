"""Project state survives interrupted/torn writes (#473)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from opaihub.state import (
    _state_backup_path,
    load_state,
    save_state,
    state_path,
)


class ProjectStateAtomicWriteTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_save_is_atomic_and_leaves_no_temp_files(self):
        state = load_state(self.root)
        save_state(self.root, state)
        leftovers = list(state_path(self.root).parent.glob("*.tmp"))
        self.assertEqual(leftovers, [])
        # A saved file is always complete, parseable JSON (never torn).
        json.loads(state_path(self.root).read_text(encoding="utf-8"))

    def test_save_keeps_a_last_known_good_backup(self):
        save_state(self.root, load_state(self.root))
        self.assertTrue(_state_backup_path(self.root).exists())

    def test_torn_primary_recovers_config_and_schema_from_backup(self):
        state = load_state(self.root)
        state["profile"]["mode"] = "custom-mode"
        state["notes"] = ["keep me"]
        save_state(self.root, state)

        # Simulate an interrupted write leaving a torn primary file.
        state_path(self.root).write_text("{ torn write", encoding="utf-8")
        recovered = load_state(self.root)

        self.assertEqual(recovered["profile"]["mode"], "custom-mode")
        self.assertEqual(recovered["notes"], ["keep me"])
        self.assertEqual(recovered["schema_version"], 1)

    def test_torn_primary_and_backup_falls_back_to_defaults_with_schema(self):
        save_state(self.root, load_state(self.root))
        state_path(self.root).write_text("{corrupt", encoding="utf-8")
        _state_backup_path(self.root).write_text("also corrupt {", encoding="utf-8")
        recovered = load_state(self.root)
        # Never crashes and always carries the current schema version.
        self.assertEqual(recovered["schema_version"], 1)
        self.assertIn("profile", recovered)


if __name__ == "__main__":
    unittest.main()
