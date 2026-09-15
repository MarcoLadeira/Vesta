"""Whether the code this process is running still matches the code on disk.

The updater answers "is there a newer version available?" by comparing the
checkout against its remote. That is the right question and it is not the only
one, because an update lands on disk while the old code is still loaded in
memory. Once a fast-forward or a reinstall has happened, the checkout *is* up
to date -- truthfully, and uselessly, because the window in front of you is
still running what it loaded at launch.

So a session could sit for hours showing a UI several merges old while every
surface agreed there was nothing to update: the check compared the wrong two
things. It reported on the repository. What the user sees is the process.

This compares the assets on disk against the ones this process started with.
The expensive part -- a SHA-256 over every packaged asset, about 30ms -- runs
only when a cheap mtime scan says something moved, so the steady state is 32
stat calls and no hashing at all.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from vesta.asset_identity import AssetIntegrityError, asset_manifest

# The build this process is running: captured once, never updated. Refreshing
# it after an update would defeat the entire point -- the memory image does not
# change when the files do, so neither may the baseline.
_baseline: dict[str, Any] | None = None


def _newest_mtime(asset_root: Path) -> float:
    newest = 0.0
    for path in Path(asset_root).rglob("*"):
        if path.name == ".runtime-index.html":
            # Rewritten on every launch for cache-busting, so it always looks
            # newer than the process and would report staleness forever.
            continue
        try:
            if path.is_file():
                newest = max(newest, path.stat().st_mtime)
        except OSError:
            continue
    return newest


def prime(asset_root: Path) -> None:
    """Record the build this process is running. Safe to call more than once.

    Called at startup so the baseline is the code actually loaded. Capturing it
    lazily on first *comparison* would be worse than useless: after an update
    the first call would fingerprint the new files and conclude, wrongly and
    permanently, that nothing had changed.
    """

    global _baseline
    if _baseline is not None:
        return
    root = Path(asset_root)
    try:
        manifest = asset_manifest(root)
    except (AssetIntegrityError, OSError):
        # Unreadable assets are the integrity check's problem, not this one.
        return
    _baseline = {
        "fingerprint": str(manifest.get("fingerprint_sha256") or ""),
        "mtime": _newest_mtime(root),
    }


def running_build_is_stale(asset_root: Path) -> bool:
    """Whether the files on disk have changed since this process started."""

    if _baseline is None or not _baseline["fingerprint"]:
        return False
    root = Path(asset_root)
    try:
        if _newest_mtime(root) <= _baseline["mtime"]:
            return False
    except OSError:
        return False
    # Something moved. Only now is it worth hashing: a touched file, a
    # checkout that rewrote a file to what it already said, or a reinstall that
    # copied identical bytes must not ask anyone to restart.
    try:
        manifest = asset_manifest(root)
    except (AssetIntegrityError, OSError):
        return False
    return str(manifest.get("fingerprint_sha256") or "") != _baseline["fingerprint"]


def reset_for_tests() -> None:
    """Drop the captured baseline. Tests only."""

    global _baseline
    _baseline = None
