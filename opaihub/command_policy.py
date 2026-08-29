"""What a command *does*, and which autonomy levels may run it unattended.

This replaces the old five-subcommand allowlist in :mod:`opaihub.safety_gates`
(``normalize_autonomous_command``), which answered the wrong question. That gate
asked "is this exact spelling on my list?", so every command in existence except
``git status/diff/log/show/rev-parse`` was refused — including ``cat``, ``ls``,
``grep``, the project's own test runner, and ``git commit``. The command-policy
store already classified those as ``allow``; the executor ignored that verdict
and hard-blocked them anyway, so the store's ``allow`` branch was dead code and
users saw a wall of "COMMAND BLOCKED" for read-only work.

The question that actually matters is what a command *does*:

``READ``
    Observes state and changes nothing. Safe at every autonomy level, including
    read-only ones, because there is nothing to undo.
``WRITE_LOCAL``
    Changes the working tree or local repository. Recoverable from the repo
    itself (``git restore``, ``git reset``, re-running a build).
``WRITE_REMOTE``
    Changes state other people can see — a push, a PR, an issue comment. Not
    silently recoverable, but not catastrophic either.
``DESTRUCTIVE``
    Discards work irreversibly or is very hard to undo: force-push, history
    rewrite, ``rm -rf``, repository deletion.

Capabilities are ordered, and a pipeline takes the maximum of its segments, so
``cat x | head`` stays READ while ``cat x > f`` is WRITE_LOCAL. An unrecognised
command is *not* silently promoted to READ: it reports ``unknown`` and the
autonomy matrix decides, which is how new tooling stays usable without inventing
a safety claim the classifier cannot support.

The matrix in :data:`AUTONOMY_RULES` is the single place a run mode is turned
into run/ask/block, so the GUI permissions panel, the CLI and the tool executor
cannot drift apart about what a mode means.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import IntEnum
from typing import Iterable, Sequence


class Capability(IntEnum):
    """What a command changes. Ordered: a pipeline takes the maximum."""

    READ = 0
    WRITE_LOCAL = 1
    WRITE_REMOTE = 2
    DESTRUCTIVE = 3


CAPABILITY_LABELS: dict[Capability, str] = {
    Capability.READ: "reads state without changing it",
    Capability.WRITE_LOCAL: "changes local files or repository state",
    Capability.WRITE_REMOTE: "changes shared or remote state",
    Capability.DESTRUCTIVE: "discards work irreversibly",
}

# --- Autonomy levels ---------------------------------------------------------
#
# Named after Claude Code's mode set so the two tools mean the same thing by the
# same word. ``AUTO_EDITS`` mirrors "auto-accept edits": local work proceeds,
# outward-facing work still asks. ``BYPASS`` mirrors
# ``--dangerously-skip-permissions``: nothing is withheld.

PLAN = "plan"
NORMAL = "normal"
AUTO_EDITS = "auto-edits"
BYPASS = "bypass"

AUTONOMY_LEVELS: tuple[str, ...] = (PLAN, NORMAL, AUTO_EDITS, BYPASS)
DEFAULT_AUTONOMY = NORMAL

RUN = "run"
ASK = "ask"
BLOCK = "block"

AUTONOMY_RULES: dict[str, dict[Capability, str]] = {
    PLAN: {
        Capability.READ: RUN,
        Capability.WRITE_LOCAL: BLOCK,
        Capability.WRITE_REMOTE: BLOCK,
        Capability.DESTRUCTIVE: BLOCK,
    },
    NORMAL: {
        Capability.READ: RUN,
        Capability.WRITE_LOCAL: ASK,
        Capability.WRITE_REMOTE: ASK,
        Capability.DESTRUCTIVE: ASK,
    },
    AUTO_EDITS: {
        Capability.READ: RUN,
        Capability.WRITE_LOCAL: RUN,
        Capability.WRITE_REMOTE: ASK,
        Capability.DESTRUCTIVE: ASK,
    },
    # Bypass is the deliberate "get out of my way" level. It is not a default
    # and cannot be reached by accident: the caller has to select it explicitly,
    # exactly like Claude Code's --dangerously-skip-permissions flag.
    BYPASS: {
        Capability.READ: RUN,
        Capability.WRITE_LOCAL: RUN,
        Capability.WRITE_REMOTE: RUN,
        Capability.DESTRUCTIVE: RUN,
    },
}

# An unrecognised command is treated as this capability when deciding. It is a
# deliberate middle: high enough that a read-only level refuses it, low enough
# that an unknown build tool is one confirmation away rather than a dead end.
UNKNOWN_CAPABILITY = Capability.WRITE_LOCAL


# --- Shell decomposition -----------------------------------------------------

_SEGMENT_SPLIT = re.compile(r"\|\||&&|[|;\n]")
_SUBSTITUTION = re.compile(r"\$\(([^()]*)\)|`([^`]*)`")
# A redirection that writes somewhere real. /dev/null and NUL discard output, so
# they are not writes; `2>/dev/null` on a read command must stay READ.
_REDIRECT = re.compile(r"(?<![0-9])>>?\s*([^\s;|&]+)|[0-9]>>?\s*([^\s;|&]+)")
_DISCARD_TARGETS = {"/dev/null", "nul", "$null", "/dev/stdout", "/dev/stderr"}


def _iter_segments(raw: str) -> list[str]:
    """Split a command line into independently-classifiable segments."""

    text = str(raw or "")
    inner: list[str] = []
    for match in _SUBSTITUTION.finditer(text):
        captured = match.group(1) or match.group(2) or ""
        if captured.strip():
            inner.append(captured.strip())
    text = _SUBSTITUTION.sub(" ", text)
    segments = [part.strip() for part in _SEGMENT_SPLIT.split(text)]
    return [part for part in [*segments, *inner] if part]


def _writes_via_redirect(segment: str) -> bool:
    for match in _REDIRECT.finditer(segment):
        target = (match.group(1) or match.group(2) or "").strip().strip("\"'")
        if target and target.lower() not in _DISCARD_TARGETS:
            return True
    return False


def _tokens(segment: str) -> list[str]:
    """Best-effort argv for one segment; never raises on unbalanced quotes."""

    import shlex

    try:
        return shlex.split(segment, posix=True)
    except ValueError:
        return segment.split()


def _strip_wrappers(tokens: list[str]) -> list[str]:
    """Drop env assignments and shell/tool wrappers to expose the real command."""

    result = list(tokens)
    changed = True
    while changed and result:
        changed = False
        head = result[0].lower()
        base = head.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        if base.endswith(".exe"):
            base = base[:-4]
        if "=" in result[0] and not result[0].startswith("-"):
            result = result[1:]
            changed = True
        elif base in {"env", "command", "nohup", "time", "nice", "stdbuf"}:
            result = result[1:]
            changed = True
        elif base in {"sudo", "doas"}:
            # Privilege escalation is itself worth surfacing; keep the token so
            # the caller can see it, but expose the inner command for matching.
            result = result[1:]
            changed = True
        elif base in {"bash", "sh", "zsh", "dash", "fish", "cmd", "powershell", "pwsh"}:
            for index, token in enumerate(result[1:], start=1):
                if token.lower() in {"-c", "/c", "-command", "/k"}:
                    inner = result[index + 1 :]
                    if inner:
                        result = _tokens(" ".join(inner)) if len(inner) == 1 else inner
                        changed = True
                    break
            else:
                break
    return result


# --- Command tables ----------------------------------------------------------

_READ_EXECUTABLES = frozenset(
    {
        "cat",
        "bat",
        "head",
        "tail",
        "less",
        "more",
        "nl",
        "wc",
        "ls",
        "dir",
        "tree",
        "stat",
        "file",
        "du",
        "df",
        "pwd",
        "cd",
        "echo",
        "printf",
        "basename",
        "dirname",
        "realpath",
        "readlink",
        "grep",
        "egrep",
        "fgrep",
        "rg",
        "ripgrep",
        "ag",
        "ack",
        "find",
        "fd",
        "locate",
        "which",
        "where",
        "whereis",
        "type",
        "sort",
        "uniq",
        "cut",
        "tr",
        "column",
        "diff",
        "cmp",
        "comm",
        "join",
        "paste",
        "date",
        "whoami",
        "hostname",
        "uname",
        "id",
        "groups",
        "env",
        "printenv",
        "set",
        "history",
        "man",
        "help",
        "true",
        "false",
        "sleep",
        "jq",
        "yq",
        "xmllint",
        "md5sum",
        "sha1sum",
        "sha256sum",
        "cksum",
        "base64",
        "od",
        "xxd",
        "strings",
        "ps",
        "top",
        "free",
        "uptime",
        "getconf",
        "locale",
        "tty",
    }
)

# Read-only only when they are not asked to modify anything in place.
_CONDITIONAL_READ: dict[str, tuple[str, ...]] = {
    "sed": ("-i", "--in-place"),
    "perl": ("-i",),
    "awk": (),
    "gawk": (),
    "python": (),
    "python3": (),
    "node": (),
}

_GIT_READ = frozenset(
    {
        "status",
        "diff",
        "log",
        "show",
        "rev-parse",
        "rev-list",
        "merge-tree",
        "ls-files",
        "ls-tree",
        "ls-remote",
        "cat-file",
        "describe",
        "blame",
        "annotate",
        "shortlog",
        "reflog",
        "whatchanged",
        "show-ref",
        "for-each-ref",
        "symbolic-ref",
        "name-rev",
        "check-ignore",
        "check-attr",
        "count-objects",
        "verify-commit",
        "verify-tag",
        "diff-tree",
        "diff-index",
        "diff-files",
        "grep",
        "var",
        "version",
        "help",
        "cherry",
        "range-diff",
        "difftool",
        "instaweb",
        "bisect",
    }
)

_GIT_WRITE_LOCAL = frozenset(
    {
        "add",
        "commit",
        "checkout",
        "switch",
        "restore",
        "merge",
        "rebase",
        "cherry-pick",
        "revert",
        "stash",
        "apply",
        "am",
        "mv",
        "init",
        "clone",
        "fetch",
        "pull",
        "gc",
        "repack",
        "prune",
        "notes",
        "submodule",
        "sparse-checkout",
        "update-index",
        "read-tree",
        "write-tree",
        "commit-tree",
        "hash-object",
        "mktree",
        "maintenance",
        "bundle",
    }
)

_GIT_WRITE_REMOTE = frozenset({"push", "send-email", "request-pull", "svn"})

_GIT_DESTRUCTIVE_SUBCOMMANDS = frozenset({"filter-branch", "filter-repo"})

_GH_READ_SUBCOMMANDS = frozenset(
    {"view", "list", "status", "diff", "checks", "browse", "search", "ready"}
)
_GH_DESTRUCTIVE = (
    (("repo",), ("delete", "archive", "rename")),
    (("release",), ("delete",)),
    (("cache",), ("delete",)),
    (("secret",), ("delete",)),
    (("run",), ("delete",)),
)

# Non-git tools that reach outside the machine when they mutate.
_REMOTE_PUBLISH = (
    ("npm", "publish"),
    ("yarn", "publish"),
    ("pnpm", "publish"),
    ("twine", "upload"),
    ("cargo", "publish"),
    ("gem", "push"),
    ("docker", "push"),
    ("terraform", "apply"),
    ("terraform", "destroy"),
    ("kubectl", "apply"),
    ("kubectl", "delete"),
    ("helm", "install"),
    ("helm", "upgrade"),
    ("vercel", "deploy"),
    ("wrangler", "deploy"),
    ("az", "repos"),
    ("az", "pipelines"),
    ("glab", "mr"),
    ("hub", "pull-request"),
)

_BUILD_TOOLS = frozenset(
    {
        "npm",
        "npx",
        "yarn",
        "pnpm",
        "bun",
        "deno",
        "pytest",
        "tox",
        "nox",
        "make",
        "cmake",
        "ninja",
        "cargo",
        "go",
        "dotnet",
        "mvn",
        "gradle",
        "gradlew",
        "rake",
        "bundle",
        "composer",
        "pip",
        "pip3",
        "poetry",
        "uv",
        "pipenv",
        "ruff",
        "black",
        "isort",
        "mypy",
        "flake8",
        "pylint",
        "eslint",
        "prettier",
        "tsc",
        "vitest",
        "jest",
        "playwright",
        "cypress",
        "gcc",
        "g++",
        "clang",
        "javac",
        "swift",
        "rustc",
        "docker",
        "podman",
    }
)

_DESTRUCTIVE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\brm\s+(?:-[a-z]*[rf][a-z]*\s+)+", re.I), "recursive/forced delete"),
    (re.compile(r"\brmdir\s+/s\b", re.I), "recursive directory delete"),
    (re.compile(r"\bdel\s+/[sq]\b", re.I), "recursive delete"),
    (re.compile(r"\bremove-item\b[^\n]*-recurse", re.I), "recursive delete"),
    (re.compile(r"\bshutil\.rmtree\b", re.I), "recursive delete"),
    (re.compile(r"\bmkfs\b|\bfdisk\b|\bdiskpart\b", re.I), "disk formatting"),
    (re.compile(r"\bdd\s+[^\n]*\bof=", re.I), "raw disk write"),
    (re.compile(r"\bchmod\s+-R\s+0?777\b", re.I), "world-writable permissions"),
    (re.compile(r":\(\)\s*\{.*\};\s*:", re.S), "fork bomb"),
    (
        re.compile(
            r"\b(?:curl|wget|iwr|invoke-webrequest)\b[^\n]*\|\s*(?:sudo\s+)?(?:sh|bash|zsh|iex|python)\b",
            re.I,
        ),
        "download-and-execute",
    ),
    # Piping anything into an interpreter is arbitrary code execution whose
    # content the classifier cannot see. Splitting on "|" first would classify
    # the harmless-looking producer and a bare `sh` separately and miss it.
    (
        re.compile(
            r"\|\s*(?:sudo\s+)?(?:sh|bash|zsh|dash|fish|python[0-9.]*|perl|ruby|node"
            r"|iex|invoke-expression)\b",
            re.I,
        ),
        "pipes into an interpreter, executing unreviewed code",
    ),
)

# Git global options that change *what repository or configuration* a command
# runs against. They can smuggle an alias or redirect the operation outside the
# repo, so the subcommand alone no longer describes what happens.
_GIT_GLOBAL_WITH_VALUE = frozenset(
    {"-c", "-C", "--git-dir", "--work-tree", "--exec-path", "--namespace"}
)


def _split_git_globals(arguments: Sequence[str]) -> tuple[list[str], list[str]]:
    """Separate git's global options from the subcommand and its arguments."""

    globals_seen: list[str] = []
    index = 0
    items = [str(a) for a in arguments]
    while index < len(items):
        token = items[index]
        lowered = token.lower()
        if lowered in {
            "--no-pager",
            "-p",
            "--paginate",
            "--no-replace-objects",
            "--bare",
        }:
            index += 1
            continue
        if lowered in _GIT_GLOBAL_WITH_VALUE:
            globals_seen.append(token)
            if index + 1 < len(items):
                globals_seen.append(items[index + 1])
            index += 2
            continue
        if token.startswith("--") and "=" in token:
            name = lowered.split("=", 1)[0]
            if name in _GIT_GLOBAL_WITH_VALUE:
                globals_seen.append(token)
                index += 1
                continue
        break
    return globals_seen, items[index:]


