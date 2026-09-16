"""Everything Vesta still knows about its former name, "OPai", in one place.

Vesta used to be called OPai. Installs made before the rename still carry the
old names on disk: a ``~/.opai`` home folder, ``.opaihub`` project state,
``OPAI_*`` environment variables, an ``OPai/free-model-api`` keychain service,
``opai-*`` launch wrappers and "OPai"-marked managed blocks. This module holds
every one of those legacy names -- nothing else in the codebase spells them --
plus the small, failure-tolerant functions that upgrade an old install in place.

Rules every migration here follows:

* Nothing is lost. A move never overwrites an existing target; when both the
  old and the new copy exist, the new one wins and the old one is left alone.
* Nothing breaks. No migration raises into startup; a failed step (a file in
  use, a permission error) is reported, the old location keeps being read, and
  the step is retried on the next start.
* The running install is never moved. A child of ``~/.opai`` that contains the
  code, interpreter or editable checkout this process runs from stays put.
* User-authored content is never deleted. Only files Vesta generated -- carrying
  its managed marker or living in a folder Vesta owns -- are removed.

Standard library only: the bootstrap imports this before any runtime layer.
"""

from __future__ import annotations

import errno
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, MutableMapping
from urllib.parse import unquote, urlparse

# --------------------------------------------------------------------------- #
# Legacy names
# --------------------------------------------------------------------------- #

LEGACY_BRAND = "OPai"
LEGACY_PACKAGE = "opai"
LEGACY_DISTRIBUTIONS = ("opai",)
CURRENT_DISTRIBUTIONS = ("vesta",)

HOME_DIRNAME = ".vesta"
LEGACY_HOME_DIRNAME = ".opai"

STATE_DIRNAME = ".vestahub"
LEGACY_STATE_DIRNAME = ".opaihub"

ENV_PREFIX = "VESTA_"
LEGACY_ENV_PREFIX = "OPAI_"

LEGACY_KEYRING_SERVICE = "OPai/free-model-api"

# Managed-block markers written into files Vesta does not own (CLAUDE.md,
# AGENTS.md, shell profiles) before the rename.
LEGACY_START_MARKER = "<!-- OPai managed block: start -->"
LEGACY_END_MARKER = "<!-- OPai managed block: end -->"
LEGACY_PS_START_MARKER = "# OPai managed block: start"
LEGACY_PS_END_MARKER = "# OPai managed block: end"

# Context-slimming rule headers written into AI ignore files before the rename.
LEGACY_IGNORE_HEADER = "# OPai context-slimming rules"
LEGACY_CONTEXT_RULES_START = "# OPai context-slimming rules (managed)"
LEGACY_CONTEXT_RULES_END = "# end OPai rules"

# Files Vesta installed under the old name.
LEGACY_WRAPPER_PREFIX = "opai-"
WRAPPER_TOOLS = ("codex", "claude", "copilot", "gemini")
LEGACY_SKILL_DIRNAME = "opai"
LEGACY_INSTRUCTIONS_FILENAME = "OPAI.md"
LEGACY_CLINE_RULE = (".clinerules", "opai.md")
LEGACY_CURSOR_RULE = (".cursor", "rules", "opai.mdc")
LEGACY_IGNORE_FILENAME = ".opaiignore"
# The status page `visibility install` wrote to a project root, and the
# names it listed in the repository's local git exclude file.
LEGACY_STATUS_FILENAME = "OPAI_STATUS.md"
LEGACY_STATUS_JSON = "opai-status.json"
LEGACY_STATUS_HEADINGS = ("# OPai Status", "# Vesta Status")

# The GitHub repository before it was renamed; GitHub still redirects it.
LEGACY_REPOSITORY = "MarcoLadeira/OPai"

# Where administrators deployed a machine update policy before the rename.
# A policy that disables or takes over updates must keep binding.
LEGACY_WINDOWS_POLICY_DIRNAME = "OPai"
LEGACY_MACOS_POLICY_FILE = "com.opai.desktop.update.json"
LEGACY_LINUX_POLICY_DIRNAME = "opai"

# Files a team commits to its repository. Repositories set up before the
# rename still carry these names; they are read until someone renames them.
LEGACY_TEAM_POLICY_FILE = "opai-team-policy.yaml"
LEGACY_VERIFICATION_POLICY_FILE = "opai-verification-policy.yaml"

# Keys in benchmark scores and task definitions written before the rename.
LEGACY_EFFECTIVENESS_KEY = "opai_effectiveness_index"
LEGACY_CONTEXT_BYTES_KEY = "opai_context_bytes"

