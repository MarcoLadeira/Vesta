"""How often this installation should look for a new version.

One interval for every installation was the wrong shape. A packaged stable
build receives a release every few weeks, so asking every four hours is
generous. A source checkout tracks ``origin/main``, which can take several
merges in an hour -- and four hours of not looking means a developer runs an
obsolete build for most of a working day while every surface agrees there is
nothing to update.

The cadence is therefore a property of what this installation *tracks*, not a
constant. It lives here, alone, so it can be read, argued with and tested,
rather than being spelled as a number in whichever module happened to need it.

The values are deliberately conservative at the fast end. Ten minutes for a
source checkout is one ``git fetch`` of one branch -- cheap, local-network in
most cases, and far below the rate at which a developer would hit "Check for
updates" by hand.
"""

from __future__ import annotations

from .models import InstallType

# Bumped when the table below changes in a way existing installations should
# adopt. Persisted policies carry the version they were written under, so a
# stale interval can be migrated exactly once without ever overwriting an
# interval the user chose themselves.
CADENCE_POLICY_VERSION = 1

# The interval every installation used to share, and the value already sitting
# in policy.json on every machine that has run OPai before this change.
LEGACY_INTERVAL_SECONDS = 4 * 60 * 60

_SOURCE_CHECKOUT_SECONDS = 10 * 60
_BY_CHANNEL_SECONDS = {
    # Internal builds move constantly and their users are the people who need
    # to be on the newest one.
    "alpha": 15 * 60,
    "beta": 45 * 60,
    # A stable release lands every few weeks. Four hours costs nothing and
    # keeps the update source quiet.
    "stable": 4 * 60 * 60,
}

# A floor the rest of the system can rely on: no cadence may ask the update
# source for anything more often than this, whatever the table says.
MINIMUM_INTERVAL_SECONDS = 5 * 60


def discovery_interval_seconds(
    install_type: InstallType | str, channel: str = "stable"
) -> int:
    """The automatic discovery interval for this installation, in seconds.

    Install type wins over channel. A source checkout is a source checkout
    whichever channel it nominally reports, because what it actually tracks is
    a git branch rather than a release feed.
    """

    value = (
        install_type.value
        if isinstance(install_type, InstallType)
        else str(install_type or "")
    )
    if value == InstallType.SOURCE_CHECKOUT.value:
        return _SOURCE_CHECKOUT_SECONDS
    return _BY_CHANNEL_SECONDS.get(str(channel or "stable"), LEGACY_INTERVAL_SECONDS)


def cadence_reason(install_type: InstallType | str, channel: str = "stable") -> str:
    """A short, safe explanation of why this cadence applies. For diagnostics."""

    value = (
        install_type.value
        if isinstance(install_type, InstallType)
        else str(install_type or "")
    )
    if value == InstallType.SOURCE_CHECKOUT.value:
        return "source checkout tracking origin/main"
    channel = str(channel or "stable")
    if channel in _BY_CHANNEL_SECONDS:
        return f"{channel} release channel"
    return "default release cadence"