@dataclass(frozen=True)
class CommandVerdict:
    """What a command line does, and why the classifier thinks so."""

    capability: Capability
    reason: str
    unknown: bool = False
    segments: tuple[str, ...] = ()

    @property
    def label(self) -> str:
        return CAPABILITY_LABELS[self.capability]


@dataclass(frozen=True)
class CommandDecision:
    """Whether this command may run now at this autonomy level."""

    action: str
    capability: Capability
    reason: str
    autonomy: str
    unknown: bool = False

    @property
    def allowed(self) -> bool:
        return self.action == RUN

    @property
    def needs_approval(self) -> bool:
        return self.action == ASK


def _base_name(token: str) -> str:
    name = str(token or "").strip().strip("\"'")
    name = name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1].lower()
    if name.endswith(".exe") or name.endswith(".cmd") or name.endswith(".bat"):
        name = name.rsplit(".", 1)[0]
    return name


def _git_capability(arguments: Sequence[str]) -> tuple[Capability, str]:
    options = [a for a in arguments if a.startswith("-")]
    positional = [a for a in arguments if not a.startswith("-")]
    subcommand = _base_name(positional[0]) if positional else ""
    joined = " ".join(arguments).lower()

    forced = any(
        option in {"-f", "--force"} or option.startswith("--force-with-lease")
        for option in options
    )
    if subcommand == "push":
        if forced or "--delete" in options or "-d" in options:
            return Capability.DESTRUCTIVE, "force/delete push rewrites remote history"
        if re.search(r"(?:^|\s):\S", joined):
            return Capability.DESTRUCTIVE, "refspec deletes a remote branch"
        return Capability.WRITE_REMOTE, "publishes commits to a remote"
    if subcommand in _GIT_DESTRUCTIVE_SUBCOMMANDS:
        return Capability.DESTRUCTIVE, "rewrites repository history"
    if subcommand == "reset" and "--hard" in options:
        return Capability.DESTRUCTIVE, "discards uncommitted work"
    if subcommand == "clean" and any(
        set(option[1:]) & {"f", "x", "d"}
        for option in options
        if not option.startswith("--")
    ):
        return Capability.DESTRUCTIVE, "deletes untracked files"
    if subcommand == "branch" and ("-D" in arguments or "--delete" in options):
        return Capability.DESTRUCTIVE, "deletes a branch"
    if subcommand == "update-ref" and "-d" in options:
        return Capability.DESTRUCTIVE, "deletes a ref"
    if subcommand == "checkout" and ("-f" in options or "--force" in options):
        return Capability.DESTRUCTIVE, "overwrites local modifications"
    if subcommand == "stash" and positional[1:2] == ["drop"]:
        return Capability.DESTRUCTIVE, "discards stashed work"
    if (
        subcommand == "remote"
        and positional[1:2]
        and positional[1]
        in {
            "add",
            "remove",
            "rm",
            "set-url",
            "rename",
        }
    ):
        return Capability.WRITE_LOCAL, "changes remote configuration"
    if subcommand == "branch" and not positional[1:]:
        return Capability.READ, "lists branches"
    if subcommand == "branch":
        return Capability.WRITE_LOCAL, "creates or moves a branch"
    if subcommand == "config":
        if "--get" in options or "--list" in options or "-l" in options:
            return Capability.READ, "reads git configuration"
        return Capability.WRITE_LOCAL, "changes git configuration"
    if subcommand == "tag" and (
        not positional[1:] or "-l" in options or "--list" in options
    ):
        return Capability.READ, "lists tags"
    if subcommand == "tag":
        return Capability.WRITE_LOCAL, "creates or deletes a tag"
    if subcommand == "stash" and (not positional[1:] or positional[1] == "list"):
        return Capability.READ, "lists stashes"
    if subcommand == "worktree" and positional[1:2] == ["list"]:
        return Capability.READ, "lists worktrees"
    if subcommand in _GIT_READ:
        return Capability.READ, "reads repository state"
    if subcommand in _GIT_WRITE_REMOTE:
        return Capability.WRITE_REMOTE, "changes remote state"
    if subcommand in _GIT_WRITE_LOCAL:
        return Capability.WRITE_LOCAL, "changes local repository state"
    return Capability.WRITE_LOCAL, f"unrecognised git subcommand '{subcommand}'"


