"""Vesta self-update: check-for-update and forced-update for the desktop app.

Vesta normally runs from a git checkout: ``install.ps1``/``install.sh`` clone
or ``git pull`` a copy under ``~/.opai/source`` and ``pip install -e`` it, so
"update" is exactly those same three steps — fetch, fast-forward, reinstall —
made available as one call instead of a re-run of the installer. This module
gives the desktop app an honest "you're behind" signal (``check_for_update``)
and a safe way to act on it (``apply_update``), for a Settings button and a
startup check, without ever needing a published release artifact.

Safety contract: an update check never mutates anything and never raises —
any failure (offline, not a git checkout, no remote) comes back as a plain
``checked: False`` with a human-readable reason. Applying an update refuses
outright on a dirty working tree (reporting ``dirty: True`` so a caller can
offer "update anyway") or a repo with no ``origin`` remote. Passing
``force=True`` performs that "update anyway": local changes are stashed
before the update and restored afterward, so nothing is ever discarded —
only a genuine merge conflict between the stash and the update can leave
changes sitting in the stash for the user to resolve by hand.
"""

from __future__ import annotations

import json
import re
import subprocess  # nosec B404 - fixed git/pip argv, never a shell
import time
from pathlib import Path
from typing import Any, Callable, Sequence

from . import __version__ as CURRENT_VERSION
from opaihub.boundary_errors import safe_detail

# An hour: opening Settings repeatedly (or the startup check) should not
# re-hit the network every time. The button (`force=True`) always re-checks.
CHECK_TTL_SECONDS = 3600.0
DEFAULT_BRANCH = "main"
_GIT_TIMEOUT_SECONDS = 8.0
_DUNDER_VERSION = re.compile(r'(?m)^\s*__version__\s*=\s*"([^"]+)"')
_APPLICATION_VERSION = re.compile(r'(?m)^\s*APPLICATION_VERSION\s*=\s*"([^"]+)"')

GitRunner = Callable[[Path, Sequence[str]], "subprocess.CompletedProcess[str]"]
PipInstaller = Callable[[Path], "subprocess.CompletedProcess[str]"]
# (stage label, stages finished, stages total). Reported *before* each
# stage starts, so a caller can name the step the user is waiting on
# rather than the one that just finished.
UpdateProgress = Callable[[str, int, int], None]
# The reinstall dominates the wall clock -- git is milliseconds, pip is
# seconds -- so the stages are deliberately not equal in duration. They
# are named, and the label is what carries the truth.
_APPLY_STAGES = (
    "Checking your working tree",
    "Fetching the latest version",
    "Fast-forwarding to it",
    "Reinstalling Vesta",
)


def install_root() -> Path:
    """Where Vesta's own source lives — never the user's active project.

    ``check_for_update``/``apply_update`` operate on *this*, resolved from
    this module's own file location so it works whether Vesta is installed
    editable (``pip install -e .`` against a git clone, the normal case) or,
    later, from a non-editable copy (where it will simply not be a git
    checkout, and updates are honestly reported as unavailable here).
    """

    return Path(__file__).resolve().parent.parent


def _default_state_path() -> Path:
    return Path.home() / ".opai" / "update_check.json"


def _default_git(root: Path, args: Sequence[str]) -> "subprocess.CompletedProcess[str]":
    from opaihub.proc import no_window_kwargs

    return subprocess.run(  # nosec B603 B607 - fixed git argv, no shell
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=_GIT_TIMEOUT_SECONDS,
        **no_window_kwargs(),
    )