# Written into a user's app by `opai new` and its builds.
LEGACY_APP_MANIFEST = ".opai-app.json"
LEGACY_APP_BACKUP_DIR = ".opai-backups"
LEGACY_APP_BUILD_LOG = ".opai-build-log.jsonl"

# Other names a project carries from before the rename.
LEGACY_PROJECT_RULES_FILE = ".opai/rules.md"
LEGACY_BENCH_DIRNAME = "opaibench"
LEGACY_MODEL_EVAL_REPORT = "opai-model-eval"
# Lines that surrounded the managed block in the generated Cursor/Cline rules.
LEGACY_RULE_BOILERPLATE = (
    "description: OPai local-first, cost-aware routing and safety policy",
    "For Cline: prefer OPai local-first routing before model escalation.",
)
# Only a wrapper that sets one of these was written by the old installer.
LEGACY_WRAPPER_SIGNATURES = ("OPAI_ACTIVE", f"-m {LEGACY_PACKAGE} ")


def repository_file(root: Path, name: str, legacy_name: str) -> Path:
    """``root/name``, or the pre-rename ``root/legacy_name`` while only that exists.

    A repository that committed a policy under its old name keeps that policy
    in force: silently ignoring it would weaken every check it configures.
    """

    current = root / name
    if current.exists():
        return current
    legacy = root / legacy_name
    return legacy if legacy.exists() else current


def legacy_env_name(name: str) -> str:
    """``VESTA_X`` -> ``OPAI_X``; any other name is returned unchanged."""

    if name.startswith(ENV_PREFIX):
        return LEGACY_ENV_PREFIX + name[len(ENV_PREFIX) :]
    return name


def legacy_spelling(text: str) -> str:
    """The pre-rename spelling of a generated line (``.vestahub/`` -> ``.opaihub/``)."""

    return (
        text.replace(STATE_DIRNAME, LEGACY_STATE_DIRNAME)
        .replace("Vesta", LEGACY_BRAND)
        .replace("vesta", LEGACY_PACKAGE)
    )


def _log(message: str) -> None:
    # Startup must stay quiet; a migration note is for whoever is debugging.
    if os.environ.get("VESTA_STARTUP_TRACE"):
        print(f"vesta.legacy: {message}", file=sys.stderr)


# --------------------------------------------------------------------------- #
# Environment
# --------------------------------------------------------------------------- #


def adopt_legacy_environment(
    environ: MutableMapping[str, str] | None = None, *, remove_legacy: bool = True
) -> list[str]:
    """Copy each ``OPAI_<X>`` to ``VESTA_<X>`` when ``VESTA_<X>`` is unset.

    Old shell wrappers, CI and user shells still export the old names. With
    ``remove_legacy`` (the default) the old names are then dropped from the
    mapping, so every child process Vesta spawns inherits ``VESTA_*`` only --
    and a stale old-name control value (autonomy, run id) cannot be revived by
    a child that adopts it again. Returns the ``VESTA_*`` names adopted.
    """

    env = os.environ if environ is None else environ
    adopted: list[str] = []
    for legacy_name in sorted(
        name for name in list(env) if name.startswith(LEGACY_ENV_PREFIX)
    ):
        suffix = legacy_name[len(LEGACY_ENV_PREFIX) :]
        if not suffix:
            continue
        current = ENV_PREFIX + suffix
        if current not in env:
            env[current] = env[legacy_name]
            adopted.append(current)
        if remove_legacy:
            env.pop(legacy_name, None)
    return adopted


def strip_legacy_environment(env: MutableMapping[str, str]) -> list[str]:
    """Remove every ``OPAI_*`` name from a child environment; returns them."""

    removed = sorted(name for name in env if name.startswith(LEGACY_ENV_PREFIX))
    for name in removed:
        env.pop(name, None)
    return removed


def env_flag(name: str, environ: MutableMapping[str, str] | None = None) -> str:
    """The value of ``VESTA_X``, falling back to a not-yet-adopted ``OPAI_X``."""

    env = os.environ if environ is None else environ
    return str(env.get(name) or env.get(legacy_env_name(name)) or "")


# --------------------------------------------------------------------------- #
# Home folder: ~/.opai -> ~/.vesta
# --------------------------------------------------------------------------- #


