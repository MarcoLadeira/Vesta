"""Deterministic pre-ship gates for coding-agent changes."""

from __future__ import annotations

import os
import re
import stat
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Iterable, Sequence

_SECRET = re.compile(
    r"(?:sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|(?:api[_-]?key|token|secret)\s*[=:]\s*[^\s]{8,})",
    re.IGNORECASE,
)
_RISKY_NAMES = {
    ".env",
    ".env.local",
    ".env.production",
    "credentials.json",
    "permissions.yaml",
}


def is_destructive_command(command: Iterable[str]) -> bool:
    """Catch destructive Git/filesystem operations, including shell wrappers.

    ``gh`` mutations (issue/pr close, comment, merge, ...) and plain
    ``git push`` change shared remote state, so they are destructive even
    without force flags (F23). ``git commit`` stays out: it is local and
    undoable, and the confirm-policy registry already gates it.
    """

    text = " ".join(str(item) for item in command).lower()
    patterns = (
        r"\bgit\s+reset\s+--hard\b",
        r"\bgit\s+clean\b",
        r"\bgit\s+push\b",
        r"\bgit\s+branch\s+(?:-d|-D|--delete)\b",
        r"\bgh\s+issue\s+(?:close|comment|edit|delete|create|reopen)\b",
        r"\bgh\s+pr\s+(?:close|merge|comment|create|edit|review)\b",
        r"\bgh\s+release\s+(?:create|delete|edit)\b",
        r"\bgh\s+api\b",
        r"\bgh\s+repo\s+(?:delete|archive)\b",
        r"\brm\s+-rf\b",
        r"\brmdir\s+/s\b",
        r"\bremove-item\b[^\r\n]*-recurse\b",
        r"\bdel\s+/[sq]\b",
    )
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)


# --- Autonomous command capability gate --------------------------------------
#
# ``run_command`` is intentionally much narrower than a shell. Builds and tests
# have dedicated, fixed-command tools; GitHub/network work has consent-aware
# connector tools. This gate therefore accepts only a small set of local Git
# reads and returns canonical argv for execution. A denylist is insufficient:
# executable aliases, wrappers, Git aliases and new subcommands otherwise create
# bypasses whenever one spelling is missed.

