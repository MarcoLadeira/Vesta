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
        "push",
        "create_pr",
    }
)
_SHIP = _IMPLEMENT | frozenset({"merge_pr"})

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

    ship_at = _last_match(_SHIP_SIGNAL, text)
    implement_at = _last_positive_write(text)
    read_only_at = _last_match(_READ_ONLY_SIGNAL, text)
    review_at = _last_match(_REVIEW_SIGNAL, text)
    explain_at = _last_match(_EXPLAIN_SIGNAL, text)

    latest_write = max(ship_at, implement_at)
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
    return AgentPolicy(
        mode,
        _CAPABILITIES[mode],
        merge_requirements=merge_requirements,
        rationale=(
            "Latest explicit write request controls."
            if latest_write > latest_read_only
            else "Latest explicit read-only request controls."
        ),
    )


def build_capability_contract(policy: AgentPolicy, *, active_repo: str) -> str:
    """Render a concise, secret-safe provider instruction for this turn."""

    repo = redact(str(active_repo or "active workspace"))
    allowed = ", ".join(sorted(policy.capabilities))
    lines = [
        "OPai capability contract for this turn:",
        f"- Effective mode: {policy.mode.value.title()}",
        f"- Active repository: {repo}",
        f"- Authorized capabilities: {allowed}",
    ]
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
