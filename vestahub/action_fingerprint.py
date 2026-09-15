"""Privacy-safe semantic action fingerprints for trajectory control (#649).

This module answers one narrow question: whether two proposed actions are the
same information-gathering or verification step despite different tool syntax.
It does not execute, retry, approve, or authorize anything.  In particular,
mutations remain under the exact identity and reconciliation rules from #616.

Only fixed vocabulary, numeric ranges, and one-way digests leave this module.
Repository paths, search text, prompts, source, command text, and secrets are
used transiently during derivation and are never retained on a fingerprint.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import posixpath
from pathlib import Path
import re
import shlex
from typing import Any, Mapping, Sequence


FINGERPRINT_VERSION = "semantic-action-v1"
_UNKNOWN_FRESHNESS = "unknown"


class EquivalenceLevel(str, Enum):
    """The five comparison outcomes required by issue #649."""

    EXACT_DUPLICATE = "exact_duplicate"
    SEMANTICALLY_EQUIVALENT = "semantically_equivalent"
    OVERLAPPING_OR_SUBSUMING = "overlapping_or_subsuming"
    RELATED_MATERIALLY_DIFFERENT = "related_but_materially_different"
    UNKNOWN = "unknown"


class SideEffectClass(str, Enum):
    """Effect boundary used only to decide whether suppression is safe."""

    READ_ONLY = "read_only"
    VERIFICATION = "verification"
    PROVIDER_CALL = "provider_call"
    LOCAL_MUTATION = "local_mutation"
    EXTERNAL_EFFECT = "external_effect"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ActionFingerprint:
    """A deterministic action identity containing no raw private values."""

    version: str
    operation_family: str
    capability: str
    target_digest: str
    arguments_digest: str
    repository_digest: str
    expected_effect_digest: str
    side_effect_class: SideEffectClass
    failure_class_digest: str
    plan_digest: str
    trajectory_digest: str
    semantic_digest: str
    exact_digest: str
    scope_start: int | None = None
    scope_end: int | None = None
    freshness_known: bool = False
    suppressible: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Return the public, JSON-safe representation.

        Do not replace this with ``dataclasses.asdict`` if private derivation
        fields are ever added.  This explicit projection is the privacy gate.
        """

        return {
            "version": self.version,
            "operation_family": self.operation_family,
            "capability": self.capability,
            "target_digest": self.target_digest,
            "arguments_digest": self.arguments_digest,
            "repository_digest": self.repository_digest,
            "expected_effect_digest": self.expected_effect_digest,
            "side_effect_class": self.side_effect_class.value,
            "failure_class_digest": self.failure_class_digest,
            "plan_digest": self.plan_digest,
            "trajectory_digest": self.trajectory_digest,
            "semantic_digest": self.semantic_digest,
            "exact_digest": self.exact_digest,
            "scope_start": self.scope_start,
            "scope_end": self.scope_end,
            "freshness_known": self.freshness_known,
            "suppressible": self.suppressible,
        }


@dataclass(frozen=True)
class _CanonicalAction:
    family: str
    capability: str
    target: Any
    arguments: Any
    side_effect: SideEffectClass
    scope_start: int | None = None
    scope_end: int | None = None
    recognized: bool = True


_LOCAL_MUTATIONS = frozenset(
    {
        "apply_patch",
        "write_file",
        "create_file",
        "delete_file",
        "git_create_branch",
        "git_add",
    }
)
_EXTERNAL_EFFECTS = frozenset(
    {
        "git_commit",
        "git_push",
        "open_pr",
        "github_comment",
        "github_request_review",
    }
)
_PROVIDER_ACTIONS = frozenset(
    {
        "provider_query",
        "provider_prompt",
        "model_call",
        "model_call_free",
        "model_call_paid",
    }
)
_PRESENTATION_FLAGS = frozenset(
    {
        "-q",
        "--quiet",
        "--no-cache",
        "--no-header",
        "--disable-warnings",
        "--silent",
    }
)
_PROSE_PUNCTUATION = re.compile(r"[^\w\s-]+", re.UNICODE)


def _digest(label: str, value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8", "replace")
    hasher = hashlib.sha256()
    hasher.update(FINGERPRINT_VERSION.encode("ascii"))
    hasher.update(b"\0")
    hasher.update(label.encode("ascii", "replace"))
    hasher.update(b"\0")
    hasher.update(encoded)
    return hasher.hexdigest()[:32]


def _mapping(arguments: Mapping[str, Any] | str | None) -> dict[str, Any]:
    if isinstance(arguments, Mapping):
        return {str(key): value for key, value in arguments.items()}
    if isinstance(arguments, str):
        try:
            parsed = json.loads(arguments)
        except (json.JSONDecodeError, TypeError):
            return {"_raw": arguments}
        if isinstance(parsed, Mapping):
            return {str(key): value for key, value in parsed.items()}
        return {"_value": parsed}
    return {}


def _stable_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _stable_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_stable_value(item) for item in value]
    if isinstance(value, set):
        return sorted((_stable_value(item) for item in value), key=repr)
    return value


def _normal_prose(value: Any) -> str:
    text = _PROSE_PUNCTUATION.sub(" ", str(value or "").casefold())
    return " ".join(text.split())


def _normal_path(value: Any, root: Path | None) -> str:
    raw = str(value or ".").strip().strip("\"'").replace("\\", "/")
    normalized = posixpath.normpath(raw or ".")
    root_text = ""
    if root is not None:
        root_text = posixpath.normpath(root.as_posix())
        case_insensitive = bool(re.match(r"^[a-zA-Z]:/", root_text))
        left = normalized.casefold() if case_insensitive else normalized
        base = (root_text.casefold() if case_insensitive else root_text).rstrip("/")
        if left == base:
            normalized = "."
        elif left.startswith(base + "/"):
            normalized = normalized[len(root_text.rstrip("/")) + 1 :]
    # Windows drive and UNC paths are case-insensitive.  Relative paths retain
    # case on case-sensitive repositories so distinct Linux files do not merge.
    if re.match(r"^[a-zA-Z]:/", raw) or raw.startswith("//"):
        normalized = normalized.casefold()
    return normalized.replace("\\", "/") or "."


def _normal_paths(values: Any, root: Path | None) -> tuple[str, ...]:
    if values is None or values == []:
        return (".",)
    if isinstance(values, (str, bytes)):
        values = (values,)
    if not isinstance(values, Sequence):
        values = (values,)
    return tuple(sorted({_normal_path(value, root) for value in values})) or (".",)


def _bounded_line(value: Any, default: int | None) -> int | None:
    if value is None:
        return default
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return default


def _file_read(
    path: Any,
    root: Path | None,
    *,
    start: Any = 1,
    end: Any = None,
) -> _CanonicalAction:
    first = _bounded_line(start, 1)
    last = _bounded_line(end, None)
    return _CanonicalAction(
        family="file_read",
        capability="read_source",
        target={"path": _normal_path(path, root)},
        arguments={"range": [first, last]},
        side_effect=SideEffectClass.READ_ONLY,
        scope_start=first,
        scope_end=last,
    )


def _search(
    query: Any,
    paths: Any,
    root: Path | None,
    *,
    capability: str = "search_text",
) -> _CanonicalAction:
    return _CanonicalAction(
        family="repository_search",
        capability=capability,
        target={"paths": _normal_paths(paths, root)},
        # Search matching may be case-sensitive, so only trim quote/spacing
        # syntax.  We do not case-fold or simplify the expression itself.
        arguments={"query": str(query or "").strip().strip("\"'")},
        side_effect=SideEffectClass.READ_ONLY,
    )


def _tokens(command: str) -> list[str]:
    try:
        # Preserve Windows separators. Value-specific canonicalizers strip
        # quotes later, so derivation stays deterministic on every host.
        return shlex.split(command, posix=False)
    except ValueError:
        return []


def _option_value(tokens: Sequence[str], *names: str) -> str | None:
    lowered = tuple(name.casefold() for name in names)
    for index, token in enumerate(tokens):
        lower = token.casefold()
        if lower in lowered and index + 1 < len(tokens):
            return tokens[index + 1]
        for name in lowered:
            if lower.startswith(name + "="):
                return token.split("=", 1)[1]
    return None


def _positional(tokens: Sequence[str], *, skip_flags: bool = True) -> list[str]:
    result: list[str] = []
    skip_next = False
    options_with_values = {
        "--color",
        "--glob",
        "-g",
        "--type",
        "-t",
        "--max-count",
        "-m",
    }
    for token in tokens:
        if skip_next:
            skip_next = False
            continue
        lower = token.casefold()
        if lower in options_with_values:
            skip_next = True
            continue
        if skip_flags and token.startswith("-"):
            continue
        result.append(token)
    return result


def _verification(
    capability: str, command: str, scope: Sequence[str]
) -> _CanonicalAction:
    normalized_scope = tuple(
        sorted(
            {
                item
                for item in (str(value).strip() for value in scope)
                if item and item not in _PRESENTATION_FLAGS
            }
        )
    ) or ("all",)
    return _CanonicalAction(
        family="verification",
        capability=capability,
        target={"runner": command},
        arguments={"scope": normalized_scope},
        side_effect=SideEffectClass.VERIFICATION,
    )


def _canonical_command(command: str, root: Path | None) -> _CanonicalAction | None:
    tokens = _tokens(command)
    if not tokens:
        return None
    executable = Path(tokens[0]).name.casefold()
    if executable.endswith(".exe"):
        executable = executable[:-4]

    # Python wrappers: source reads and module-based verification commands.
    if executable in {"python", "python3", "py"}:
        open_match = re.search(
            r"\bopen\(\s*(['\"])(?P<path>.+?)\1\s*[,)]", command, re.IGNORECASE
        )
        if open_match and ".read" in command:
            return _file_read(open_match.group("path"), root)
        lowered = [token.casefold() for token in tokens]
        if len(tokens) >= 4 and lowered[1] == "-m":
            module = lowered[2]
            remainder = tokens[3:]
            if module in {"pytest", "unittest"}:
                scope = _positional(remainder)
                return _verification("test", module, scope)
            if module == "ruff" and remainder and remainder[0].casefold() == "check":
                scope = _positional(remainder[1:])
                return _verification("lint", "ruff", scope)

    if executable in {"cat", "type"}:
        paths = _positional(tokens[1:])
        return _file_read(paths[-1], root) if paths else None

    if executable in {"get-content", "gc"}:
        path = _option_value(tokens[1:], "-path", "-literalpath")
        if path is None:
            values = _positional(tokens[1:])
            path = values[0] if values else None
        if path is None:
            return None
        count = _option_value(tokens[1:], "-totalcount", "-first")
        return _file_read(path, root, end=count)

    if executable in {"rg", "ripgrep", "grep"}:
        values = _positional(tokens[1:])
        if not values:
            return None
        query = values[0]
        paths = values[1:] or ["."]
        return _search(query, paths, root)

    if executable in {"select-string", "sls"}:
        query = _option_value(tokens[1:], "-pattern")
        path = _option_value(tokens[1:], "-path", "-literalpath")
        if query is None:
            values = _positional(tokens[1:])
            query = values[-1] if values else ""
        return _search(query, [path or "."], root)

    if executable == "git":
        rest = list(tokens[1:])
        rest = [token for token in rest if token.casefold() != "--no-pager"]
        if not rest:
            return None
        subcommand = rest[0].casefold()
        arguments = rest[1:]
        if subcommand == "status":
            return _CanonicalAction(
                family="repository_read",
                capability="status",
                target={"repository": "current"},
                arguments={"view": "working_state"},
                side_effect=SideEffectClass.READ_ONLY,
            )
        if subcommand in {"diff", "log", "show", "rev-parse", "branch"}:
            safe_arguments = [
                "--short" if item == "-s" and subcommand == "status" else item
                for item in arguments
                if item.casefold() not in {"--no-ext-diff", "--no-textconv"}
            ]
            return _CanonicalAction(
                family="repository_read",
                capability=subcommand.replace("-", "_"),
                target={"repository": "current"},
                arguments={"arguments": safe_arguments},
                side_effect=SideEffectClass.READ_ONLY,
            )

    if executable == "gh" and len(tokens) >= 4:
        resource = tokens[1].casefold()
        operation = tokens[2].casefold()
        if resource in {"issue", "pr"} and operation == "view":
            number = tokens[3]
            return _CanonicalAction(
                family="github_read",
                capability=f"{resource}_details",
                target={"resource": resource, "number": str(number)},
                arguments={"view": "details_with_comments"},
                side_effect=SideEffectClass.READ_ONLY,
            )

    if executable in {"pytest", "unittest"}:
        return _verification("test", executable, _positional(tokens[1:]))

    if executable == "ruff" and len(tokens) >= 2 and tokens[1].casefold() == "check":
        return _verification("lint", "ruff", _positional(tokens[2:]))

    if executable in {"npm", "npm.cmd"} and len(tokens) >= 2:
        rest = list(tokens[1:])
        if rest and rest[0].casefold() == "run":
            rest.pop(0)
        if rest and rest[0].casefold() in {"test", "build", "lint"}:
            capability = rest.pop(0).casefold()
            return _verification(capability, f"npm:{capability}", _positional(rest))

    if executable == "cargo" and len(tokens) >= 2 and tokens[1].casefold() == "test":
        return _verification("test", "cargo", _positional(tokens[2:]))
    if executable == "go" and len(tokens) >= 2 and tokens[1].casefold() == "test":
        return _verification("test", "go", _positional(tokens[2:]))
    return None


def _canonical_action(
    tool: str, arguments: dict[str, Any], root: Path | None
) -> _CanonicalAction:
    name = str(tool or "unknown").strip().casefold()
    if name == "read_file":
        return _file_read(
            arguments.get("path"),
            root,
            start=arguments.get("start_line", 1),
            end=arguments.get("end_line"),
        )
    if name == "search_code":
        return _search(arguments.get("query"), arguments.get("paths"), root)
    if name == "find_files":
        return _search(
            arguments.get("pattern", "*"),
            ["."],
            root,
            capability="find_files",
        )
    if name == "git_status":
        return _CanonicalAction(
            family="repository_read",
            capability="status",
            target={"repository": "current"},
            arguments={"view": "working_state"},
            side_effect=SideEffectClass.READ_ONLY,
        )
    if name == "github_get_issue":
        return _CanonicalAction(
            family="github_read",
            capability="issue_details",
            target={"resource": "issue", "number": str(arguments.get("number", ""))},
            arguments={"view": "details_with_comments"},
            side_effect=SideEffectClass.READ_ONLY,
        )
    if name == "github_pr_status":
        return _CanonicalAction(
            family="github_read",
            capability="pr_status",
            target={"resource": "pr", "number": str(arguments.get("number", ""))},
            arguments={"view": "status_with_checks"},
            side_effect=SideEffectClass.READ_ONLY,
        )
    if name == "github_search_issues":
        labels = tuple(
            sorted(str(item).casefold() for item in arguments.get("labels", ()))
        )
        return _CanonicalAction(
            family="github_read",
            capability="issue_search",
            target={"repository": "current"},
            arguments={
                "query": _normal_prose(arguments.get("query")),
                "state": str(arguments.get("state") or "open").casefold(),
                "labels": labels,
            },
            side_effect=SideEffectClass.READ_ONLY,
        )
    if name == "run_tests":
        command = str(arguments.get("command_id") or "unknown").casefold()
        scope = str(arguments.get("scope") or command)
        return _verification("test", command, [scope])
    if name == "run_command":
        parsed = _canonical_command(str(arguments.get("command") or ""), root)
        if parsed is not None:
            return parsed
    if name in _PROVIDER_ACTIONS:
        plan = arguments.get("plan_id") or arguments.get("hypothesis_id") or ""
        return _CanonicalAction(
            family="provider_query",
            capability="obtain_facts",
            target={"plan": _normal_prose(plan) or "unscoped"},
            arguments={
                "prompt": _normal_prose(
                    arguments.get("prompt") or arguments.get("query")
                )
            },
            side_effect=SideEffectClass.PROVIDER_CALL,
        )
    if name in _LOCAL_MUTATIONS:
        return _CanonicalAction(
            family="mutation",
            capability=name,
            target={"operation": name},
            arguments={"identity": _stable_value(arguments)},
            side_effect=SideEffectClass.LOCAL_MUTATION,
        )
    if name in _EXTERNAL_EFFECTS:
        return _CanonicalAction(
            family="external_effect",
            capability=name,
            target={"operation": name},
            arguments={"identity": _stable_value(arguments)},
            side_effect=SideEffectClass.EXTERNAL_EFFECT,
        )
    return _CanonicalAction(
        family="unknown",
        capability="unknown",
        target={"tool": name},
        arguments={"identity": _stable_value(arguments)},
        side_effect=SideEffectClass.UNKNOWN,
        recognized=False,
    )


def _freshness(
    project_root: Path | None, repository_digest: str | None
) -> tuple[str, bool]:
    if repository_digest:
        value = str(repository_digest)
        return _digest("freshness", value), value.casefold() != _UNKNOWN_FRESHNESS
    if project_root is None:
        return _digest("freshness", _UNKNOWN_FRESHNESS), False
    try:
        from .evidence_cache import assess_repo_fingerprint

        assessment = assess_repo_fingerprint(project_root)
    except Exception:  # noqa: BLE001 - uncertainty must allow fresh evidence
        return _digest("freshness", _UNKNOWN_FRESHNESS), False
    if not assessment.cacheable:
        # The bypass digest changes for every assessment.  Keeping it means an
        # uncertain repository can never suppress a later safety check.
        return _digest("freshness", assessment.digest), False
    return _digest("freshness", assessment.digest), True


def fingerprint_action(
    tool: str,
    arguments: Mapping[str, Any] | str | None,
    *,
    project_root: Path | str | None = None,
    repository_digest: str | None = None,
    expected_information: str = "",
    failure_class: str = "",
    plan_id: str = "",
) -> ActionFingerprint:
    """Derive one versioned fingerprint without retaining its raw inputs."""

    root = Path(project_root).expanduser().resolve() if project_root else None
    parsed = _mapping(arguments)
    canonical = _canonical_action(tool, parsed, root)
    target_digest = _digest("target", canonical.target)
    arguments_digest = _digest("arguments", canonical.arguments)
    freshness_digest, freshness_known = _freshness(root, repository_digest)
    expected_effect_digest = _digest(
        "expected_effect",
        _normal_prose(expected_information)
        or f"{canonical.family}:{canonical.capability}",
    )
    normalized_failure = str(failure_class or "none").strip().casefold()
    failure_digest = _digest("failure_class", normalized_failure)
    embedded_plan = parsed.get("plan_id") or parsed.get("hypothesis_id") or plan_id
    plan_digest = _digest("plan", _normal_prose(embedded_plan) or "none")
    trajectory_basis = {
        "version": FINGERPRINT_VERSION,
        "family": canonical.family,
        "capability": canonical.capability,
        "target": target_digest,
        "arguments": arguments_digest,
        "expected_effect": expected_effect_digest,
        "side_effect": canonical.side_effect.value,
        "failure_class": failure_digest,
        "plan": plan_digest,
    }
    trajectory_digest = _digest("trajectory", trajectory_basis)
    semantic_basis = {
        **trajectory_basis,
        "repository": freshness_digest,
    }
    semantic_digest = _digest("semantic", semantic_basis)
    exact_digest = _digest(
        "exact",
        {
            "version": FINGERPRINT_VERSION,
            "tool": str(tool or "unknown").strip().casefold(),
            "arguments": _stable_value(parsed),
        },
    )
    suppressible = (
        canonical.recognized
        and freshness_known
        and canonical.side_effect
        in {
            SideEffectClass.READ_ONLY,
            SideEffectClass.VERIFICATION,
            SideEffectClass.PROVIDER_CALL,
        }
    )
    return ActionFingerprint(
        version=FINGERPRINT_VERSION,
        operation_family=canonical.family,
        capability=canonical.capability,
        target_digest=target_digest,
        arguments_digest=arguments_digest,
        repository_digest=freshness_digest,
        expected_effect_digest=expected_effect_digest,
        side_effect_class=canonical.side_effect,
        failure_class_digest=failure_digest,
        plan_digest=plan_digest,
        trajectory_digest=trajectory_digest,
        semantic_digest=semantic_digest,
        exact_digest=exact_digest,
        scope_start=canonical.scope_start,
        scope_end=canonical.scope_end,
        freshness_known=freshness_known,
        suppressible=suppressible,
    )


def failure_result_digest(action: ActionFingerprint, failure_class: str) -> str:
    """Bind a normalized result class to an existing action without re-probing.

    Semantic actions use their freshness-aware identity. Side-effecting and
    unknown actions use exact identity, preserving #616's boundary.
    """

    identity = action.semantic_digest if action.suppressible else action.exact_digest
    return _digest(
        "failure_result",
        {
            "action": identity,
            "failure_class": str(failure_class or "failed").strip().casefold(),
        },
    )


def _ranges_overlap(left: ActionFingerprint, right: ActionFingerprint) -> bool:
    left_start = left.scope_start or 1
    right_start = right.scope_start or 1
    left_end = left.scope_end if left.scope_end is not None else float("inf")
    right_end = right.scope_end if right.scope_end is not None else float("inf")
    return max(left_start, right_start) <= min(left_end, right_end)


def compare_actions(
    left: ActionFingerprint, right: ActionFingerprint
) -> EquivalenceLevel:
    """Compare fingerprints conservatively; unknown never implies blocking."""

    if left.version != right.version:
        return EquivalenceLevel.UNKNOWN
    if "unknown" in {left.operation_family, right.operation_family}:
        return EquivalenceLevel.UNKNOWN
    if left.operation_family != right.operation_family:
        return EquivalenceLevel.UNKNOWN
    if left.repository_digest != right.repository_digest:
        return EquivalenceLevel.RELATED_MATERIALLY_DIFFERENT
    if left.exact_digest == right.exact_digest:
        return EquivalenceLevel.EXACT_DUPLICATE
    if left.semantic_digest == right.semantic_digest:
        return EquivalenceLevel.SEMANTICALLY_EQUIVALENT
    if (
        left.operation_family == "file_read"
        and left.capability == right.capability
        and left.target_digest == right.target_digest
    ):
        if _ranges_overlap(left, right):
            return EquivalenceLevel.OVERLAPPING_OR_SUBSUMING
        return EquivalenceLevel.RELATED_MATERIALLY_DIFFERENT
    if left.capability == right.capability or left.target_digest == right.target_digest:
        return EquivalenceLevel.RELATED_MATERIALLY_DIFFERENT
    return EquivalenceLevel.UNKNOWN