def _default_pip_install(root: Path) -> "subprocess.CompletedProcess[str]":
    from opaihub.proc import console_interpreter, no_window_kwargs

    # Not sys.executable: the updater usually runs inside the desktop app,
    # whose interpreter is pythonw.exe, and pip derives a gui_scripts
    # launcher from it by replacing "python" with "pythonw" -- producing
    # "pythonww.exe", which does not exist. Installing Vesta from its own GUI
    # would then leave the desktop icon dead, exiting 1 with no window and no
    # message. See opaihub.proc.console_interpreter.
    return subprocess.run(  # nosec B603 - fixed argv, no shell
        [
            console_interpreter(),
            "-m",
            "pip",
            "install",
            "-e",
            str(root),
            "--no-deps",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=120.0,
        **no_window_kwargs(),
    )


def _load_cache(cache_path: Path) -> dict[str, Any]:
    try:
        return json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_cache(cache_path: Path, payload: dict[str, Any]) -> None:
    from opaihub.atomic_io import atomic_write_text

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(cache_path, json.dumps(payload, indent=2, sort_keys=True))


def _is_git_checkout(root: Path, git: GitRunner) -> bool:
    try:
        result = git(root, ["rev-parse", "--is-inside-work-tree"])
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and result.stdout.strip() == "true"


def _has_origin(root: Path, git: GitRunner) -> bool:
    try:
        result = git(root, ["remote", "get-url", "origin"])
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and bool(result.stdout.strip())


def _working_tree_dirty(root: Path, git: GitRunner) -> bool:
    result = git(root, ["status", "--porcelain"])
    return result.returncode != 0 or bool(result.stdout.strip())


def _not_checked(reason: str, branch: str) -> dict[str, Any]:
    return {
        "current_version": CURRENT_VERSION,
        "checked": False,
        "checked_at": time.time(),
        "up_to_date": True,
        "latest_version": None,
        "commits_behind": 0,
        "branch": branch,
        "reason": reason,
    }


def check_for_update(
    project_root: Path,
    *,
    branch: str = DEFAULT_BRANCH,
    force: bool = False,
    git: GitRunner = _default_git,
    cache_path: Path | None = None,
) -> dict[str, Any]:
    """Compare the running version against ``origin/<branch>``.

    Cached for `CHECK_TTL_SECONDS`; pass ``force=True`` (the button, or any
    explicit user action) to bypass the cache and check right now.
    """

    cache_path = cache_path or _default_state_path()
    if not force:
        cached = _load_cache(cache_path)
        checked_at = cached.get("checked_at")
        if (
            isinstance(checked_at, (int, float))
            and time.time() - checked_at < CHECK_TTL_SECONDS
        ):
            return cached

    root = Path(project_root)
    if not _is_git_checkout(root, git):
        result = _not_checked(
            "Vesta isn't running from a git checkout, so it can't check for updates itself.",
            branch,
        )
        _save_cache(cache_path, result)
        return result
    if not _has_origin(root, git):
        result = _not_checked("No 'origin' remote is configured.", branch)
        _save_cache(cache_path, result)
        return result

    try:
        fetched = git(
            root,
            [
                "fetch",
                "--quiet",
                "origin",
                f"refs/heads/{branch}:refs/remotes/origin/{branch}",
            ],
        )
    except subprocess.TimeoutExpired:
        result = _not_checked("Update check timed out — you may be offline.", branch)
        _save_cache(cache_path, result)
        return result
    except (OSError, subprocess.SubprocessError) as exc:
        result = _not_checked(
            f"Could not reach the update server: {safe_detail(exc)}", branch
        )
        _save_cache(cache_path, result)
        return result
    if fetched.returncode != 0:
        result = _not_checked(
            "Could not reach the update server — you may be offline.", branch
        )
        _save_cache(cache_path, result)
        return result

    remote_ref = f"origin/{branch}"
    result = {
        "current_version": CURRENT_VERSION,
        "checked": True,
        "checked_at": time.time(),
        "branch": branch,
        "reason": None,
    }

    try:
        count = git(root, ["rev-list", "--count", f"HEAD..{remote_ref}"])
    except (OSError, subprocess.SubprocessError):
        count = None
    if count is None or count.returncode != 0 or not count.stdout.strip().isdigit():
        result = _not_checked(
            "Could not compare the installed code with the latest main branch. Try checking again.",
            branch,
        )
        _save_cache(cache_path, result)
        return result
    behind = int(count.stdout.strip())
    result["commits_behind"] = behind
    result["up_to_date"] = behind == 0

    latest_version = None
    for filename, pattern in (
        ("_generated_release.py", _APPLICATION_VERSION),
        ("__init__.py", _DUNDER_VERSION),
    ):
        try:
            version_show = git(root, ["show", f"{remote_ref}:opai/{filename}"])
        except (OSError, subprocess.SubprocessError):
            continue
        match = (
            pattern.search(version_show.stdout)
            if version_show.returncode == 0
            else None
        )
        if match:
            latest_version = match.group(1)
            break
    result["latest_version"] = latest_version

    _save_cache(cache_path, result)
    return result


def apply_update(
    project_root: Path,
    *,
    branch: str = DEFAULT_BRANCH,
    force: bool = False,
    git: GitRunner = _default_git,
    pip_install: PipInstaller | None = None,
    cache_path: Path | None = None,
    progress: UpdateProgress | None = None,
) -> dict[str, Any]:
    """Fast-forward to ``origin/<branch>`` and refresh the editable install.

    Refuses on any uncommitted change unless ``force=True`` — the "update
    anyway" choice — in which case local changes are stashed before the
    update and restored afterward, so nothing is discarded. Refuses outright
    on a repo not tracking ``origin`` regardless of ``force``. A successful
    update requires an app restart to take effect (the running process
    already has the old code loaded in memory).

    ``progress`` is called before each named stage with ``(label, done,
    total)``. It exists because the reinstall is seconds long with nothing to
    show for it, and an update that looks frozen is indistinguishable from one
    that is. A callback that raises is the caller's problem, never this
    function's: reporting must not be able to fail an update.
    """

    def stage(index: int) -> None:
        if progress is None:
            return
        try:
            progress(_APPLY_STAGES[index], index, len(_APPLY_STAGES))
        except Exception:  # noqa: BLE001 - telling someone must not break doing
            # nosec B110 - deliberately swallowed and deliberately silent. This
            # is the *reporting* path: a surface that went away mid-update must
            # not be able to fail the update, and there is nowhere to log it to
            # that would not be the same broken surface.
            pass

    root = Path(project_root)
    stage(0)
    if not _is_git_checkout(root, git):
        return {"ok": False, "error": "Vesta isn't running from a git checkout."}
    if not _has_origin(root, git):
        return {"ok": False, "error": "No 'origin' remote is configured."}

    dirty = _working_tree_dirty(root, git)
    if dirty and not force:
        return {
            "ok": False,
            "dirty": True,
            "error": "There are uncommitted local changes — commit, stash, or discard them before updating.",
        }

    stashed = False
    if dirty:
        stash = git(
            root,
            ["stash", "push", "--include-untracked", "-m", "opai-update-autostash"],
        )
        if stash.returncode != 0:
            return {
                "ok": False,
                "error": f"Could not set aside your local changes to update anyway: {stash.stderr.strip()}",
            }
        stashed = True

    stage(1)
    try:
        fetch = git(
            root,
            [
                "fetch",
                "--quiet",
                "origin",
                f"refs/heads/{branch}:refs/remotes/origin/{branch}",
            ],
        )
    except (OSError, subprocess.SubprocessError, subprocess.TimeoutExpired) as exc:
        if stashed:
            git(root, ["stash", "pop"])
        return {
            "ok": False,
            "error": f"Could not reach the update server: {safe_detail(exc)}",
        }
    if fetch.returncode != 0:
        if stashed:
            git(root, ["stash", "pop"])
        return {
            "ok": False,
            "error": "Could not reach the update server — check your connection.",
        }

    stage(2)
    checkout = git(root, ["checkout", branch])
    if checkout.returncode != 0:
        if stashed:
            git(root, ["stash", "pop"])
        return {
            "ok": False,
            "error": f"Could not switch to '{branch}': {checkout.stderr.strip()}",
        }

    merged = git(root, ["merge", "--ff-only", f"origin/{branch}"])
    if merged.returncode != 0:
        if stashed:
            git(root, ["stash", "pop"])
        return {
            "ok": False,
            "error": "Could not fast-forward to the latest version — local history has diverged.",
        }

    if stashed:
        pop = git(root, ["stash", "pop"])
        if pop.returncode != 0:
            return {
                "ok": False,
                "code_updated": True,
                "error": (
                    "Updated to the latest version, but your local changes couldn't be "
                    "restored automatically because they now conflict with it. They're "
                    "safe in the git stash — run 'git stash pop' yourself to resolve, "
                    "then restart Vesta."
                ),
            }

    stage(3)
    installer = pip_install or _default_pip_install
    try:
        installed = installer(root)
    except (OSError, subprocess.SubprocessError, subprocess.TimeoutExpired) as exc:
        return {
            "ok": False,
            "error": f"Updated the code, but reinstalling failed: {safe_detail(exc)}. Restart Vesta and try again.",
            "code_updated": True,
        }
    if installed.returncode != 0:
        return {
            "ok": False,
            "error": "Updated the code, but reinstalling the package failed. Restart Vesta and try again.",
            "code_updated": True,
        }

    new_version = CURRENT_VERSION
    for filename, pattern in (
        ("_generated_release.py", _APPLICATION_VERSION),
        ("__init__.py", _DUNDER_VERSION),
    ):
        try:
            match = pattern.search(
                (root / "opai" / filename).read_text(encoding="utf-8")
            )
        except OSError:
            continue
        if match:
            new_version = match.group(1)
            break

    _save_cache(
        cache_path or _default_state_path(),
        {
            "current_version": new_version,
            "checked": True,
            "checked_at": time.time(),
            "up_to_date": True,
            "latest_version": new_version,
            "commits_behind": 0,
            "branch": branch,
            "reason": None,
        },
    )
    result = {"ok": True, "restart_required": True, "installed_version": new_version}
    if stashed:
        result["local_changes_restored"] = True
    return result
