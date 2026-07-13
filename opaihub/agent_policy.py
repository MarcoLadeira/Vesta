"""Central intent and authorization policy for coding-agent requests.

This module decides what the user asked OPai to do. Provider transports and UI
run modes consume the decision; they do not independently reinterpret it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from .command_runner import redact


class AgentMode(str, Enum):
    EXPLAIN = "explain"
    REVIEW = "review"
    IMPLEMENT = "implement"
    SHIP = "ship"
    DANGEROUS = "dangerous"


_READ = frozenset({"read_files", "search_code", "inspect_git"})
_IMPLEMENT = _READ | frozenset(
    {
        "edit_files",
        "create_files",
        "run_tests",
        "create_branch",
        "commit",
    }
)
_PUBLISH = frozenset({"push", "create_pr"})
_SHIP = _IMPLEMENT | _PUBLISH | frozenset({"merge_pr"})

_CAPABILITIES = {
    AgentMode.EXPLAIN: _READ,
    AgentMode.REVIEW: _READ,
    AgentMode.IMPLEMENT: _IMPLEMENT,
    AgentMode.SHIP: _SHIP,
    AgentMode.DANGEROUS: _READ,
}

_DANGEROUS = re.compile(
    r"\b(?:force[ -]?push|reset\s+--hard|git\s+clean|remove-item\s+-recurse|"
    r"delete\s+(?:the\s+)?(?:user'?s\s+)?(?:repo|repository|branch|database|files?|folders?|director(?:y|ies))|"
    r"drop\s+(?:the\s+)?database|wipe\s+(?:the\s+)?database|production\s+(?:credentials?|secrets?)|"
    r"expose\s+(?:a\s+)?secret|format\s+(?:the\s+)?drive)\b",
    re.IGNORECASE,
)
_SHIP_SIGNAL = re.compile(
    r"\b(?:merge\s+(?:it|the\s+pr|this\s+pr)|ship\s+(?:it|this)|merge\s+after|merge\s+when)\b",
    re.IGNORECASE,
)
_SHIP_PROHIBITION_SIGNAL = re.compile(
    r"\b(?:do\s+not|don't|never|without|avoid|forbid|must\s+not)\s+(?:merge|ship)\b",
    re.IGNORECASE,
)
_PUBLISH_SIGNAL = re.compile(
    r"\b(?:push|(?:open|create|make|submit)\s+(?:a\s+)?(?:pr|pull\s+request))\b",
    re.IGNORECASE,
)
_IMPLEMENT_SIGNAL = re.compile(
    r"\b(?:fix|implement|build|create\s+(?:a\s+)?pr|make\s+(?:a\s+)?pr|open\s+(?:a\s+)?pr|"
    r"pull\s+request|patch|(?:resolve|solve)\s+(?:the\s+)?issue|refactor|(?:write|run)(?:\s+the)?(?:\s+relevant)?\s+tests?|"
    r"create\s+(?:a\s+)?(?:new\s+)?(?:file|module|component|config)|add\s+(?:a\s+)?feature|"
    r"change\s+(?:the\s+)?code|edit\s+(?:the\s+)?files?|improve|update|upgrade|rename|remove|replace)\b",
    re.IGNORECASE,
)
_READ_ONLY_SIGNAL = re.compile(
    r"\b(?:do\s+not\s+(?:modify|edit|change)|don't\s+(?:modify|edit|change)|no\s+edits?|read[ -]?only|"
    r"report\s+findings\s+only|explain\s+only|do\s+not\s+write\s+code)\b",
    re.IGNORECASE,
)
_REVIEW_SIGNAL = re.compile(
    r"\b(?:review|audit|inspect|find\s+(?:risks?|problems?|issues?)|report\s+findings)\b",
    re.IGNORECASE,
)
_EXPLAIN_SIGNAL = re.compile(
    r"\b(?:explain|describe|summari[sz]e|how\s+does|what\s+does|why\s+does)\b",
    re.IGNORECASE,
)
_EXPLANATION_LEADER = re.compile(
    r"^(?:what|why|how|explain|describe|summari[sz]e)\b", re.IGNORECASE
)


@dataclass(frozen=True)
class AgentPolicy:
    mode: AgentMode
    capabilities: frozenset[str]
    requires_confirmation: bool = False
    needs_clarification: bool = False
    merge_requirements: tuple[str, ...] = ()
    rationale: str = ""

    def allows(self, capability: str) -> bool:
        return capability in self.capabilities

    def to_dict(self) -> dict[str, object]:
        return {
            "mode": self.mode.value,
            "label": self.mode.value.title(),
            "capabilities": sorted(self.capabilities),
            "requires_confirmation": self.requires_confirmation,
            "needs_clarification": self.needs_clarification,
            "merge_requirements": list(self.merge_requirements),
            "rationale": self.rationale,
        }


def _last_match(pattern: re.Pattern[str], text: str) -> int:
    matches = list(pattern.finditer(text))
    return matches[-1].start() if matches else -1


def _last_positive_write(text: str) -> int:
    """Ignore write verbs that are themselves inside a local negation."""

    matches = []
    for match in _IMPLEMENT_SIGNAL.finditer(text):
        prefix = text[max(0, match.start() - 14) : match.start()].lower()
        if re.search(r"(?:do not|don't|no)\s+$", prefix):
            continue
        matches.append(match)
    return matches[-1].start() if matches else -1


def _last_positive_publish(text: str) -> int:
    """Ignore publish verbs that are forbidden in their current clause."""

    latest = -1
    for match in _PUBLISH_SIGNAL.finditer(text):
        clause = re.split(r"[.;\n]", text[: match.start()])[-1]
        if re.search(
            r"\b(?:do\s+not|don't|never|without|avoid|forbid|must\s+not)\b",
            clause,
            re.IGNORECASE,
        ):
            latest = -1
            continue
        latest = match.start()
    return latest


def _last_positive_ship(text: str) -> int:
    """Ignore merge/ship signals that are negated in their current clause."""

    latest = -1
    for match in _SHIP_SIGNAL.finditer(text):
        clause = re.split(r"[.;\n]", text[: match.start()])[-1]
        if re.search(
            r"\b(?:do\s+not|don't|never|without|avoid|forbid|must\s+not)\b",
            clause,
            re.IGNORECASE,
        ):
            continue
        latest = match.start()
    return latest


def _has_positive_danger(text: str) -> bool:
    """Distinguish a destructive request from an explicit safety constraint."""

    for match in _DANGEROUS.finditer(text):
        clause = re.split(r"[.;\n]", text[max(0, match.start() - 90) : match.start()])[
            -1
        ]
        if re.search(
            r"\b(?:do\s+not|don't|never|without|avoid|forbid|must\s+not|ask\s+before)\b",
            clause,
            re.IGNORECASE,
        ) or re.search(r"\bno\s*$", clause, re.IGNORECASE):
            continue
        return True
    return False


def resolve_agent_policy(message: str, *, focus_hint: str | None = None) -> AgentPolicy:
    """Infer the effective task mode, preferring the latest explicit request.

    A focus control is advisory. It is consulted only when the message itself
    has no clear action or read-only signal, so a stale UI selection cannot
    override a current request to fix code or create a PR.
    """

    text = " ".join(str(message or "").split())
    if _EXPLANATION_LEADER.search(text):
        current_request_at = text.lower().rfind("current request:")
        later_write_at = _last_positive_write(text)
        if current_request_at < 0 or later_write_at < current_request_at:
            return AgentPolicy(
                AgentMode.EXPLAIN,
                _CAPABILITIES[AgentMode.EXPLAIN],
                rationale="The user asked for an explanation, not execution.",
            )
    if _has_positive_danger(text):
        return AgentPolicy(
            AgentMode.DANGEROUS,
            _CAPABILITIES[AgentMode.DANGEROUS],
            requires_confirmation=True,
            rationale="The request contains a destructive or irreversible action.",
        )

    ship_at = _last_positive_ship(text)
    if _last_match(_SHIP_PROHIBITION_SIGNAL, text) >= ship_at:
        ship_at = -1
    implement_at = _last_positive_write(text)
    publish_at = _last_positive_publish(text)
    read_only_at = _last_match(_READ_ONLY_SIGNAL, text)
    review_at = _last_match(_REVIEW_SIGNAL, text)
    explain_at = _last_match(_EXPLAIN_SIGNAL, text)

    latest_write = max(ship_at, implement_at, publish_at)
    latest_read_only = max(read_only_at, review_at, explain_at)
    if ship_at >= 0 and ship_at >= read_only_at:
        mode = AgentMode.SHIP
    elif latest_write >= 0 and latest_write > read_only_at:
        mode = AgentMode.IMPLEMENT
    elif review_at >= explain_at and review_at >= 0:
        mode = AgentMode.REVIEW
    elif explain_at >= 0 or read_only_at >= 0:
        mode = AgentMode.EXPLAIN
    else:
        hint = str(focus_hint or "").lower()
        if hint in {"review"}:
            mode = AgentMode.REVIEW
        elif hint in {"build", "debug", "refactor", "test", "implement"}:
            mode = AgentMode.IMPLEMENT
        else:
            mode = AgentMode.EXPLAIN

    merge_requirements = ()
    if mode is AgentMode.SHIP:
        merge_requirements = (
            "tests_pass",
            "correct_branch",
            "no_secrets",
            "no_unrelated_files",
            "no_conflicts",
            "checks_acceptable",
        )
    capabilities = _CAPABILITIES[mode]
    if mode is AgentMode.IMPLEMENT and publish_at > read_only_at:
        capabilities = capabilities | _PUBLISH
    return AgentPolicy(
        mode,
        capabilities,
        merge_requirements=merge_requirements,
        rationale=(
            "Latest explicit write request controls."
            if latest_write > latest_read_only
            else "Latest explicit read-only request controls."
        ),
    )


def build_capability_contract(
    policy: AgentPolicy,
    *,
    active_repo: str,
    tool_names: tuple[str, ...] = (),
) -> str:
    """Render a concise, secret-safe provider instruction for this turn.

    ``tool_names`` is the *actual* callable tool vocabulary for this provider.
    When given, the contract names those tools explicitly — capability prose
    alone made smaller models refuse edits ("create_files was not permitted")
    because no tool literally named ``create_files`` existed.
    """

    repo = redact(str(active_repo or "active workspace"))
    allowed = ", ".join(sorted(policy.capabilities))
    lines = [
        "OPai capability contract for this turn:",
        f"- Effective mode: {policy.mode.value.title()}",
        f"- Active repository: {repo}",
        f"- Authorized capabilities: {allowed}",
    ]
    if tool_names:
        lines.append("- Callable tools this turn: " + ", ".join(tool_names) + ".")
        if "write_file" in tool_names:
            lines.append(
                "- Create new files or rewrite whole files with the write_file "
                "tool; modify existing code with apply_patch. These tools ARE "
                "your authorization to edit — do not claim edits are not "
                "permitted while they are listed."
            )
        if "run_command" in tool_names:
            lines.append(
                "- Run builds, linters, formatters, generators, or inspection "
                "commands with run_command (one command per call, no shell "
                "operators). Network access, package installs, and privileged or "
                "destructive commands are refused — ask the user to run those."
            )
        if "git_commit" in tool_names:
            lines.append(
                "- Commit your changes with git_commit (it stages only files "
                "this run touched); create a branch first with "
                "git_create_branch when the change deserves its own branch."
            )
        if "open_pr" in tool_names:
            lines.append(
                "- You may push with git_push and open a pull request with "
                "open_pr — the user has explicitly enabled GitHub operations."
            )
        elif "git_commit" in tool_names:
            lines.append(
                "- Pushing and PRs are disabled this turn. If asked to push or "
                "open a PR, commit locally and tell the user to run "
                "`opai github status` to see what's missing — pushes need BOTH a "
                "connected token (opai github connect) AND consent (opai github "
                "allow-push on). Do not tell them to only re-run allow-push."
            )
    if policy.mode in {AgentMode.IMPLEMENT, AgentMode.SHIP}:
        lines.append(
            "- Proceed without repeated confirmation for the authorized repository workflow."
        )
        lines.append(
            "- Inspect before acting, preserve unrelated user changes, test the result, and report concrete evidence."
        )
    else:
        lines.append(
            "- This is read-only: do not modify files or run mutating commands."
        )
    if policy.mode is AgentMode.SHIP:
        lines.append(
            "- Merge only after every gate passes: "
            + ", ".join(policy.merge_requirements)
            + "."
        )
    lines.append(
        "- Always ask before force-push, destructive deletion, secret exposure, paid service use, or production credential changes."
    )
    return "\n".join(lines)
