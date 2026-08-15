"""The desktop release transport must work on every supported Python.

``pyproject.toml`` declares ``requires-python = ">=3.10"``, but
``scripts/desktop_release_transport.py`` imported ``tomllib`` unconditionally
-- standard library only from 3.11. The result was invisible for a while and
then obvious in hindsight:

* every push to ``main`` failed six tests in ``test_desktop_artifacts.py``
  with ``ModuleNotFoundError: No module named 'tomllib'``;
* every pull request went green, because pull requests run only the 3.13 lane
  while ``main`` runs 3.10 *and* 3.13.

So a 3.10-only breakage was structurally impossible to catch before merge.
This file is the smallest guard against the class: it exercises the 3.10 code
path on whatever interpreter happens to be running, by removing the module's
reference to ``tomllib`` the way 3.10 would.
"""

from __future__ import annotations

import tempfile
import unittest
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

_TRANSPORT = (
    Path(__file__).resolve().parents[1] / "scripts" / "desktop_release_transport.py"
)


def _load_transport():
    spec = spec_from_file_location("opai_release_transport_compat_test", _TRANSPORT)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise AssertionError("Could not load desktop_release_transport.py")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ReadsTheProjectVersionWithoutTomllibTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = _load_transport()
        self.repository_root = Path(__file__).resolve().parents[1]
        self._real_tomllib = self.module.tomllib
        self.addCleanup(setattr, self.module, "tomllib", self._real_tomllib)

    def _without_tomllib(self) -> None:
        """Make the module see exactly what a 3.10 interpreter would."""
        self.module.tomllib = None

    def test_both_paths_read_the_same_version_from_the_real_pyproject(self):
        pyproject = self.repository_root / "pyproject.toml"

        with_tomllib = self.module._read_project_version(pyproject)
        self._without_tomllib()
        without_tomllib = self.module._read_project_version(pyproject)

        # Agreement is the point: a fallback that merely returns *something*
        # would still let a release ship with a mismatched identity.
        self.assertTrue(with_tomllib)
        self.assertEqual(with_tomllib, without_tomllib)

    def test_a_version_in_another_table_is_never_mistaken_for_the_project(self):
        self._without_tomllib()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pyproject.toml"
            path.write_text('[tool.other]\nversion = "9.9.9"\n', encoding="utf-8")

            with self.assertRaises(self.module.TransportError):
                self.module._read_project_version(path)

    def test_the_scan_stops_at_the_next_table_header(self):
        self._without_tomllib()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pyproject.toml"
            path.write_text(
                '[project]\nname = "opai"\n\n[tool.x]\nversion = "9.9.9"\n',
                encoding="utf-8",
            )

            # `[project]` exists but carries no version of its own; the 9.9.9
            # belonging to `[tool.x]` must not be borrowed.
            with self.assertRaises(self.module.TransportError):
                self.module._read_project_version(path)

    def test_a_missing_file_fails_closed(self):
        self._without_tomllib()
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(self.module.TransportError):
                self.module._read_project_version(Path(tmp) / "absent.toml")

    def test_a_malformed_version_line_fails_closed(self):
        self._without_tomllib()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pyproject.toml"
            path.write_text("[project]\nversion =\n", encoding="utf-8")

            with self.assertRaises(self.module.TransportError):
                self.module._read_project_version(path)


if __name__ == "__main__":  # pragma: no cover - convenience
    unittest.main()