@dataclass
class HomeMigration:
    legacy: str
    target: str
    status: str = "absent"  # absent | same | migrated | partial | failed
    moved: list[str] = field(default_factory=list)
    running_install: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    failed: list[dict[str, str]] = field(default_factory=list)
    legacy_removed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "legacy": self.legacy,
            "target": self.target,
            "status": self.status,
            "moved": list(self.moved),
            "running_install": list(self.running_install),
            "conflicts": list(self.conflicts),
            "failed": [dict(item) for item in self.failed],
            "legacy_removed": self.legacy_removed,
        }


def _user_home(home: Path | None) -> Path:
    # Path.home() honours USERPROFILE/HOME, the only home override Vesta has.
    return (home or Path.home()).expanduser()


def legacy_home_dir(home: Path | None = None) -> Path:
    return _user_home(home) / LEGACY_HOME_DIRNAME


def home_item(base: Path, *parts: str) -> Path:
    """``base/.vesta/<parts>``, or the ``~/.opai`` copy while it is not migrated.

    ``base`` is the user's home directory, normalised however the caller
    already normalises it. The old location is used only when its top-level
    item still exists there and has no counterpart in ``.vesta`` -- the state a
    move that failed (a file in use) leaves behind until the next start
    retries it. Reads and writes then both go to the one live copy.
    """

    target = base / HOME_DIRNAME
    if not parts:
        return target
    head = parts[0]
    try:
        if not os.path.lexists(target / head):
            legacy = base / LEGACY_HOME_DIRNAME
            if os.path.lexists(legacy / head):
                return legacy.joinpath(*parts)
    except (OSError, ValueError):
        pass
    return target.joinpath(*parts)


def _norm(path: Path | str) -> str:
    try:
        resolved = Path(path).resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        resolved = Path(os.path.abspath(path))
    return os.path.normcase(str(resolved))


def _editable_roots(names: Iterable[str]) -> list[Path]:
    import importlib.metadata

    roots: list[Path] = []
    for name in names:
        try:
            text = importlib.metadata.distribution(name).read_text("direct_url.json")
        except Exception:  # noqa: BLE001 - metadata is optional evidence
            continue
        try:
            data = json.loads(text or "{}")
            parsed = urlparse(str(data.get("url") or ""))
        except (ValueError, AttributeError):
            continue
        if parsed.scheme != "file":
            continue
        raw = unquote(parsed.path)
        if os.name == "nt" and raw.startswith("/") and len(raw) > 2 and raw[2] == ":":
            raw = raw[1:]
        if parsed.netloc and os.name == "nt":
            raw = f"//{parsed.netloc}{raw}"
        roots.append(Path(raw))
    return roots


def running_install_paths() -> list[Path]:
    """Every path this process runs from: code, interpreter, editable checkouts."""

    paths: list[Path] = []
    package = sys.modules.get("vesta")
    package_file = getattr(package, "__file__", None) or __file__
    paths.append(Path(package_file).parent)
    if getattr(sys, "frozen", False) and sys.argv and sys.argv[0]:
        paths.append(Path(sys.argv[0]))
    for value in (sys.executable, sys.prefix, sys.base_prefix):
        if value:
            paths.append(Path(value))
    paths.extend(_editable_roots((*CURRENT_DISTRIBUTIONS, *LEGACY_DISTRIBUTIONS)))
    return paths


def _contains(parent: str, child: str) -> bool:
    try:
        return os.path.commonpath([parent, child]) == parent
    except ValueError:  # different drives
        return False


def _move_no_clobber(source: Path, target: Path) -> None:
    """Rename ``source`` to ``target`` without ever replacing an existing target."""

    if os.path.lexists(target):
        raise FileExistsError(errno.EEXIST, "target exists", str(target))
    if os.name == "nt" or source.is_dir() or source.is_symlink():
        # Windows rename refuses an existing target; POSIX rename of a
        # directory can only replace an *empty* directory, which loses nothing.
        os.rename(source, target)
        return
    try:
        os.link(source, target)
    except FileExistsError:
        raise
    except OSError:
        # Hard links unsupported on this filesystem: plain rename, target was
        # checked a moment ago.
        os.rename(source, target)
        return
    os.unlink(source)


def _is_code_checkout(path: Path) -> bool:
    return any(os.path.lexists(path / marker) for marker in (".git", "pyvenv.cfg"))


