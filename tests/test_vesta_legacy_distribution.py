"""The old ``opai`` distribution left beside ``vesta`` is cleaned up safely.

pip treats the renamed distribution as a different project, so upgrading an
install leaves both. These tests build a fake environment with the records
both distributions would write and prove startup removes only what belongs
to the old one alone -- never a launcher the two share.
"""

from __future__ import annotations

import tempfile
import unittest
from importlib.metadata import PackageNotFoundError, PackagePath
from pathlib import Path
from unittest import mock

from vesta import legacy


class _FakeDistribution:
    def __init__(self, site: Path, entries: list[str]) -> None:
        self._site = site
        self.files = [PackagePath(entry) for entry in entries]

    def locate_file(self, item) -> Path:
        return self._site / str(item)


class _Environment:
    """``<root>/Lib/site-packages`` and ``<root>/Scripts``, as a Windows venv."""

    def __init__(self, root: Path) -> None:
        self.site = root / "Lib" / "site-packages"
        self.scripts = root / "Scripts"
        self.distributions: dict[str, _FakeDistribution] = {}

    def install(self, name: str, entries: list[str]) -> None:
        for entry in entries:
            path = self.site / entry
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(name, encoding="utf-8")
        self.distributions[name] = _FakeDistribution(self.site, entries)

    def find(self, name: str) -> _FakeDistribution:
        try:
            return self.distributions[name]
        except KeyError:
            raise PackageNotFoundError(name) from None


SHARED = ["../../Scripts/vesta.exe", "../../Scripts/Vesta-Desktop.exe"]
OLD = [
    "opai-0.2.1a1.dist-info/METADATA",
    "opai-0.2.1a1.dist-info/RECORD",
    "opai/__init__.py",
    "opai/update/relaunch.py",
    "../../Scripts/opai.exe",
    "../../Scripts/OPai-Desktop.exe",
    *SHARED,
]
NEW = ["vesta-0.2.1a1.dist-info/RECORD", "vesta/__init__.py", *SHARED]


class LegacyDistributionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.env = _Environment(Path(self._tmp.name))

    def test_old_only_files_go_and_shared_launchers_stay(self):
        self.env.install("opai", OLD)
        self.env.install("vesta", NEW)

        result = legacy.remove_legacy_distribution(distribution=self.env.find)

        self.assertEqual(result["status"], "removed")
        for gone in ("opai.exe", "OPai-Desktop.exe"):
            self.assertFalse((self.env.scripts / gone).exists(), gone)
        self.assertFalse((self.env.site / "opai").exists())
        self.assertFalse((self.env.site / "opai-0.2.1a1.dist-info").exists())
        for kept in ("vesta.exe", "Vesta-Desktop.exe"):
            self.assertTrue((self.env.scripts / kept).exists(), kept)
        self.assertTrue((self.env.site / "vesta" / "__init__.py").exists())
        self.assertTrue(self.env.scripts.is_dir())

    def test_a_file_in_use_keeps_the_metadata_for_the_next_start(self):
        self.env.install("opai", OLD)
        self.env.install("vesta", NEW)
        locked = self.env.scripts / "OPai-Desktop.exe"
        original_unlink = Path.unlink

        def unlink(path, *args, **kwargs):
            if Path(path).name == locked.name:
                raise PermissionError("in use")
            return original_unlink(path, *args, **kwargs)

        with mock.patch.object(Path, "unlink", unlink):
            result = legacy.remove_legacy_distribution(distribution=self.env.find)

        self.assertEqual(result["status"], "partial")
        self.assertTrue((self.env.site / "opai-0.2.1a1.dist-info" / "RECORD").exists())

        retry = legacy.remove_legacy_distribution(distribution=self.env.find)
        self.assertEqual(retry["status"], "removed")
        self.assertFalse(locked.exists())

    def test_nothing_is_removed_unless_vesta_is_installed(self):
        self.env.install("opai", OLD)

        result = legacy.remove_legacy_distribution(distribution=self.env.find)

        self.assertNotEqual(result["status"], "removed")
        self.assertTrue((self.env.scripts / "opai.exe").exists())
        self.assertTrue((self.env.scripts / "vesta.exe").exists())

    def test_a_record_pointing_outside_the_environment_is_ignored(self):
        outside = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(outside, True))
        victim = outside / "notes.txt"
        victim.write_text("mine", encoding="utf-8")
        entries = [*OLD, str(victim)]
        self.env.install("opai", OLD)
        self.env.distributions["opai"] = _FakeDistribution(self.env.site, entries)
        self.env.install("vesta", NEW)

        legacy.remove_legacy_distribution(distribution=self.env.find)

        self.assertTrue(victim.exists())

    def test_absent_when_the_old_distribution_is_not_installed(self):
        self.env.install("vesta", NEW)

        result = legacy.remove_legacy_distribution(distribution=self.env.find)

        self.assertEqual(result["status"], "absent")


if __name__ == "__main__":
    unittest.main()