def _gh_capability(arguments: Sequence[str]) -> tuple[Capability, str]:
    positional = [_base_name(a) for a in arguments if not a.startswith("-")]
    noun = positional[0] if positional else ""
    verb = positional[1] if len(positional) > 1 else ""
    for nouns, verbs in _GH_DESTRUCTIVE:
        if noun in nouns and verb in verbs:
            return Capability.DESTRUCTIVE, f"gh {noun} {verb} is not reversible"
    if noun == "api":
        method = ""
        for index, token in enumerate(arguments):
            if token in {"-X", "--method"} and index + 1 < len(arguments):
                method = arguments[index + 1].upper()
        if method in {"DELETE"}:
            return Capability.DESTRUCTIVE, "gh api DELETE removes remote data"
        if method in {"POST", "PATCH", "PUT"}:
            return Capability.WRITE_REMOTE, "gh api writes to the remote"
        return Capability.READ, "gh api read request"
    if verb in _GH_READ_SUBCOMMANDS or noun in {"auth", "config", "alias", "extension"}:
        return Capability.READ, "reads from the forge"
    if not verb:
        return Capability.READ, "gh help/listing"
    return Capability.WRITE_REMOTE, f"gh {noun} {verb} changes shared state"


def _segment_capability(segment: str) -> tuple[Capability, str, bool]:
    for pattern, why in _DESTRUCTIVE_PATTERNS:
        if pattern.search(segment):
            return Capability.DESTRUCTIVE, why, False

    tokens = _strip_wrappers(_tokens(segment))
    if not tokens:
        return Capability.READ, "empty segment", False
    executable = _base_name(tokens[0])
    arguments = tokens[1:]

    if executable == "git":
        globals_seen, rest = _split_git_globals(arguments)
        capability, why = _git_capability(rest)
        lowered_globals = " ".join(globals_seen).lower()
        if "alias." in lowered_globals:
            # An injected alias can expand to anything, including a push, so
            # the visible subcommand proves nothing about what will run.
            return (
                max(capability, Capability.WRITE_REMOTE),
                "git: -c alias injection can expand to any operation",
                True,
            )
        if globals_seen:
            return (
                max(capability, UNKNOWN_CAPABILITY),
                f"git: global option {globals_seen[0]} redirects the operation",
                True,
            )
        return capability, f"git: {why}", False
    if executable in {"gh", "glab"}:
        capability, why = _gh_capability(arguments)
        return capability, f"{executable}: {why}", False

    for tool, subcommand in _REMOTE_PUBLISH:
        if executable == tool and any(_base_name(a) == subcommand for a in arguments):
            return (
                Capability.WRITE_REMOTE,
                f"{tool} {subcommand} affects remote state",
                False,
            )

    if executable in _READ_EXECUTABLES:
        if _writes_via_redirect(segment):
            return Capability.WRITE_LOCAL, f"{executable} redirected into a file", False
        return Capability.READ, f"{executable} reads without changing state", False

    if executable in _CONDITIONAL_READ:
        writing_flags = _CONDITIONAL_READ[executable]
        if any(a.split("=")[0] in writing_flags for a in arguments):
            return Capability.WRITE_LOCAL, f"{executable} edits in place", False
        if _writes_via_redirect(segment):
            return Capability.WRITE_LOCAL, f"{executable} redirected into a file", False
        # An interpreter can do anything; only a version/help probe is provably
        # read-only, so anything else stays unknown rather than claiming safety.
        if arguments and arguments[0] in {"--version", "-V", "--help", "-h"}:
            return Capability.READ, f"{executable} version probe", False
        return UNKNOWN_CAPABILITY, f"{executable} can run arbitrary code", True

    if executable in _BUILD_TOOLS:
        return Capability.WRITE_LOCAL, f"{executable} may write build output", False

    if _writes_via_redirect(segment):
        return Capability.WRITE_LOCAL, f"{executable} redirected into a file", True
    return UNKNOWN_CAPABILITY, f"unrecognised command '{executable}'", True