def _merge_directory(
    source: Path,
    target: Path,
    result: HomeMigration,
    protected: list[str],
    prefix: str,
    mover: Callable[[Path, Path], None],
) -> None:
    try:
        entries = sorted(os.scandir(source), key=lambda entry: entry.name)
    except OSError as exc:
        result.failed.append({"item": prefix or ".", "error": _error_text(exc)})
        return
    for entry in entries:
        name = f"{prefix}{entry.name}"
        child = Path(entry.path)
        normalised = _norm(child)
        if any(
            _contains(normalised, path) or _contains(path, normalised)
            for path in protected
        ):
            result.running_install.append(name)
            continue
        destination = target / entry.name
        if not os.path.lexists(destination):
            try:
                mover(child, destination)
            except FileNotFoundError:
                continue  # another Vesta process moved it first
            except FileExistsError:
                result.conflicts.append(name)
            except OSError as exc:
                result.failed.append({"item": name, "error": _error_text(exc)})
            else:
                result.moved.append(name)
            continue
        if (
            entry.is_dir(follow_symlinks=False)
            and destination.is_dir()
            and not destination.is_symlink()
            and not _is_code_checkout(child)
            and not _is_code_checkout(destination)
        ):
            _merge_directory(child, destination, result, protected, f"{name}/", mover)
            try:
                os.rmdir(child)  # only ever removes an empty directory
            except OSError:
                pass
            continue
        # Both copies exist: the current one wins, the old one is kept as-is.
        result.conflicts.append(name)


def _error_text(exc: OSError) -> str:
    code = errno.errorcode.get(exc.errno or 0, "") if exc.errno else ""
    return code or type(exc).__name__


def migrate_home(
    home: Path | None = None,
    *,
    protected_paths: Iterable[Path] | None = None,
    mover: Callable[[Path, Path], None] | None = None,
) -> HomeMigration:
    """Move ``~/.opai`` children into ``~/.vesta`` one by one. Never raises.

    * Each child moves on its own, so a partial run converges on the next.
    * A child containing the running install (``protected_paths``, by default
      :func:`running_install_paths`) is skipped and left where it runs from.
    * A child that fails to move (in use) is reported and read in place via
      :func:`home_item` until a later start moves it.
    * When both copies exist, directories are merged child by child (never a
      git checkout or virtualenv); a file present in both is left untouched.
    * ``~/.opai`` is removed only once it is empty.
    """

    base = _user_home(home)
    legacy = base / LEGACY_HOME_DIRNAME
    target = base / HOME_DIRNAME
    result = HomeMigration(legacy=str(legacy), target=str(target))
    try:
        if not legacy.is_dir() or legacy.is_symlink():
            return result
        if target.exists() and os.path.samefile(legacy, target):
            result.status = "same"
            return result
        target.mkdir(parents=True, exist_ok=True)
        paths = running_install_paths() if protected_paths is None else protected_paths
        protected = [_norm(path) for path in paths]
        _merge_directory(
            legacy, target, result, protected, "", mover or _move_no_clobber
        )
        try:
            os.rmdir(legacy)
            result.legacy_removed = True
        except OSError:
            pass
    except Exception as exc:  # noqa: BLE001 - startup must never fail here
        result.failed.append({"item": ".", "error": type(exc).__name__})
    left_behind = result.failed or result.conflicts or result.running_install
    if result.failed and not result.moved:
        result.status = "failed"
    elif left_behind:
        result.status = "partial"
    elif result.moved or result.legacy_removed:
        result.status = "migrated"
    else:
        result.status = "absent"
    if result.moved or result.failed:
        _log(f"home migration {result.status}: {result.to_dict()}")
    return result


# --------------------------------------------------------------------------- #
# Project state: .opaihub -> .vestahub
# --------------------------------------------------------------------------- #


def migrate_project_state(project_root: Path) -> str:
    """Rename a project's ``.opaihub`` to ``.vestahub`` in place. Never raises.

    Returns ``current`` (already migrated or nothing to do), ``migrated``,
    ``skipped`` (a link, or the user's home folder, whose machine-wide state
    holds git worktrees registered by absolute path) or ``failed`` (in use;
    retried the next time the state directory is resolved).
    """

    try:
        root = Path(project_root)
        target = root / STATE_DIRNAME
        if os.path.lexists(target):
            return "current"
        legacy = root / LEGACY_STATE_DIRNAME
        if not os.path.lexists(legacy):
            return "current"
        if (
            legacy.is_symlink()
            or (hasattr(legacy, "is_junction") and legacy.is_junction())
            or not legacy.is_dir()
        ):
            return "skipped"
        try:
            if _norm(root) == _norm(Path.home()):
                return "skipped"
        except (RuntimeError, OSError):
            pass
        os.replace(legacy, target)
    except OSError as exc:
        # Only the errno symbol or exception class: never a path or message.
        _log("project state migration failed: " + _error_text(exc))
        return "failed"
    except Exception:  # noqa: BLE001
        return "failed"
    _carry_git_excludes(root)
    _remove_legacy_status_page(root)
    return "migrated"


