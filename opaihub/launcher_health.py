"""Read what OPai's installed launchers will actually run.

An installed OPai is reached through small launcher programs pip generates
from the entry points in ``pyproject.toml``: ``opai.exe``, ``op.exe``, and the
windowed ``OPai-Desktop.exe`` behind the desktop icon. Each one hard-codes the
absolute path of the interpreter it will spawn, decided once at install time.

That path can be wrong, and when it is, a Windows launcher fails in the worst
possible way: it exits 1 immediately, with no window, no dialog, no log entry
and nothing on stderr. The icon is simply inert. Every other OPai surface
keeps working, so nothing anywhere reports a problem -- the app is broken and
the app does not know.

This module makes that answerable. It reads the interpreter each installed
launcher has baked in and checks whether that file is really there, so
``opai doctor`` can name a dead icon instead of leaving the user to guess.

It reports only what it read. A launcher whose bytes cannot be parsed is
``UNREADABLE``, not "fine": the one thing this module must never do is
report health it did not verify.
"""

from __future__ import annotations

import os
import sys
import sysconfig
from dataclasses import dataclass
from pathlib import Path

# A launcher's interpreter is present, so the launcher can at least start.
OK = "ok"
# The launcher names an interpreter that is not on disk. On Windows this is
# the silent exit-1 failure; the icon does nothing at all.
MISSING_INTERPRETER = "missing_interpreter"
# The entry point has no launcher installed under any known scripts directory.
NOT_INSTALLED = "not_installed"
# The file is there but no interpreter could be read out of it.
UNREADABLE = "unreadable"

STATUSES = (OK, MISSING_INTERPRETER, NOT_INSTALLED, UNREADABLE)

# The shebang pip writes sits immediately before the appended zip payload.
# Taking the *last* "#!" before the zip is what makes the answer right: the
# real launcher stub contains a "#!" of its own ~37KB earlier, and a forward
# search reports that decoy as the interpreter.
#
# The window is a bound, not the guard -- it keeps the scan off 100KB of
# unrelated bytes. Saying otherwise would overstate it, which is the same
# species of mistake as a surface overstating its evidence.
_SHEBANG_WINDOW = 512
_ZIP_MAGIC = b"PK\x03\x04"


@dataclass(frozen=True)
class LauncherReport:
    """One entry point, and the interpreter its launcher will really spawn."""

    name: str
    path: str
    interpreter: str
    status: str
    windowed: bool

    @property
    def healthy(self) -> bool:
        return self.status == OK

    def describe(self) -> str:
        if self.status == OK:
            return f"{self.name}: runs {self.interpreter}"
        if self.status == MISSING_INTERPRETER:
            kind = "desktop icon" if self.windowed else "command"
            return (
                f"{self.name}: the {kind} points at {self.interpreter}, "
                "which does not exist -- it will exit silently"
            )
        if self.status == NOT_INSTALLED:
            return f"{self.name}: no launcher installed"
        return f"{self.name}: installed at {self.path}, interpreter unreadable"


def scripts_directories() -> list[Path]:
    """Every directory pip may have written OPai's launchers into."""

    seen: list[Path] = []
    schemes = ["", f"{os.name}_user"]
    for scheme in schemes:
        try:
            raw = (
                sysconfig.get_path("scripts")
                if not scheme
                else sysconfig.get_path("scripts", scheme)
            )
        except (KeyError, ValueError):
            continue
        if not raw:
            continue
        candidate = Path(raw)
        if candidate not in seen:
            seen.append(candidate)
    # The interpreter that is running us may itself live beside the launchers
    # (a virtualenv), and that directory is authoritative even when sysconfig
    # reports a scheme the environment does not actually use.
    if sys.executable:
        beside = Path(sys.executable).parent
        if beside not in seen:
            seen.append(beside)
    return seen


