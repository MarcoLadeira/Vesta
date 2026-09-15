"""Truthful packaged/source installation detection."""

from __future__ import annotations

import ctypes
import os
import platform
import sys
from pathlib import Path

from .models import InstallType


_APPMODEL_ERROR_NO_PACKAGE = 15700


def _windows_has_package_identity() -> bool:
    if os.name != "nt":
        return False
    try:
        length = ctypes.c_uint32(0)
        result = ctypes.windll.kernel32.GetCurrentPackageFullName(  # type: ignore[attr-defined]
            ctypes.byref(length), None
        )
        return int(result) != _APPMODEL_ERROR_NO_PACKAGE
    except (AttributeError, OSError):
        return False


def detect_install_type(
    *,
    platform_name: str | None = None,
    package_identity: bool | None = None,
    executable: Path | None = None,
    git_checkout: bool | None = None,
) -> InstallType:
    system = (platform_name or platform.system()).casefold()
    exe = (executable or Path(sys.executable)).expanduser().resolve(strict=False)
    if system == "windows" and (
        _windows_has_package_identity()
        if package_identity is None
        else package_identity
    ):
        return InstallType.WINDOWS_MSIX
    if system in {"darwin", "macos"} and any(
        part.casefold().endswith(".app") for part in exe.parts
    ):
        return InstallType.MACOS_SPARKLE
    if git_checkout is None:
        root = Path(__file__).resolve().parents[2]
        git_checkout = (root / ".git").exists()
    if git_checkout:
        return InstallType.SOURCE_CHECKOUT
    if system in {"windows", "darwin", "macos"}:
        return InstallType.PORTABLE
    return InstallType.UNKNOWN