_SHELL_OPERATORS = re.compile(r"[|&;<>`\n\r]|\$\(|\$\{|>>|<<")
_LOCAL_GIT_READS = frozenset({"status", "diff", "log", "show", "rev-parse"})
_SAFE_GIT_EXACT_OPTIONS: dict[str, frozenset[str]] = {
    "status": frozenset(
        {
            "--short",
            "-s",
            "--branch",
            "-b",
            "-sb",
            "--show-stash",
            "--porcelain",
            "--long",
            "--verbose",
            "-v",
            "--ignored",
            "--no-column",
            "--ahead-behind",
            "--no-ahead-behind",
            "--renames",
            "--no-renames",
            "-z",
        }
    ),
    "diff": frozenset(
        {
            "--cached",
            "--staged",
            "--patch",
            "-p",
            "-u",
            "--no-patch",
            "-s",
            "--raw",
            "--patch-with-raw",
            "--patch-with-stat",
            "--stat",
            "--numstat",
            "--shortstat",
            "--dirstat",
            "--summary",
            "--compact-summary",
            "--name-only",
            "--name-status",
            "--check",
            "--full-index",
            "--binary",
            "--no-color",
            "--minimal",
            "--patience",
            "--histogram",
            "--word-diff",
            "--ignore-space-at-eol",
            "--ignore-space-change",
            "--ignore-all-space",
            "--ignore-blank-lines",
            "--ignore-cr-at-eol",
            "-b",
            "-w",
            "--no-renames",
            "--relative",
            "--submodule",
            "--exit-code",
            "--quiet",
            "--merge-base",
            "--no-ext-diff",
            "--no-textconv",
        }
    ),
    "log": frozenset(
        {
            "--oneline",
            "--patch",
            "-p",
            "-u",
            "--no-patch",
            "-s",
            "--raw",
            "--stat",
            "--numstat",
            "--shortstat",
            "--summary",
            "--name-only",
            "--name-status",
            "--abbrev-commit",
            "--no-abbrev-commit",
            "--full-diff",
            "--graph",
            "--all",
            "--branches",
            "--tags",
            "--remotes",
            "--first-parent",
            "--merges",
            "--no-merges",
            "--reverse",
            "--topo-order",
            "--date-order",
            "--author-date-order",
            "--boundary",
            "--left-right",
            "--cherry-mark",
            "--cherry-pick",
            "--ancestry-path",
            "--simplify-merges",
            "--full-history",
            "--sparse",
            "--simplify-by-decoration",
            "--regexp-ignore-case",
            "-i",
            "--extended-regexp",
            "-E",
            "--fixed-strings",
            "-F",
            "--perl-regexp",
            "-P",
            "--remove-empty",
            "--follow",
            "--no-decorate",
            "--decorate",
            "--no-color",
            "--no-ext-diff",
            "--no-textconv",
        }
    ),
    "show": frozenset(
        {
            "--oneline",
            "--patch",
            "-p",
            "-u",
            "--no-patch",
            "-s",
            "--raw",
            "--stat",
            "--numstat",
            "--shortstat",
            "--summary",
            "--name-only",
            "--name-status",
            "--abbrev-commit",
            "--no-abbrev-commit",
            "--decorate",
            "--no-decorate",
            "--no-color",
            "--no-ext-diff",
            "--no-textconv",
        }
    ),
    "rev-parse": frozenset(
        {
            "--verify",
            "--quiet",
            "-q",
            "--symbolic",
            "--symbolic-full-name",
            "--abbrev-ref",
            "--show-toplevel",
            "--show-prefix",
            "--show-cdup",
            "--show-superproject-working-tree",
            "--git-dir",
            "--absolute-git-dir",
            "--is-inside-git-dir",
            "--is-inside-work-tree",
            "--is-bare-repository",
            "--show-object-format",
            "--local-env-vars",
            "--sq",
            "--sq-quote",
            "--revs-only",
            "--no-revs",
            "--flags",
            "--no-flags",
            "--end-of-options",
        }
    ),
}
_SAFE_GIT_OPTION_PREFIXES: dict[str, tuple[str, ...]] = {
    "status": (
        "--porcelain=",
        "--untracked-files=",
        "--ignored=",
        "--ignore-submodules=",
        "--column=",
        "--find-renames=",
    ),
    "diff": (
        "--stat=",
        "--dirstat=",
        "--abbrev=",
        "--unified=",
        "--inter-hunk-context=",
        "--diff-algorithm=",
        "--anchored=",
        "--word-diff=",
        "--word-diff-regex=",
        "--color-words=",
        "--ignore-matching-lines=",
        "--find-renames=",
        "--find-copies=",
        "--diff-filter=",
        "--relative=",
        "--submodule=",
        "--src-prefix=",
        "--dst-prefix=",
        "--line-prefix=",
    ),
    "log": (
        "--max-count=",
        "--format=",
        "--pretty=",
        "--abbrev=",
        "--decorate=",
        "--branches=",
        "--tags=",
        "--remotes=",
        "--since=",
        "--after=",
        "--until=",
        "--before=",
        "--author=",
        "--committer=",
        "--grep=",
        "--date=",
        "--diff-filter=",
    ),
    "show": (
        "--format=",
        "--pretty=",
        "--abbrev=",
        "--decorate=",
        "--date=",
        "--diff-filter=",
    ),
    "rev-parse": (
        "--short=",
        "--abbrev-ref=",
        "--path-format=",
        "--git-path=",
        "--resolve-git-dir=",
        "--default=",
    ),
}
_SAFE_LOG_COUNT = re.compile(r"^-(?:n)?\d+$")
_SAFE_DIFF_SHORT_OPTION = re.compile(r"^-(?:U\d+|M\d*%?|C\d*%?)$")