def legacy_project_state_dir(project_root: Path) -> Path:
    """The pre-rename state directory, still live while its move keeps failing."""

    return Path(project_root) / LEGACY_STATE_DIRNAME


def _git_exclude_files(root: Path) -> list[Path]:
    dot_git = root / ".git"
    if dot_git.is_dir():
        return [dot_git / "info" / "exclude"]
    text = _read(dot_git)
    if not text or not text.startswith("gitdir:"):
        return []
    # A worktree or submodule: git reads info/exclude from the common dir.
    git_dir = Path(text.splitlines()[0][len("gitdir:"):].strip())
    if not git_dir.is_absolute():
        git_dir = root / git_dir
    common = _read(git_dir / "commondir")
    if common and common.strip():
        common_dir = Path(common.strip())
        git_dir = common_dir if common_dir.is_absolute() else git_dir / common_dir
    return [git_dir / "info" / "exclude"]


def _carry_git_excludes(root: Path) -> None:
    """Keep the renamed state directory out of git where the old one was.

    ``visibility install`` hid ``.opaihub/`` and its status page through the
    repository's local exclude file. After the rename those lines match
    nothing, and the state directory -- ledgers, conversations, team keys --
    would show up in ``git status`` and be swept into ``git add -A``.
    """

    for exclude in _git_exclude_files(root):
        try:
            text = _read(exclude)
            if not text:
                continue
            lines = [line.strip() for line in text.splitlines()]
            renamed = []
            for line in lines:
                if line.startswith("#") or (
                    LEGACY_STATE_DIRNAME not in line and line != LEGACY_STATUS_FILENAME
                ):
                    continue
                new = (
                    line.replace(LEGACY_STATE_DIRNAME, STATE_DIRNAME)
                    .replace(LEGACY_STATUS_JSON, "vesta-status.json")
                    .replace(LEGACY_STATUS_FILENAME, "VESTA_STATUS.md")
                )
                if new not in lines and new not in renamed:
                    renamed.append(new)
            if not renamed:
                continue
            suffix = "" if text.endswith("\n") else "\n"
            with exclude.open("a", encoding="utf-8", newline="") as handle:
                handle.write(
                    suffix
                    + "# Vesta local state (renamed from .opaihub)\n"
                    + "\n".join(renamed)
                    + "\n"
                )
        except Exception:  # noqa: BLE001 - best effort; never block the move
            continue


def _remove_legacy_status_page(root: Path) -> None:
    path = root / LEGACY_STATUS_FILENAME
    text = _read(path)
    if text is None:
        return
    lines = text.splitlines()
    # Only the page Vesta wrote: it lists the old commands, which no longer exist.
    if lines and lines[0].strip() in LEGACY_STATUS_HEADINGS and "## Commands" in lines:
        _unlink(path, [])


# --------------------------------------------------------------------------- #
# Keychain: "OPai/free-model-api" -> "Vesta/free-model-api"
# --------------------------------------------------------------------------- #


def migrate_keyring_entry(backend: Any, service: str, username: str) -> str:
    """Return the secret for ``username``, moving a legacy entry to ``service``.

    Called after a read of ``service`` missed. The legacy value is written to
    the new service first and the legacy entry deleted only when that write
    succeeded, so a failure at any point leaves the secret readable.
    """

    try:
        value = str(
            backend.get_password(LEGACY_KEYRING_SERVICE, username) or ""
        ).strip()
    except Exception:  # noqa: BLE001 - backend failures fail closed
        return ""
    if not value:
        return ""
    try:
        backend.set_password(service, username, value)
    except Exception:  # noqa: BLE001 - keep the legacy entry; still usable
        return value
    try:
        backend.delete_password(LEGACY_KEYRING_SERVICE, username)
    except Exception:  # noqa: BLE001 - a leftover legacy copy is harmless
        pass
    return value


def delete_legacy_keyring_entry(backend: Any, username: str) -> None:
    """Forget the legacy copy too, so a deleted key is not migrated back."""

    try:
        backend.delete_password(LEGACY_KEYRING_SERVICE, username)
    except Exception:  # noqa: BLE001 - usually simply absent
        pass