def classify_command_capability(raw: str | Iterable[str]) -> CommandVerdict:
    """Classify a whole command line, taking the maximum over its segments."""

    if not isinstance(raw, str):
        raw = " ".join(str(part) for part in raw)
    segments = _iter_segments(raw)
    if not segments:
        return CommandVerdict(Capability.READ, "empty command", False, ())

    # Whole-line patterns must run before segmentation: download-and-execute is
    # defined by the pipe joining two otherwise-ordinary commands, so splitting
    # on `|` first would hide exactly the shape being detected.
    for pattern, why in _DESTRUCTIVE_PATTERNS:
        if pattern.search(raw):
            return CommandVerdict(Capability.DESTRUCTIVE, why, False, tuple(segments))

    worst = Capability.READ
    reason = ""
    unknown = False
    for segment in segments:
        capability, why, segment_unknown = _segment_capability(segment)
        unknown = unknown or segment_unknown
        if capability >= worst:
            worst, reason = capability, why
    return CommandVerdict(worst, reason, unknown, tuple(segments))


def normalize_autonomy(value: str | None) -> str:
    """Map any run-mode spelling onto a canonical autonomy level."""

    text = str(value or "").strip().lower().replace("_", "-")
    if text in AUTONOMY_LEVELS:
        return text
    # Legacy OPai run-mode ids, kept working so stored preferences and older
    # callers keep meaning what they used to mean.
    legacy = {
        "ask": PLAN,
        "plan": PLAN,
        "safe-auto": NORMAL,
        "approve-edits": NORMAL,
        "auto-accept-edits": AUTO_EDITS,
        "auto": AUTO_EDITS,
        "full-auto": BYPASS,
        "auto-apply": BYPASS,
        "dangerously-skip-permissions": BYPASS,
        "yolo": BYPASS,
    }
    return legacy.get(text, DEFAULT_AUTONOMY)


def decide_command(
    raw: str | Iterable[str],
    *,
    autonomy: str | None = None,
) -> CommandDecision:
    """Decide whether ``raw`` runs, asks, or is refused at this autonomy level."""

    level = normalize_autonomy(autonomy)
    verdict = classify_command_capability(raw)
    capability = verdict.capability
    if verdict.unknown and capability < UNKNOWN_CAPABILITY:
        capability = UNKNOWN_CAPABILITY
    action = AUTONOMY_RULES[level][capability]
    return CommandDecision(
        action=action,
        capability=capability,
        reason=verdict.reason,
        autonomy=level,
        unknown=verdict.unknown,
    )
