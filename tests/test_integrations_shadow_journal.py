"""#613 Stage 2: connected-service consent mirrors into the shadow journal.

``vesta/integrations.py`` is Stage 1's "approvals: connected-service consent".
``~/.vesta/global.json`` records which AI clients the user has agreed to let
Vesta manage, so losing or silently altering it changes what Vesta is permitted
to touch on someone's machine.

One placement decision worth pinning: the mirror sits at the manifest call
site, **not** inside the shared ``_write`` helper. ``_write`` also emits
instruction files (CLAUDE.md, GEMINI.md, registry.yaml and friends), which
Stage 1 classifies as generated content rather than runtime truth. Mirroring
every ``_write`` would journal documents #613 explicitly excludes and bury the
one record that matters.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from vesta.integrations import (
    install_global_integrations,
    load_global_status,
    vesta_home,
)
from vestahub import shadow_journal


class ConsentManifestShadowTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name) / "home"
        self.home.mkdir(parents=True, exist_ok=True)
        self.project = Path(self._tmp.name) / "project"
        self.project.mkdir(parents=True, exist_ok=True)

    def _manifest_path(self) -> Path:
        return vesta_home(self.home) / "global.json"

    def _install(self, targets: list[str]) -> dict:
        return install_global_integrations(
            self.project,
            home=self.home,
            targets=targets,
            ensure_superpowers=False,
        )

    def _read_legacy(self) -> dict:
        try:
            return json.loads(self._manifest_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def test_granting_consent_is_mirrored(self):
        self._install(["claude"])

        projection = shadow_journal.projection(self._manifest_path())

        self.assertIn("claude", projection["targets"])

    def test_the_mirror_agrees_with_the_manifest(self):
        self._install(["claude"])

        report = shadow_journal.contradiction_report(
            self._manifest_path(), self._read_legacy
        )

        self.assertIsNone(report)

    def test_targets_accumulate_on_both_sides(self):
        """install merges with existing consent rather than replacing it."""
        self._install(["claude"])
        self._install(["codex"])

        projection = shadow_journal.projection(self._manifest_path())

        self.assertIn("claude", projection["targets"])
        self.assertIn("codex", projection["targets"])
        self.assertEqual(
            sorted(projection["targets"]),
            sorted(load_global_status(self.home)["targets"]),
        )

    def test_instruction_files_are_not_journalled(self):
        """Only the consent manifest is runtime truth.

        `_write` also emits generated instruction files; journalling those
        would record documents Stage 1 classifies as NOT_RUNTIME_STATE.
        """
        self._install(["claude"])

        journal_dir = shadow_journal.journal_path_for(self._manifest_path()).parent
        journals = sorted(p.name for p in journal_dir.glob("*.jsonl"))

        self.assertEqual(journals, ["global.journal.jsonl"])

    def test_an_out_of_band_consent_change_is_reported(self):
        self._install(["claude"])
        tampered = {**self._read_legacy(), "targets": ["claude", "codex", "gemini"]}
        self._manifest_path().write_text(json.dumps(tampered), encoding="utf-8")

        report = shadow_journal.contradiction_report(
            self._manifest_path(), self._read_legacy
        )

        self.assertIsNotNone(report)
        self.assertIn("targets", report["mismatched_fields"])

    def test_an_untouched_home_agrees_as_both_empty(self):
        self.assertEqual(shadow_journal.projection(self._manifest_path()), {})
        self.assertIsNone(
            shadow_journal.contradiction_report(self._manifest_path(), lambda: {})
        )


if __name__ == "__main__":  # pragma: no cover - convenience
    unittest.main()