# --------------------------------------------------------------------------- #
# Legacy installed files
# --------------------------------------------------------------------------- #


def _read(path: Path) -> str | None:
    try:
        if path.is_symlink() or not path.is_file():
            return None
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _unlink(path: Path, removed: list[str]) -> None:
    try:
        path.unlink()
    except OSError:
        return
    removed.append(str(path))


def _strip_managed_block(text: str) -> str | None:
    """``text`` without its managed block, or ``None`` when it has none."""

    for start_marker, end_marker in (
        (LEGACY_START_MARKER, LEGACY_END_MARKER),
        ("<!-- Vesta managed block: start -->", "<!-- Vesta managed block: end -->"),
    ):
        start = text.find(start_marker)
        end = text.find(end_marker)
        if start != -1 and end > start:
            return text[:start] + text[end + len(end_marker) :]
    return None


def is_generated_rule_file(text: str) -> bool:
    """A Cursor/Cline rule Vesta wrote, with nothing a user added around it."""

    remainder = _strip_managed_block(text)
    if remainder is None:
        return False
    allowed = {
        "---",
        "alwaysApply: true",
        *LEGACY_RULE_BOILERPLATE,
        *(line.replace(LEGACY_BRAND, "Vesta") for line in LEGACY_RULE_BOILERPLATE),
    }
    return all(
        not line.strip() or line.strip() in allowed for line in remainder.splitlines()
    )


def is_generated_ignore_file(text: str, known_lines: Iterable[str]) -> bool:
    """An ignore file holding only rules Vesta generates (either spelling)."""

    legacy_markers = {
        LEGACY_IGNORE_HEADER,
        LEGACY_CONTEXT_RULES_START,
        LEGACY_CONTEXT_RULES_END,
    }
    # Installs from between the display rename and the internal one wrote the
    # new brand into files that still carried the old names.
    markers = legacy_markers | {
        line.replace(LEGACY_BRAND, "Vesta") for line in legacy_markers
    }
    known = {line.strip() for line in known_lines} | markers
    known |= {legacy_spelling(line) for line in known}
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return bool(markers & set(lines)) and all(line in known for line in lines)


def is_legacy_wrapper(path: Path) -> bool:
    name = path.name
    stem = name[: -len(".ps1")] if name.endswith(".ps1") else name
    if stem not in {f"{LEGACY_WRAPPER_PREFIX}{tool}" for tool in WRAPPER_TOOLS}:
        return False
    text = _read(path)
    return bool(text) and any(mark in text for mark in LEGACY_WRAPPER_SIGNATURES)


_REGISTRY_ID = re.compile(r"""(?:"id"\s*:\s*"|\bid:\s*["']?)([A-Za-z0-9_.-]+)""")


def _legacy_skill_library_files(path: Path) -> list[Path] | None:
    """The files of ``~/.agents/skills/opai`` Vesta wrote, or ``None`` if not ours.

    Ours means its ``registry.yaml`` is the Vesta skill registry (it lists the
    ``using-opai``/``using-vesta`` core skill). Only the registry, the top-level
    SKILL.md and each registered ``<id>/SKILL.md`` are returned; anything a
    user added inside the folder is not.
    """

    if path.is_symlink() or not path.is_dir():
        return None
    registry = _read(path / "registry.yaml")
    if not registry:
        return None
    ids = set(_REGISTRY_ID.findall(registry))
    if not ids & {f"using-{LEGACY_PACKAGE}", "using-vesta"}:
        return None
    files = [path / "registry.yaml"]
    skill = _read(path / "SKILL.md") or ""
    if f"name: {LEGACY_PACKAGE}" in skill:
        files.append(path / "SKILL.md")
    files.extend(path / skill_id / "SKILL.md" for skill_id in sorted(ids))
    return [item for item in files if item.is_file() and not item.is_symlink()]


