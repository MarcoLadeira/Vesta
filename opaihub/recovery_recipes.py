"""Deterministic recovery recipes for known failure families (#569).

A diagnosis is only useful if something acts on it. This module holds the
IF-THEN library that turns a :class:`~opaihub.failure_envelope.FailureEnvelope`
into a concrete next action — deterministically, without asking a model what it
thinks went wrong.

That determinism is the point. Handing a failed command back to the provider
and hoping costs a paid turn, produces a different answer each time, and is
exactly how a run ends up repeating the same broken call. A recipe is matched by
signature, produces the same action every time, and is cheap.

Safety rules, enforced here rather than left to the caller:

* **Never repeat the failing action.** A recipe whose action equals the thing
  that just failed is rejected at construction.
* **Recover, never mutate.** Recipes may only read/diagnose. Anything that
  changes repository or remote state has to go back through the normal
  authority path, so a recovery path can never become a side-door around
  approval.
* **Once per failure signature.** A recipe that did not help is not retried for
  the same signature, so recovery cannot itself become the loop.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Mapping, Sequence

from .failure_envelope import FailureCategory, FailureEnvelope

# Tools a recipe may invoke. Deliberately read-only: recovery diagnoses and
# re-routes, it never edits, commits, pushes or comments.
READ_ONLY_ACTIONS = frozenset(
    {
        "github_rest_api",
        "opai_github_connector",
        "github_search_issues",
        "read_file",
        "search_code",
        "run_tests",
        "provider_reauth_prompt",
        "switch_provider",
        "wait_and_retry",
        "compact_context",
    }
)


class RecipeError(ValueError):
    """A recipe that would be unsafe or useless to apply."""


@dataclass(frozen=True)
class RecoveryAction:
    """What to do instead, and why."""

    action: str
    rationale: str
    parameters: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "rationale": self.rationale,
            "parameters": dict(self.parameters),
        }


@dataclass(frozen=True)
class RecoveryRecipe:
    """One IF-THEN rule: a matched failure maps to a safe alternative action."""

    recipe_id: str
    category: FailureCategory
    action: RecoveryAction
    signature_pattern: str = ""
    tool: str = ""

    def __post_init__(self) -> None:
        if self.action.action not in READ_ONLY_ACTIONS:
            raise RecipeError(
                f"{self.recipe_id}: {self.action.action!r} is not a read-only "
                "recovery action; mutating work must go through the normal "
                "authority path"
            )

    def matches(self, envelope: FailureEnvelope) -> bool:
        if envelope.primary_cause is not self.category:
            return False
        if self.tool and self.tool != envelope.tool:
            return False
        if self.signature_pattern:
            return bool(
                re.search(
                    self.signature_pattern, envelope.error_signature, re.IGNORECASE
                )
            )
        return True


# The shipped library. Ordered: the first matching recipe wins, so a
# tool-specific rule must precede a generic one for the same category.
RECIPES: tuple[RecoveryRecipe, ...] = (
    RecoveryRecipe(
        recipe_id="recover.gh.issue_read.deprecation.v1",
        category=FailureCategory.TOOL_COMPATIBILITY,
        tool="github-cli",
        signature_pattern=r"projects_classic_removed|unknown field",
        action=RecoveryAction(
            action="github_rest_api",
            rationale=(
                "The installed GitHub CLI dropped this field; the REST API "
                "returns the same issue data without it."
            ),
        ),
    ),
    RecoveryRecipe(
        recipe_id="recover.tool.compatibility.generic.v1",
        category=FailureCategory.TOOL_COMPATIBILITY,
        action=RecoveryAction(
            action="opai_github_connector",
            rationale="The external tool changed; use OPai's own connector instead.",
        ),
    ),
    RecoveryRecipe(
        recipe_id="recover.auth.reauth.v1",
        category=FailureCategory.AUTHENTICATION,
        action=RecoveryAction(
            action="provider_reauth_prompt",
            rationale=(
                "Credentials were rejected. Only the user can supply new ones, "
                "so ask rather than retry."
            ),
        ),
    ),
    RecoveryRecipe(
        recipe_id="recover.quota.switch_provider.v1",
        category=FailureCategory.QUOTA_OR_RATE_LIMIT,
        action=RecoveryAction(
            action="switch_provider",
            rationale="This provider is rate limited or out of quota; route elsewhere.",
            parameters={"prefer": "free_or_local"},
        ),
    ),
    RecoveryRecipe(
        recipe_id="recover.network.retry.v1",
        category=FailureCategory.NETWORK,
        action=RecoveryAction(
            action="wait_and_retry",
            rationale="Transient network fault; one bounded retry before giving up.",
            parameters={"max_attempts": 2, "backoff_seconds": 2},
        ),
    ),
    RecoveryRecipe(
        recipe_id="recover.no_progress.compact.v1",
        category=FailureCategory.NO_PROGRESS,
        action=RecoveryAction(
            action="compact_context",
            rationale=(
                "The run stopped learning. Compress findings so the next step "
                "reasons over a summary instead of re-reading the same material."
            ),
        ),
    ),
)


@dataclass
class RecoveryLedger:
    """Remembers which recipes were already tried, per failure signature.

    Without this, recovery becomes the loop it exists to break: the same
    fallback fires forever against the same error.
    """

    attempted: dict[str, set[str]] = field(default_factory=dict)

    def mark(self, signature: str, recipe_id: str) -> None:
        self.attempted.setdefault(signature, set()).add(recipe_id)

    def already_tried(self, signature: str, recipe_id: str) -> bool:
        return recipe_id in self.attempted.get(signature, set())

    def history(self) -> list[str]:
        return sorted(
            f"{signature}:{recipe}"
            for signature, recipes in self.attempted.items()
            for recipe in recipes
        )


def select_recipe(
    envelope: FailureEnvelope,
    *,
    ledger: RecoveryLedger | None = None,
    failing_action: str = "",
    recipes: Sequence[RecoveryRecipe] = RECIPES,
) -> RecoveryRecipe | None:
    """The first safe, untried recipe for this failure, or None.

    None is a real answer: "no deterministic recovery applies" is what turns a
    stall into an honest stop instead of another expensive guess.
    """

    if not envelope.is_actionable:
        return None
    signature = envelope.error_signature or envelope.primary_cause.value
    for recipe in recipes:
        if not recipe.matches(envelope):
            continue
        if recipe.action.action == failing_action:
            continue  # never re-run the thing that just failed
        if ledger is not None and ledger.already_tried(signature, recipe.recipe_id):
            continue
        return recipe
    return None