@dataclass(frozen=True)
class NormalizedCommand:
    """A canonical local-read command that is safe to hand to the ACI."""

    executable: str
    subcommand: str
    argv: tuple[str, ...]


def resolve_trusted_git_executable(
    repo_root: Path,
    *,
    path_value: str | None = None,
) -> str | None:
    """Resolve Git from absolute PATH entries, never from the active repository.

    This intentionally does not use ``shutil.which``: Windows executable search
    may consult the current directory before PATH. Symlinks are resolved and
    repository-contained or world-writable POSIX candidates are rejected.
    """

    repository = Path(repo_root).expanduser().resolve(strict=False)
    search_path = os.environ.get("PATH", "") if path_value is None else path_value
    names = ("git.exe",) if os.name == "nt" else ("git",)
    for entry in str(search_path or "").split(os.pathsep):
        raw_entry = entry.strip().strip('"')
        if not raw_entry:
            continue
        directory = Path(os.path.expandvars(raw_entry)).expanduser()
        if not directory.is_absolute():
            continue
        try:
            resolved_directory = directory.resolve(strict=True)
        except OSError:
            continue
        if os.name != "nt":
            try:
                if resolved_directory.stat().st_mode & stat.S_IWOTH:
                    continue
            except OSError:
                continue
        for name in names:
            try:
                candidate = (resolved_directory / name).resolve(strict=True)
            except OSError:
                continue
            try:
                candidate.relative_to(repository)
            except ValueError:
                pass
            else:
                continue
            if not candidate.is_file():
                continue
            if os.name != "nt" and not os.access(candidate, os.X_OK):
                continue
            return str(candidate)
    return None


def _is_executable_path(token: str) -> bool:
    return (
        "/" in token
        or "\\" in token
        or PurePosixPath(token).is_absolute()
        or PureWindowsPath(token).is_absolute()
    )


def _git_arguments_are_allowlisted(subcommand: str, arguments: Sequence[str]) -> bool:
    exact = _SAFE_GIT_EXACT_OPTIONS[subcommand]
    prefixes = _SAFE_GIT_OPTION_PREFIXES.get(subcommand, ())
    after_separator = False
    for raw_argument in arguments:
        argument = str(raw_argument)
        if after_separator:
            continue
        if argument == "--" or (
            subcommand == "rev-parse" and argument == "--end-of-options"
        ):
            after_separator = True
            continue
        if not argument.startswith("-") or argument == "-":
            continue
        if (
            subcommand in {"log", "show"}
            and argument.startswith(("--format=", "--pretty="))
            and "%G" in argument
        ):
            # Pretty-format GPG placeholders invoke the configured verifier.
            return False
        if argument in exact or any(argument.startswith(prefix) for prefix in prefixes):
            continue
        if subcommand == "log" and _SAFE_LOG_COUNT.fullmatch(argument):
            continue
        if subcommand == "diff" and _SAFE_DIFF_SHORT_OPTION.fullmatch(argument):
            continue
        if subcommand == "status" and re.fullmatch(r"-u(?:no|normal|all)?", argument):
            continue
        return False
    return True


