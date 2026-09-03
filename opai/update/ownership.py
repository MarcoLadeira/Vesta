"""Which OPai is running, and who is allowed to update it.

Two questions that look like one.

**Which OPai is running.** `load_installed_build` reports `version` from
`release-identity.json` when that file exists, and from the imported
`__version__` when it does not. Those are different clocks: the file is on
disk and is rewritten by an update, while the imported value is whatever this
process loaded at startup. So a completed update makes the surface report the
new version while the old code is still running -- "Update complete" over code
that is not running, which is the one claim an updater must never make.
Running and disk identity are therefore reported separately, and the disk one
only when it actually differs.

**Who owns updates.** A source checkout updates itself from origin/main. A pip
or pipx installation must not: fast-forwarding a git tree that no longer backs
the running code, or that does not exist, is how an updater "succeeds" against
the wrong installation. When something else owns the installation OPai should
say which something, and what command that owner responds to, rather than
reporting a generic "manual update required" that leaves the user guessing.

Ownership is *detected*, never assumed: `INSTALLER` in the distribution
metadata is written by the tool that performed the install, and the
interpreter's own path tells pipx and Homebrew apart from a plain pip.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from importlib import metadata
import os
from pathlib import Path
import sys

from .models import InstallType


@dataclass(frozen=True)
class UpdateOwnership:
    """Who owns updates for this installation, and how a user reaches them."""

    owner: str
    mechanism: str
    evidence: str
    remediation: str
    self_updatable: bool

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _distribution_installer() -> str:
    """The tool that installed this distribution, per PEP 627's INSTALLER."""

    try:
        text = metadata.distribution("opai").read_text("INSTALLER") or ""
    except Exception:  # noqa: BLE001 - absent metadata is a normal answer
        return ""
    return text.strip().casefold()


def _interpreter_path() -> str:
    return str(Path(sys.executable).expanduser().resolve(strict=False)).casefold()


def describe_ownership(install_type: InstallType, *, management_source: str = "") -> UpdateOwnership:
    """Detect who owns updates for the running installation."""

    if management_source:
        return UpdateOwnership(
            owner="external",
            mechanism="managed policy",
            evidence=f"managed by {management_source}",
            remediation="Updates are managed by your administrator.",
            self_updatable=False,
        )

    if install_type is InstallType.SOURCE_CHECKOUT:
        return UpdateOwnership(
            owner="opai",
            mechanism="git",
            evidence="source checkout",
            remediation="OPai updates itself by fast-forwarding origin/main.",
            self_updatable=True,
        )
    if install_type is InstallType.WINDOWS_MSIX:
        return UpdateOwnership(
            owner="store",
            mechanism="msix",
            evidence="Windows package identity",
            remediation="Updates are delivered through the Microsoft Store.",
            self_updatable=False,
        )
    if install_type is InstallType.MACOS_SPARKLE:
        return UpdateOwnership(
            owner="opai",
            mechanism="sparkle",
            evidence="application bundle",
            remediation="OPai updates itself from the signed release feed.",
            self_updatable=True,
        )

    # Everything else is a Python distribution somebody installed with a tool,
    # and that tool is the one that can replace it.
    interpreter = _interpreter_path()
    installer = _distribution_installer()
    if "pipx" in interpreter:
        return UpdateOwnership(
            owner="pipx",
            mechanism="pipx",
            evidence="running from a pipx virtual environment",
            remediation="Update with `pipx upgrade opai`.",
            self_updatable=False,
        )
    if f"{os.sep}cellar{os.sep}" in interpreter or "/opt/homebrew/" in interpreter:
        return UpdateOwnership(
            owner="homebrew",
            mechanism="homebrew",
            evidence="running from a Homebrew prefix",
            remediation="Update with `brew upgrade opai`.",
            self_updatable=False,
        )
    if installer:
        return UpdateOwnership(
            owner=installer,
            mechanism=installer,
            evidence=f"installed by {installer}",
            remediation=f"Update with `{installer} install --upgrade opai`.",
            self_updatable=False,
        )
    return UpdateOwnership(
        owner="unknown",
        mechanism="manual",
        evidence="no installer metadata",
        remediation="Reinstall OPai to update it.",
        self_updatable=False,
    )


def safe_launcher_identity() -> str:
    """How this process was started, with the home directory taken out.

    The epic asks for launcher identity "where safe". A full path names the
    user, and updater diagnostics get pasted into issues -- so the parts that
    identify the *installation* are kept and the part that identifies the
    *person* is not.
    """

    raw = sys.argv[0] if sys.argv and sys.argv[0] else sys.executable
    try:
        path = Path(raw).expanduser().resolve(strict=False)
    except (OSError, ValueError):
        return ""
    try:
        home = Path.home().resolve(strict=False)
        return f"~/{path.relative_to(home).as_posix()}"
    except (OSError, ValueError):
        return path.as_posix()


def running_identity() -> dict[str, str]:
    """The version and build this process actually loaded."""

    import opai

    build = ""
    try:
        from opai._generated_release import APPLICATION_BUILD_ID  # type: ignore

        build = str(APPLICATION_BUILD_ID)
    except Exception:  # noqa: BLE001 - development builds carry no build id
        build = "development"
    return {"version": str(getattr(opai, "__version__", "")), "build_id": build}
