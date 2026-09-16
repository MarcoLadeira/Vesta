"""#613 Stage 2: the dual reads three mirrored modules were shipped without.

Stage 2 is shadow-write *plus* dual-read. Three modules got the first half and
not the second: ``session_registry``, ``vesta/integrations`` and
``vesta/update/storage`` were each mirroring correctly, with nothing able to ask
at runtime whether the mirror still agreed with the file. An unverified shadow
is just a second copy to go stale, and Stage 4 cannot qualify a cutover on real
traffic without a comparator, so the gap mattered more than it looked.

``session_registry`` is the one with a real reason behind the omission, and it
is worth stating because the obvious fix is wrong. Its mirror deliberately
drops ``started_at`` -- wall-clock at write time, so mirroring it would make
every rewrite differ from the file. Feed that record to the generic comparator
and it reports a contradiction on *every healthy session*. A report that cries
wolf is worse than no report, so the module ended up with neither. The fix
drops the field on both sides, and these tests pin both directions: healthy
sessions stay quiet, and a real divergence is still caught.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from _helpers import isolated_home

from vesta import integrations
from vesta.integrations import install_global_integrations, vesta_home
from vesta.update.models import UpdatePolicy
from vesta.update.storage import UpdaterPaths, UpdateStore
from vestahub.session_registry import SessionRegistry


class SessionRegistryDualReadTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.registry = SessionRegistry(durable_root=self.root, process_id=4242)

    def _path(self, request_id: str) -> Path:
        return self.registry._durable_path(request_id)

    def test_a_running_session_is_mirrored_and_agrees(self):
        self.registry.start("req-1", "claude")

        shadow = self.registry.session_shadow_projection("req-1")

        self.assertEqual(shadow["pid"], 4242)
        self.assertEqual(shadow["provider"], "claude")
        self.assertIsNone(self.registry.session_contradiction_report("req-1"))

    def test_a_healthy_session_is_quiet_despite_the_started_at_asymmetry(self):
        """The reason this module went without a dual read.

        ``started_at`` is on the file and deliberately not in the mirror. A
        comparator that did not account for that would fire here -- on a
        perfectly healthy session -- and every real finding would be lost in
        the noise.
        """

        self.registry.start("req-1", "claude")

        stored = json.loads(self._path("req-1").read_text(encoding="utf-8"))

        self.assertIn("started_at", stored, "the file really does carry it")
        self.assertNotIn(
            "started_at",
            self.registry.session_shadow_projection("req-1"),
            "the mirror really does not",
        )
        self.assertIsNone(self.registry.session_contradiction_report("req-1"))

    def test_an_out_of_band_provider_change_is_still_reported(self):
        """Teeth: dropping one field must not blind the comparator to the rest."""

        self.registry.start("req-1", "claude")
        path = self._path("req-1")
        tampered = {
            **json.loads(path.read_text(encoding="utf-8")),
            "provider": "somebody-else",
        }
        path.write_text(json.dumps(tampered), encoding="utf-8")

        report = self.registry.session_contradiction_report("req-1")

        self.assertIsNotNone(report)
        self.assertIn("provider", report["mismatched_fields"])
        self.assertEqual(report["request_id"], "req-1")

    def test_a_finished_session_agrees_as_both_absent(self):
        """The tombstone's whole job: a finished session is quiet, not missing."""

        self.registry.start("req-1", "claude")
        self.registry.finish("req-1")

        self.assertEqual(self.registry.session_shadow_projection("req-1"), {})
        self.assertIsNone(self.registry.session_contradiction_report("req-1"))

    def test_a_lost_session_file_is_reported_against_a_surviving_shadow(self):
        self.registry.start("req-1", "claude")
        self._path("req-1").unlink()

        report = self.registry.session_contradiction_report("req-1")

        self.assertIsNotNone(report)
        self.assertEqual(report["legacy"], {})
        self.assertEqual(report["shadow"]["provider"], "claude")

    def test_a_registry_with_no_durable_root_reports_nothing(self):
        """Durability is optional here; the accessors must not assume it."""

        volatile = SessionRegistry()

        self.assertEqual(volatile.session_shadow_projection("req-1"), {})
        self.assertIsNone(volatile.session_contradiction_report("req-1"))