def normalize_autonomous_command(
    raw: str,
    argv: Sequence[str],
) -> NormalizedCommand | None:
    """Return canonical argv for the explicit local Git-read allowlist.

    The executable itself must be a bare ``git``/``git.exe`` basename. Only
    ``--no-pager`` may precede the first effective subcommand. Git options that
    can execute helpers or write output files are rejected as an extra guard
    against repository-controlled configuration.
    """

    if not argv or _SHELL_OPERATORS.search(str(raw or "")):
        return None
    executable_token = str(argv[0]).strip()
    if not executable_token or _is_executable_path(executable_token):
        return None
    executable = executable_token.lower()
    if executable.endswith(".exe"):
        executable = executable[:-4]
    if executable != "git":
        return None

    arguments = [str(item) for item in argv[1:]]
    canonical: list[str] = ["git"]
    if arguments and arguments[0].lower() == "--no-pager":
        canonical.append("--no-pager")
        arguments.pop(0)
    if not arguments:
        return None

    subcommand = arguments.pop(0).lower()
    canonical.append(subcommand)
    if subcommand == "branch":
        if tuple(argument.lower() for argument in arguments) != ("--show-current",):
            return None
        canonical.append("--show-current")
    elif subcommand in _LOCAL_GIT_READS:
        if not _git_arguments_are_allowlisted(subcommand, arguments):
            return None
        if subcommand in {"diff", "log", "show"}:
            canonical.extend(("--no-ext-diff", "--no-textconv"))
        canonical.extend(arguments)
    else:
        return None
    return NormalizedCommand(executable, subcommand, tuple(canonical))


def classify_run_command(raw: str, argv: Sequence[str]) -> tuple[bool, str]:
    """Backwards-compatible classifier for the canonical capability gate."""

    if not argv:
        return False, "No command was given."
    if _SHELL_OPERATORS.search(str(raw or "")):
        return False, (
            "Shell operators (| & ; > < `` $()) are not supported. Run one "
            "command per call; chain steps as separate run_command calls."
        )
    if normalize_autonomous_command(raw, argv) is not None:
        return True, ""
    return False, (
        "Only bounded local Git reads are allowed: status, diff, log, show, "
        "rev-parse, or exactly branch --show-current (optional --no-pager). "
        "Use dedicated test/build and consent-aware GitHub tools for other work."
    )


def _normal(value: str) -> PurePosixPath:
    return PurePosixPath(str(value).replace("\\", "/").strip("/"))


def _overlap(left: PurePosixPath, right: PurePosixPath) -> bool:
    return left == right or left in right.parents or right in left.parents


@dataclass(frozen=True)
class SafetyGateReport:
    gates: dict[str, bool]
    failed: tuple[str, ...]
    details: dict[str, tuple[str, ...]]

    @property
    def can_ship(self) -> bool:
        return not self.failed

    def to_dict(self) -> dict[str, object]:
        value = asdict(self)
        value["can_ship"] = self.can_ship
        return value


def evaluate_safety_gates(
    *,
    changed_files: Iterable[str],
    intended_files: Iterable[str],
    command: Iterable[str] = (),
    diff_text: str = "",
    tests_pass: bool = False,
    correct_branch: bool = False,
    no_conflicts: bool = False,
    pr_checks_pass: bool = False,
    production_authorized: bool = False,
    risky_files_reviewed: bool = False,
) -> SafetyGateReport:
    changed = tuple(_normal(item) for item in changed_files)
    intended = tuple(_normal(item) for item in intended_files)
    unrelated = tuple(
        str(path)
        for path in changed
        if not any(_overlap(path, target) for target in intended)
    )
    risky = tuple(
        str(path)
        for path in changed
        if path.name.lower() in _RISKY_NAMES
        or str(path).startswith((".github/workflows/", "migrations/"))
    )
    destructive = is_destructive_command(command)
    production_paths = tuple(
        str(path)
        for path in changed
        if any(
            part in {"production", "prod", "auth", "credentials"} for part in path.parts
        )
    )
    gates = {
        "secrets": not bool(_SECRET.search(diff_text)),
        "risky_files": not risky or risky_files_reviewed,
        "destructive_command": not destructive,
        "production_auth": not production_paths or production_authorized,
        "unrelated_diff": not unrelated,
        "tests": bool(tests_pass),
        "correct_branch": bool(correct_branch),
        "conflicts": bool(no_conflicts),
        "pr_checks": bool(pr_checks_pass),
    }
    failed = tuple(name for name, passed in gates.items() if not passed)
    return SafetyGateReport(
        gates,
        failed,
        {
            "risky_files": risky,
            "unrelated_files": unrelated,
            "production_auth_files": production_paths,
        },
    )