def read_interpreter(path: Path) -> str:
    """Return the interpreter path baked into a launcher, or "" if unreadable."""

    try:
        blob = path.read_bytes()
    except OSError:
        return ""
    if not blob:
        return ""
    if blob[:2] == b"#!":
        # A plain POSIX console script: the shebang is the first line.
        line = blob.split(b"\n", 1)[0]
        return _clean(line[2:])
    zip_at = blob.find(_ZIP_MAGIC)
    if zip_at < 0:
        return ""
    window = blob[max(0, zip_at - _SHEBANG_WINDOW) : zip_at].rstrip(b"\x00")
    mark = window.rfind(b"#!")
    if mark < 0:
        return ""
    return _clean(window[mark + 2 :].split(b"\n", 1)[0])


def _clean(raw: bytes) -> str:
    text = raw.decode("utf-8", "replace").strip()
    # pip quotes interpreter paths that contain spaces.
    if len(text) >= 2 and text[0] == '"' and text[-1] == '"':
        text = text[1:-1]
    return text


def _launcher_path(directory: Path, name: str) -> Path | None:
    candidates = [name + ".exe", name] if os.name == "nt" else [name, name + ".exe"]
    for candidate in candidates:
        found = directory / candidate
        if found.is_file():
            return found
    return None


def entry_point_names(
    distribution: str = "opai",
) -> tuple[tuple[str, bool], ...]:
    """Return ``(name, windowed)`` for every launcher OPai installs.

    Read from the installed distribution rather than hard-coded, so a renamed
    or added entry point is checked without this module being edited -- and so
    a report can never describe launchers this build does not actually ship.
    """
    import importlib.metadata as metadata

    try:
        points = metadata.distribution(distribution).entry_points
    except metadata.PackageNotFoundError:
        return ()
    found: list[tuple[str, bool]] = []
    for point in points:
        group = getattr(point, "group", "")
        if group not in ("console_scripts", "gui_scripts"):
            continue
        found.append((point.name, group == "gui_scripts"))
    return tuple(sorted(set(found)))


def inspect_launchers(
    distribution: str = "opai",
    *,
    directories: list[Path] | None = None,
) -> list[LauncherReport]:
    """Report the interpreter behind each of OPai's installed launchers."""

    where = scripts_directories() if directories is None else list(directories)
    reports: list[LauncherReport] = []
    for name, windowed in entry_point_names(distribution):
        path: Path | None = None
        for directory in where:
            path = _launcher_path(directory, name)
            if path is not None:
                break
        if path is None:
            reports.append(LauncherReport(name, "", "", NOT_INSTALLED, windowed))
            continue
        interpreter = read_interpreter(path)
        if not interpreter:
            reports.append(LauncherReport(name, str(path), "", UNREADABLE, windowed))
            continue
        status = OK if os.path.exists(interpreter) else MISSING_INTERPRETER
        reports.append(LauncherReport(name, str(path), interpreter, status, windowed))
    return reports


def broken_launchers(
    reports: list[LauncherReport] | None = None,
) -> list[LauncherReport]:
    """Only the launchers that name an interpreter which is not there."""

    checked = inspect_launchers() if reports is None else reports
    return [report for report in checked if report.status == MISSING_INTERPRETER]


def summary(reports: list[LauncherReport] | None = None) -> dict[str, object]:
    """A doctor-shaped summary that never claims unverified health."""

    checked = inspect_launchers() if reports is None else reports
    if not checked:
        return {
            "checked": 0,
            "healthy": False,
            "available": False,
            "broken": [],
            "note": "no installed launchers found to check",
        }
    bad = [report for report in checked if report.status == MISSING_INTERPRETER]
    unreadable = [report for report in checked if report.status == UNREADABLE]
    return {
        "checked": len(checked),
        # Unreadable is not healthy: it is unverified, and saying otherwise is
        # the exact failure this module exists to stop.
        "healthy": not bad and not unreadable,
        "available": True,
        "broken": [report.name for report in bad],
        "unreadable": [report.name for report in unreadable],
        "note": "" if not bad else "reinstall OPai to rewrite its launchers",
    }
