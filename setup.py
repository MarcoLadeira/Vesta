"""Setuptools hooks for immutable OPai distribution build identity."""

from __future__ import annotations

from pathlib import Path
import sys

from setuptools import setup
from setuptools.command.build_py import build_py
from setuptools.command.sdist import sdist

ROOT = Path(__file__).resolve().parent
if sys.path[0] != str(ROOT):
    sys.path.insert(0, str(ROOT))

from opai.build_metadata import write_build_metadata  # noqa: E402


class CanonicalBuildPy(build_py):
    """Add build identity to wheel staging without modifying the checkout."""

    def run(self) -> None:
        version = self.distribution.get_version()
        # Validate before setuptools creates an apparently successful artifact.
        write_build_metadata(
            Path(self.build_lib) / "opai",
            application_version=version,
            source_root=ROOT,
        )
        super().run()
        # Package-data copying may replace the staging directory; write the
        # authoritative generated file last as well.
        write_build_metadata(
            Path(self.build_lib) / "opai",
            application_version=version,
            source_root=ROOT,
        )


class CanonicalSdist(sdist):
    """Carry exact source identity into an sdist's release tree."""

    def make_release_tree(self, base_dir: str, files: list[str]) -> None:
        super().make_release_tree(base_dir, files)
        write_build_metadata(
            Path(base_dir) / "opai",
            application_version=self.distribution.get_version(),
            source_root=ROOT,
        )


setup(cmdclass={"build_py": CanonicalBuildPy, "sdist": CanonicalSdist})