class IntegrationsDualReadTests(unittest.TestCase):
    def _install(self, home: Path, targets: list[str]) -> dict:
        project = Path(home) / "project"
        project.mkdir(parents=True, exist_ok=True)
        return install_global_integrations(
            project, home=Path(home), targets=targets, ensure_superpowers=False
        )

    def test_granting_consent_is_mirrored_and_agrees(self):
        with isolated_home() as home:
            self._install(Path(home), ["claude"])

            shadow = integrations.global_manifest_projection(Path(home))

            self.assertIsInstance(shadow.get("targets"), list)
            self.assertIsNone(
                integrations.global_manifest_contradiction_report(Path(home))
            )

    def test_an_out_of_band_consent_change_is_reported(self):
        """The scenario #613 exists for: consent edited behind our back."""

        with isolated_home() as home:
            self._install(Path(home), ["claude"])
            manifest_path = vesta_home(Path(home)) / "global.json"
            tampered = {
                **json.loads(manifest_path.read_text(encoding="utf-8")),
                "targets": ["something-nobody-approved"],
            }
            manifest_path.write_text(json.dumps(tampered), encoding="utf-8")

            report = integrations.global_manifest_contradiction_report(Path(home))

            self.assertIsNotNone(report)
            self.assertIn("targets", report["mismatched_fields"])

    def test_an_untouched_home_agrees_as_both_empty(self):
        with isolated_home() as home:
            self.assertEqual(integrations.global_manifest_projection(Path(home)), {})
            self.assertIsNone(
                integrations.global_manifest_contradiction_report(Path(home))
            )


class UpdateStorageDualReadTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name)
        self.store = UpdateStore(UpdaterPaths.for_home(self.home))

    def test_each_mirrored_document_has_its_own_dual_read(self):
        self.store.save_policy(UpdatePolicy(automatic_downloads=True))
        self.store.record_metadata_version("stable", 7)

        self.assertTrue(self.store.document_projection("policy"))
        self.assertTrue(self.store.document_projection("trust"))
        self.assertIsNone(self.store.document_contradiction_report("policy"))
        self.assertIsNone(self.store.document_contradiction_report("trust"))

    def test_an_out_of_band_policy_change_is_reported(self):
        self.store.save_policy(UpdatePolicy(automatic_downloads=True))
        path = self.store.paths.policy
        tampered = {
            **json.loads(path.read_text(encoding="utf-8")),
            "automatic_downloads": False,
        }
        path.write_text(json.dumps(tampered), encoding="utf-8")

        report = self.store.document_contradiction_report("policy")

        self.assertIsNotNone(report)
        self.assertIn("automatic_downloads", report["mismatched_fields"])
        self.assertEqual(report["document"], "policy")

    def test_a_rolled_back_metadata_floor_is_reported(self):
        """The anti-rollback high-water mark is the value worth watching here."""

        self.store.record_metadata_version("stable", 7)
        path = self.store.paths.trust
        path.write_text(
            json.dumps({"schema_version": 1, "metadata_versions": {"stable": 1}}),
            encoding="utf-8",
        )

        report = self.store.document_contradiction_report("trust")

        self.assertIsNotNone(report)
        self.assertIn("metadata_versions", report["mismatched_fields"])

    def test_an_unknown_document_is_refused_rather_than_guessed(self):
        with self.assertRaises(ValueError):
            self.store.document_contradiction_report("lease")

    def test_an_untouched_store_agrees_as_both_empty(self):
        self.assertEqual(self.store.document_projection("operation"), {})
        self.assertIsNone(self.store.document_contradiction_report("operation"))


if __name__ == "__main__":  # pragma: no cover - convenience
    unittest.main()