def remove_legacy_global_artifacts(
    home: Path | None = None, *, claude_memory_upgraded: bool = True
) -> list[str]:
    """Delete files the old install wrote into Vesta-owned locations.

    * ``~/.vesta/bin/opai-*`` (and a not-yet-migrated ``~/.opai/bin``) --
      only wrappers carrying the old installer's signature;
    * ``~/.agents/skills/opai`` -- only when its SKILL.md/registry.yaml show
      it is the Vesta skill library copy;
    * ``instructions/OPAI.md`` -- unless ``claude_memory_upgraded`` is false,
      because an old CLAUDE.md block may still point at it.
    """

    base = _user_home(home)
    removed: list[str] = []
    for root in (base / HOME_DIRNAME, base / LEGACY_HOME_DIRNAME):
        bin_dir = root / "bin"
        try:
            candidates = sorted(bin_dir.iterdir()) if bin_dir.is_dir() else []
        except OSError:
            candidates = []
        for path in candidates:
            if is_legacy_wrapper(path):
                _unlink(path, removed)
        if claude_memory_upgraded:
            instructions = root / "instructions" / LEGACY_INSTRUCTIONS_FILENAME
            if _read(instructions) is not None:
                _unlink(instructions, removed)
    for empty in (
        base / LEGACY_HOME_DIRNAME / "bin",
        base / LEGACY_HOME_DIRNAME / "instructions",
        base / LEGACY_HOME_DIRNAME,
    ):
        _rmdir_quietly(empty)
    skill = base / ".agents" / "skills" / LEGACY_SKILL_DIRNAME
    for path in _legacy_skill_library_files(skill) or ():
        _unlink(path, removed)
        if path.parent != skill:
            _rmdir_quietly(path.parent)
    _rmdir_quietly(skill)
    return removed


def _rmdir_quietly(path: Path) -> None:
    try:
        os.rmdir(path)  # only an empty directory; user files keep it alive
    except OSError:
        pass


def legacy_project_artifacts(
    project_root: Path, *, ignore_lines: Iterable[str] = ()
) -> list[Path]:
    """Old-name files in a project that hold only what Vesta generated."""

    root = Path(project_root)
    found: list[Path] = []
    for parts in (LEGACY_CLINE_RULE, LEGACY_CURSOR_RULE):
        path = root.joinpath(*parts)
        text = _read(path)
        if text is not None and is_generated_rule_file(text):
            found.append(path)
    ignore = root / LEGACY_IGNORE_FILENAME
    text = _read(ignore)
    if text is not None and is_generated_ignore_file(text, ignore_lines):
        found.append(ignore)
    return found


def remove_legacy_project_artifacts(
    project_root: Path, *, ignore_lines: Iterable[str] = ()
) -> list[str]:
    """Delete old-name rule and ignore files Vesta generated in a project."""

    removed: list[str] = []
    for path in legacy_project_artifacts(project_root, ignore_lines=ignore_lines):
        _unlink(path, removed)
    return removed


# Files that carry Vesta's managed instruction block in a project.
PROJECT_INSTRUCTION_FILES = (
    "AGENTS.md",
    "CLAUDE.md",
    "GEMINI.md",
    ".github/copilot-instructions.md",
)
# Startup reads a few small files per known project; never an unbounded walk.
_MAX_KNOWN_PROJECTS = 25


def known_project_roots(home: Path | None = None) -> list[Path]:
    """Projects Vesta was activated in or opened: the consent manifest's root
    and the desktop app's recent workspaces, existing directories only."""

    base = _user_home(home)
    candidates: list[str] = []
    try:
        manifest = json.loads(_read(home_item(base, "global.json")) or "{}")
        if isinstance(manifest, dict) and manifest.get("project_root"):
            candidates.append(str(manifest["project_root"]))
    except ValueError:
        pass
    try:
        recents = json.loads(_read(home_item(base, "gui_workspaces.json")) or "[]")
        if isinstance(recents, list):
            candidates.extend(str(entry) for entry in recents)
    except ValueError:
        pass
    roots: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        try:
            path = Path(candidate).expanduser()
            key = _norm(path)
            if key in seen or not path.is_dir():
                continue
        except (OSError, RuntimeError, ValueError):
            continue
        seen.add(key)
        roots.append(path)
        if len(roots) >= _MAX_KNOWN_PROJECTS:
            break
    return roots


def has_legacy_instruction_block(path: Path) -> bool:
    text = _read(path)
    return text is not None and LEGACY_START_MARKER in text


def projects_with_legacy_blocks(home: Path | None = None) -> list[Path]:
    """Known projects whose instruction files still hold a pre-rename block.

    Those blocks tell every agent in the project to run commands that no
    longer exist, and activation -- which would rewrite them -- may never run
    there again.
    """

    return [
        root
        for root in known_project_roots(home)
        if any(
            has_legacy_instruction_block(root / name)
            for name in PROJECT_INSTRUCTION_FILES
        )
    ]


def legacy_global_artifacts_present(home: Path | None = None) -> bool:
    """Cheap startup probe: did an old install leave wrappers or instructions?"""

    base = _user_home(home)
    for root in (base / HOME_DIRNAME, base / LEGACY_HOME_DIRNAME):
        if (root / "instructions" / LEGACY_INSTRUCTIONS_FILENAME).is_file():
            return True
        bin_dir = root / "bin"
        for tool in WRAPPER_TOOLS:
            for suffix in (".ps1", ""):
                if is_legacy_wrapper(
                    bin_dir / f"{LEGACY_WRAPPER_PREFIX}{tool}{suffix}"
                ):
                    return True
    return False


# --------------------------------------------------------------------------- #
# Python environment: the old distribution beside the new one
# --------------------------------------------------------------------------- #


def remove_legacy_distribution(
    *, distribution: Callable[[str], Any] | None = None
) -> dict[str, Any]:
    """Remove what the old ``opai`` distribution still installs beside ``vesta``.

    pip treats the renamed distribution as unrelated, so an upgrade leaves the
    old one installed: its launchers import a package that no longer exists
    (or, from a wheel, keep running the old code), and ``pip uninstall opai``
    would delete the launchers both record. Only files the old record lists
    and the new one does not are removed, inside that environment; its
    metadata goes last, so a file in use keeps it for the next start. Never
    raises.
    """

    import importlib.metadata

    find = distribution or importlib.metadata.distribution
    try:
        old = find(LEGACY_PACKAGE)
    except Exception:  # noqa: BLE001 - not installed
        return {"status": "absent"}
    try:
        new = find("vesta")
        old_files = list(old.files or [])
        new_files = list(new.files or [])
        if not old_files or not new_files:
            return {"status": "no_record"}
        keep = {_norm(Path(new.locate_file(item))) for item in new_files}
        base = Path(old.locate_file("")).resolve()
        site = _norm(base)
        # Launchers live outside site-packages; only a directory vesta's own
        # record also installs into counts as part of this environment.
        script_dirs = {
            _norm(Path(new.locate_file(item)).parent)
            for item in new_files
            if not _contains(site, _norm(Path(new.locate_file(item))))
        }
        metadata: list[Path] = []
        removed: list[str] = []
        failed = 0
        for item in old_files:
            path = Path(old.locate_file(item))
            key = _norm(path)
            inside = _contains(site, key) or _norm(path.parent) in script_dirs
            if key in keep or not inside:
                continue
            if item.parts and item.parts[0].endswith(".dist-info"):
                metadata.append(path)
                continue
            if not os.path.lexists(path) or path.is_dir():
                continue
            try:
                path.unlink()
                removed.append(str(path))
            except OSError:
                failed += 1
        if failed:
            _log(f"legacy distribution: {failed} file(s) in use; retrying next start")
            return {"status": "partial", "removed": len(removed)}
        for path in metadata:
            _unlink(path, removed)
        for directory in sorted(
            {Path(entry).parent for entry in removed},
            key=lambda item: len(item.parts),
            reverse=True,
        ):
            while _contains(site, _norm(directory)) and _norm(directory) != site:
                _rmdir_quietly(directory)
                if os.path.lexists(directory):
                    break
                directory = directory.parent
    except Exception:  # noqa: BLE001 - startup must never fail here
        return {"status": "failed"}
    return {"status": "removed", "removed": len(removed)}


# --------------------------------------------------------------------------- #
# Startup
# --------------------------------------------------------------------------- #


def run_startup_migrations(
    *,
    environ: MutableMapping[str, str] | None = None,
    home: Path | None = None,
) -> dict[str, Any]:
    """Environment then home-folder migration, for the application entry points.

    Never raises; returns what happened so a caller may follow up (for example
    by re-rendering integrations an old install left behind).
    """

    report: dict[str, Any] = {
        "adopted_env": [],
        "home": None,
        "legacy_integrations": False,
        "legacy_project_blocks": [],
    }
    try:
        report["adopted_env"] = adopt_legacy_environment(environ)
    except Exception:  # noqa: BLE001
        pass
    try:
        report["home"] = migrate_home(home).to_dict()
    except Exception:  # noqa: BLE001
        pass
    try:
        report["legacy_integrations"] = legacy_global_artifacts_present(home)
    except Exception:  # noqa: BLE001
        pass
    try:
        report["legacy_project_blocks"] = [
            str(root) for root in projects_with_legacy_blocks(home)
        ]
    except Exception:  # noqa: BLE001
        pass
    return report
