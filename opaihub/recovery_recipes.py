"""Deterministic recovery recipes for known failure families (#569, #653).

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
  that just failed is rejected at selection.
* **Recover, never mutate.** Recipes may only read/diagnose. Anything that
  changes repository or remote state has to go back through the normal
  authority path, so a recovery path can never become a side-door around
  approval. Enforced at construction against :data:`READ_ONLY_ACTIONS`.
* **Once per failure signature.** A recipe that did not help is not retried for
  the same signature, so recovery cannot itself become the loop.

#653 extends the original prototype into the reviewed library the issue
specifies: the full declaration schema, the fifteen named failure families,
deterministic selection with a recorded trace of every considered *and
excluded* candidate, hard caps that cannot increase, and per-recipe
governance (owner, expiry, independent rollback).

## What a recipe may not do

A recipe is data, not authority. It cannot modify policy, budget, approval or
verification definitions; it declares what it *needs* and the caller enforces
that. :meth:`RecoveryRecipe.violates` is the single predicate a caller uses to
refuse a recipe whose requirements the current context does not satisfy —
recipes are excluded by the caller's facts, never by their own say-so.

## Honest scope

Selection, bounding and governance are complete and adopted. Automatic
*execution* of a selected recipe belongs to the unified convergence controller
(#648), which does not exist yet; until it does, a selected recipe is
surfaced on the failure result so a human or a later controller can act on it.
Recipes declare their checkpoint inputs/outputs (:attr:`checkpoint_inputs`,
:attr:`checkpoint_outputs`) but resuming from one is #651's provider-neutral
checkpoint work, also outstanding. Both are stated on the relevant recipes
rather than silently assumed.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date
from enum import Enum
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
        # #653 additions, all read-only or user-facing by construction.
        "targeted_symbol_search",
        "request_reproducer",
        "replan_from_evidence",
        "reconcile_operation",
        "verify_tool_capability",
        "rerun_affected_verification",
        "refresh_repository_state",
        "request_approval",
        "report_budget_exhausted",
        "resume_from_checkpoint",
        "ask_user",
        "stop_with_reason",
    }
)


class RecipeError(ValueError):
    """A recipe that would be unsafe or useless to apply."""


class RecipeFamily(str, Enum):
    """The fifteen failure families #653 requires the library to cover.

    Every member must appear in :data:`RECIPES` with either a real recipe or
    an explicit ``not_automatable_reason`` — ``test_recovery_recipe_library``
    pins that, which is acceptance criterion 1.
    """

    BROAD_EXPLORATION = "broad_exploration"
    EQUIVALENT_REPEATS = "equivalent_repeats"
    MISSING_REPRODUCER = "missing_reproducer"
    CONTEXT_OVERLOAD = "context_overload"
    INVALID_PLAN_ASSUMPTION = "invalid_plan_assumption"
    PROVIDER_INTERRUPTION = "provider_interruption"
    PROVIDER_AUTH_QUOTA = "provider_auth_quota"
    TOOL_DRIFT = "tool_drift"
    MALFORMED_TOOL_OUTPUT = "malformed_tool_output"
    REPOSITORY_MOVEMENT = "repository_movement"
    REPEATED_CHECK_FAILURE = "repeated_check_failure"
    VERIFICATION_REPAIR = "verification_repair"
    APPROVAL_EXPIRY = "approval_expiry"
    BUDGET_EXHAUSTION = "budget_exhaustion"
    CRASH_CONTINUITY = "crash_continuity"


class TerminalVerdict(str, Enum):
    """What the run becomes if a recipe's fallback is reached.

    Deliberately a closed set drawn from the canonical lifecycle vocabulary —
    a recipe cannot invent an ending.
    """

    NEEDS_ATTENTION = "needs_attention"
    AWAITING_INPUT = "awaiting_input"
    BLOCKED = "blocked"
    PARTIAL = "partial"
    FAILED = "failed"


@dataclass(frozen=True)
class RecipeCaps:
    """Hard bounds on one recipe's execution (#653 execution contract).

    Caps are *maxima the caller must not exceed*, and
    :meth:`RecipeBudget.spend` can only ever move remaining allowances down —
    a recipe cannot grant itself more room mid-recovery, which is what makes
    "a recipe cannot loop into itself without decrementing hard caps" true
    rather than aspirational.
    """

    max_attempts: int = 1
    max_elapsed_seconds: float = 60.0
    max_incremental_spend_usd: float = 0.0

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise RecipeError("max_attempts must be at least 1")
        if self.max_elapsed_seconds <= 0:
            raise RecipeError("max_elapsed_seconds must be positive")
        if self.max_incremental_spend_usd < 0:
            raise RecipeError("max_incremental_spend_usd cannot be negative")

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_attempts": self.max_attempts,
            "max_elapsed_seconds": self.max_elapsed_seconds,
            "max_incremental_spend_usd": self.max_incremental_spend_usd,
        }


@dataclass(frozen=True)
class RecipeRequirements:
    """What a recipe needs before it may run.

    The caller checks these against its own facts (see
    :meth:`RecoveryRecipe.violates`). A recipe never asserts that it *has*
    authority — it states what it would need, and is excluded when the
    context does not supply it.
    """

    preconditions: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()
    requires_authority: bool = False
    requires_network: bool = False
    requires_repository: bool = False
    # True when the recipe may send repository or prompt content off-device.
    sends_data_off_device: bool = False
    min_confidence: float = 0.0

    def __post_init__(self) -> None:
        if not 0.0 <= self.min_confidence <= 1.0:
            raise RecipeError("min_confidence must be between 0.0 and 1.0")

    def to_dict(self) -> dict[str, Any]:
        return {
            "preconditions": list(self.preconditions),
            "capabilities": list(self.capabilities),
            "requires_authority": self.requires_authority,
            "requires_network": self.requires_network,
            "requires_repository": self.requires_repository,
            "sends_data_off_device": self.sends_data_off_device,
            "min_confidence": self.min_confidence,
        }


@dataclass(frozen=True)
class RecipeGovernance:
    """Who owns a recipe, when it expires, and how to roll it back.

    ``enabled`` is the independent rollback switch acceptance criterion 10
    requires: one misbehaving recipe is turned off without touching the rest
    of the library.
    """

    owner: str = "opai-core"
    # ISO date. A recipe past its expiry is never selected — a stale rule is
    # more dangerous than no rule, because it looks reviewed.
    expires_on: str = "2027-01-01"
    enabled: bool = True
    # Free-text rollback instruction for a human unwinding a bad rollout.
    rollback: str = "Set enabled=False for this recipe id and re-run."

    def is_expired(self, *, today: date | None = None) -> bool:
        moment = today or date.today()
        try:
            return moment >= date.fromisoformat(self.expires_on)
        except ValueError:
            # An unparseable expiry is treated as expired: fail closed, since
            # the alternative is trusting a rule nobody can date.
            return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "owner": self.owner,
            "expires_on": self.expires_on,
            "enabled": self.enabled,
            "rollback": self.rollback,
        }


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
    """One reviewed IF-THEN rule: a matched failure maps to a safe action.

    The original five fields keep their meaning and position so existing
    callers and the prototype's tests are unaffected; everything #653 adds
    carries a default, so a recipe declares only what differs from the safe
    baseline.
    """

    recipe_id: str
    category: FailureCategory
    action: RecoveryAction
    signature_pattern: str = ""
    tool: str = ""

    # --- #653 declaration schema -----------------------------------------
    version: int = 1
    family: RecipeFamily | None = None
    trigger_evidence: tuple[str, ...] = ()
    requirements: RecipeRequirements = field(default_factory=RecipeRequirements)
    caps: RecipeCaps = field(default_factory=RecipeCaps)
    governance: RecipeGovernance = field(default_factory=RecipeGovernance)
    allowed_operations: tuple[str, ...] = ()
    prohibited_operations: tuple[str, ...] = ()
    expected_evidence: tuple[str, ...] = ()
    success_predicate: str = ""
    failure_predicate: str = ""
    requires_reconciliation: bool = False
    checkpoint_inputs: tuple[str, ...] = ()
    checkpoint_outputs: tuple[str, ...] = ()
    fallback_action: str = "stop_with_reason"
    terminal_verdict: TerminalVerdict = TerminalVerdict.NEEDS_ATTENTION
    # Minimum/maximum compatible version of `tool`, inclusive/exclusive
    # respectively. Empty means unbounded on that side.
    min_tool_version: str = ""
    max_tool_version: str = ""
    # AC1's escape hatch: a family that cannot be safely automated declares
    # why instead of shipping a rule nobody should trust.
    automatable: bool = True
    not_automatable_reason: str = ""

    def __post_init__(self) -> None:
        if self.action.action not in READ_ONLY_ACTIONS:
            raise RecipeError(
                f"{self.recipe_id}: {self.action.action!r} is not a read-only "
                "recovery action; mutating work must go through the normal "
                "authority path"
            )
        if self.fallback_action not in READ_ONLY_ACTIONS:
            raise RecipeError(
                f"{self.recipe_id}: fallback {self.fallback_action!r} is not read-only"
            )
        overlap = set(self.allowed_operations) & set(self.prohibited_operations)
        if overlap:
            raise RecipeError(
                f"{self.recipe_id}: {sorted(overlap)} is both allowed and prohibited"
            )
        if not self.automatable and not self.not_automatable_reason:
            raise RecipeError(
                f"{self.recipe_id}: a non-automatable family must say why"
            )

    # --- matching ---------------------------------------------------------

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

    def violates(self, context: Mapping[str, Any] | None) -> str:
        """Why this recipe may not run in ``context``, or "" when it may.

        The caller owns the facts; this only compares. Returning a *reason*
        rather than a bool is what makes the selection trace explain itself.
        """

        facts = dict(context or {})
        if not self.automatable:
            return f"not automatable: {self.not_automatable_reason}"
        if not self.governance.enabled:
            return "recipe is disabled (rolled back)"
        if self.governance.is_expired(today=facts.get("today")):
            return f"recipe expired on {self.governance.expires_on}"

        confidence = facts.get("confidence")
        if (
            confidence is not None
            and float(confidence) < self.requirements.min_confidence
        ):
            return (
                f"confidence {float(confidence):.2f} below required "
                f"{self.requirements.min_confidence:.2f}"
            )
        if self.requirements.requires_authority and not facts.get("has_authority"):
            return "requires authority the current run does not hold"
        if self.requirements.requires_network and facts.get("network_allowed") is False:
            return "requires network, which policy currently forbids"
        if self.requirements.requires_repository and not facts.get("has_repository"):
            return "requires a repository this run does not have"
        if (
            self.requirements.sends_data_off_device
            and facts.get("allow_cloud") is False
        ):
            return "would send data off device without cloud consent"
        available = facts.get("capabilities")
        if available is not None:
            missing = [c for c in self.requirements.capabilities if c not in available]
            if missing:
                return f"missing capabilities: {', '.join(sorted(missing))}"
        unmet = [
            p
            for p in self.requirements.preconditions
            if p not in set(facts.get("preconditions") or ())
        ]
        if unmet:
            return f"unmet preconditions: {', '.join(sorted(unmet))}"
        budget = facts.get("remaining_budget_usd")
        if budget is not None and float(budget) < self.caps.max_incremental_spend_usd:
            return (
                f"needs up to ${self.caps.max_incremental_spend_usd:.4f} but only "
                f"${float(budget):.4f} remains"
            )
        version = facts.get("tool_version")
        if version and not self._version_compatible(str(version)):
            return (
                f"tool version {version} outside compatibility range "
                f"[{self.min_tool_version or '*'}, {self.max_tool_version or '*'})"
            )
        return ""

    def _version_compatible(self, version: str) -> bool:
        parsed = _version_tuple(version)
        if parsed is None:
            # An unreadable version cannot prove it is in range.
            return not (self.min_tool_version or self.max_tool_version)
        if self.min_tool_version:
            floor = _version_tuple(self.min_tool_version)
            if floor is not None and parsed < floor:
                return False
        if self.max_tool_version:
            ceiling = _version_tuple(self.max_tool_version)
            if ceiling is not None and parsed >= ceiling:
                return False
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "recipe_id": self.recipe_id,
            "version": self.version,
            "family": self.family.value if self.family else None,
            "category": self.category.value,
            "tool": self.tool,
            "signature_pattern": self.signature_pattern,
            "trigger_evidence": list(self.trigger_evidence),
            "action": self.action.to_dict(),
            "requirements": self.requirements.to_dict(),
            "caps": self.caps.to_dict(),
            "governance": self.governance.to_dict(),
            "allowed_operations": list(self.allowed_operations),
            "prohibited_operations": list(self.prohibited_operations),
            "expected_evidence": list(self.expected_evidence),
            "success_predicate": self.success_predicate,
            "failure_predicate": self.failure_predicate,
            "requires_reconciliation": self.requires_reconciliation,
            "checkpoint_inputs": list(self.checkpoint_inputs),
            "checkpoint_outputs": list(self.checkpoint_outputs),
            "fallback_action": self.fallback_action,
            "terminal_verdict": self.terminal_verdict.value,
            "min_tool_version": self.min_tool_version,
            "max_tool_version": self.max_tool_version,
            "automatable": self.automatable,
            "not_automatable_reason": self.not_automatable_reason,
        }


def _version_tuple(text: str) -> tuple[int, ...] | None:
    match = re.search(r"\d+(?:\.\d+)*", str(text or ""))
    if match is None:
        return None
    return tuple(int(part) for part in match.group(0).split(".")[:4])


# Shared requirement/cap presets, so the library reads as policy rather than
# as repeated literals.
_READ_ONLY = RecipeRequirements()
_NEEDS_NETWORK = RecipeRequirements(requires_network=True, sends_data_off_device=True)
_ONE_CHEAP_ATTEMPT = RecipeCaps(max_attempts=1, max_elapsed_seconds=30.0)


# The shipped library. Ordered: the first matching recipe wins, so a
# tool-specific rule must precede a generic one for the same category.
RECIPES: tuple[RecoveryRecipe, ...] = (
    RecoveryRecipe(
        recipe_id="recover.gh.issue_read.deprecation.v1",
        category=FailureCategory.TOOL_COMPATIBILITY,
        family=RecipeFamily.TOOL_DRIFT,
        tool="github-cli",
        signature_pattern=r"projects_classic_removed|unknown field",
        trigger_evidence=("stderr names a removed CLI field",),
        action=RecoveryAction(
            action="github_rest_api",
            rationale=(
                "The installed GitHub CLI dropped this field; the REST API "
                "returns the same issue data without it."
            ),
        ),
        requirements=RecipeRequirements(
            requires_network=True,
            sends_data_off_device=True,
            capabilities=("github_rest",),
        ),
        caps=_ONE_CHEAP_ATTEMPT,
        allowed_operations=("github_rest_api",),
        prohibited_operations=("gh_cli_upgrade", "git_push", "open_pr"),
        expected_evidence=("issue payload returned with the requested fields",),
        success_predicate="rest_response_ok",
        failure_predicate="rest_response_error_or_empty",
        governance=RecipeGovernance(owner="opai-core", expires_on="2027-01-01"),
    ),
    RecoveryRecipe(
        recipe_id="recover.tool.compatibility.generic.v1",
        category=FailureCategory.TOOL_COMPATIBILITY,
        family=RecipeFamily.TOOL_DRIFT,
        action=RecoveryAction(
            action="opai_github_connector",
            rationale="The external tool changed; use Vesta's own connector instead.",
        ),
        trigger_evidence=("tool reported an unsupported argument or schema",),
        requirements=RecipeRequirements(
            requires_network=True, sends_data_off_device=True
        ),
        caps=_ONE_CHEAP_ATTEMPT,
        allowed_operations=("opai_github_connector",),
        prohibited_operations=("tool_upgrade", "git_push"),
        expected_evidence=("connector returned the requested target identity",),
        success_predicate="connector_returned_target",
        failure_predicate="connector_unavailable",
    ),
    RecoveryRecipe(
        recipe_id="recover.auth.reauth.v1",
        category=FailureCategory.AUTHENTICATION,
        family=RecipeFamily.PROVIDER_AUTH_QUOTA,
        action=RecoveryAction(
            action="provider_reauth_prompt",
            rationale=(
                "Credentials were rejected. Only the user can supply new ones, "
                "so ask rather than retry."
            ),
        ),
        trigger_evidence=("provider returned 401/403 or an expired-session error",),
        caps=RecipeCaps(max_attempts=1, max_elapsed_seconds=15.0),
        allowed_operations=("provider_reauth_prompt",),
        prohibited_operations=("retry_same_credentials", "switch_account_silently"),
        expected_evidence=("user supplied a fresh credential",),
        success_predicate="credential_accepted",
        failure_predicate="user_declined_or_timeout",
        terminal_verdict=TerminalVerdict.AWAITING_INPUT,
    ),
    RecoveryRecipe(
        recipe_id="recover.quota.switch_provider.v1",
        category=FailureCategory.QUOTA_OR_RATE_LIMIT,
        family=RecipeFamily.PROVIDER_AUTH_QUOTA,
        action=RecoveryAction(
            action="switch_provider",
            rationale="This provider is rate limited or out of quota; route elsewhere.",
            parameters={"prefer": "free_or_local"},
        ),
        trigger_evidence=("provider returned 429 or a quota-exhausted error",),
        caps=RecipeCaps(max_attempts=1, max_elapsed_seconds=30.0),
        allowed_operations=("switch_provider",),
        prohibited_operations=("escalate_to_paid_without_consent",),
        expected_evidence=("an eligible alternative provider was selected",),
        success_predicate="alternative_provider_available",
        failure_predicate="no_eligible_alternative",
        terminal_verdict=TerminalVerdict.BLOCKED,
    ),
    RecoveryRecipe(
        recipe_id="recover.network.retry.v1",
        category=FailureCategory.NETWORK,
        family=RecipeFamily.PROVIDER_INTERRUPTION,
        action=RecoveryAction(
            action="wait_and_retry",
            rationale="Transient network fault; one bounded retry before giving up.",
            parameters={"max_attempts": 2, "backoff_seconds": 2},
        ),
        trigger_evidence=("connection reset, DNS failure or timeout before response",),
        requirements=_NEEDS_NETWORK,
        caps=RecipeCaps(max_attempts=2, max_elapsed_seconds=45.0),
        allowed_operations=("wait_and_retry",),
        prohibited_operations=("retry_without_reconciliation",),
        expected_evidence=("a response was received on the retry",),
        success_predicate="response_received",
        failure_predicate="second_attempt_also_failed",
        # A transport interruption may have delivered the request. #616 owns
        # proving that; this recipe declares it must be proven first.
        requires_reconciliation=True,
        checkpoint_inputs=("last_operation_id",),
        checkpoint_outputs=("reconciled_operation_state",),
    ),
    RecoveryRecipe(
        recipe_id="recover.no_progress.compact.v1",
        category=FailureCategory.NO_PROGRESS,
        family=RecipeFamily.CONTEXT_OVERLOAD,
        action=RecoveryAction(
            action="compact_context",
            rationale=(
                "The run stopped learning. Compress findings so the next step "
                "reasons over a summary instead of re-reading the same material."
            ),
        ),
        trigger_evidence=("no new objective-linked evidence across recent turns",),
        caps=RecipeCaps(max_attempts=1, max_elapsed_seconds=60.0),
        allowed_operations=("compact_context",),
        prohibited_operations=("broad_rescan", "repeat_equivalent_read"),
        expected_evidence=("context size fell and verified facts were retained",),
        success_predicate="context_reduced_facts_preserved",
        failure_predicate="nothing_compactable",
        checkpoint_inputs=("evidence_ledger",),
        checkpoint_outputs=("compacted_evidence",),
    ),
    # --- #653 families not covered by the prototype ------------------------
    RecoveryRecipe(
        recipe_id="recover.exploration.localise.v1",
        category=FailureCategory.NO_PROGRESS,
        family=RecipeFamily.BROAD_EXPLORATION,
        signature_pattern=r"broad|exploration|no_localisation|searching",
        trigger_evidence=("many reads/searches, no file or symbol localised",),
        action=RecoveryAction(
            action="targeted_symbol_search",
            rationale=(
                "Exploration is broad but has localised nothing. Compact what "
                "is verified, then make one targeted symbol/test lookup rather "
                "than another sweep."
            ),
        ),
        caps=RecipeCaps(max_attempts=2, max_elapsed_seconds=60.0),
        allowed_operations=("targeted_symbol_search", "read_file"),
        prohibited_operations=("broad_rescan", "repeat_equivalent_read"),
        expected_evidence=("a specific symbol, file or test was identified",),
        success_predicate="target_localised",
        failure_predicate="still_unlocalised_after_caps",
        checkpoint_inputs=("evidence_ledger",),
        checkpoint_outputs=("localised_target",),
    ),
    RecoveryRecipe(
        recipe_id="recover.repeats.replan.v1",
        category=FailureCategory.REPEATED_FAILURE,
        family=RecipeFamily.EQUIVALENT_REPEATS,
        signature_pattern=r"equivalent|duplicate|already_read|same_query",
        trigger_evidence=("semantically equivalent reads/searches repeated",),
        action=RecoveryAction(
            action="replan_from_evidence",
            rationale=(
                "The same information is being fetched under different "
                "spellings. Re-plan from what is already known instead of "
                "issuing another equivalent query."
            ),
        ),
        caps=RecipeCaps(max_attempts=1, max_elapsed_seconds=60.0),
        allowed_operations=("replan_from_evidence", "compact_context"),
        prohibited_operations=("repeat_equivalent_read", "broad_rescan"),
        expected_evidence=("a plan step differing from prior attempts",),
        success_predicate="new_distinct_step_chosen",
        failure_predicate="no_distinct_step_available",
        # Detecting *semantic* equivalence is #649's job; this recipe fires on
        # the evidence #649 will produce and is inert until then.
        requirements=RecipeRequirements(preconditions=("equivalence_detector",)),
    ),
    RecoveryRecipe(
        recipe_id="recover.reproducer.request.v1",
        category=FailureCategory.REPEATED_FAILURE,
        family=RecipeFamily.MISSING_REPRODUCER,
        signature_pattern=r"no_repro|cannot_reproduce|missing_fixture|no failing test",
        trigger_evidence=("repair attempted with no failing test or fixture",),
        action=RecoveryAction(
            action="request_reproducer",
            rationale=(
                "Nothing here reproduces the reported problem, so any change "
                "would be unverifiable. Ask for a failing case before editing."
            ),
        ),
        caps=RecipeCaps(max_attempts=1, max_elapsed_seconds=15.0),
        allowed_operations=("request_reproducer", "ask_user"),
        prohibited_operations=("edit_without_reproducer", "guess_fix"),
        expected_evidence=("a failing test or reproduction command",),
        success_predicate="reproducer_supplied",
        failure_predicate="user_declined",
        terminal_verdict=TerminalVerdict.AWAITING_INPUT,
    ),
    RecoveryRecipe(
        recipe_id="recover.plan.invalid_assumption.v1",
        category=FailureCategory.REPEATED_FAILURE,
        family=RecipeFamily.INVALID_PLAN_ASSUMPTION,
        signature_pattern=r"assumption|not_found|does_not_exist|no such",
        trigger_evidence=("a plan step referenced something that does not exist",),
        action=RecoveryAction(
            action="replan_from_evidence",
            rationale=(
                "The plan assumed something the repository does not contain. "
                "Re-plan from verified facts rather than retrying the step."
            ),
        ),
        caps=RecipeCaps(max_attempts=1, max_elapsed_seconds=60.0),
        allowed_operations=("replan_from_evidence",),
        prohibited_operations=("retry_same_step",),
        expected_evidence=("a plan grounded in confirmed repository facts",),
        success_predicate="replanned",
        failure_predicate="objective_unreachable",
    ),
    RecoveryRecipe(
        recipe_id="recover.provider.interruption.reconcile.v1",
        category=FailureCategory.PROVIDER_ERROR,
        family=RecipeFamily.PROVIDER_INTERRUPTION,
        signature_pattern=r"stream|interrupt|aborted|incomplete|truncat",
        trigger_evidence=("stream ended before a terminal provider event",),
        action=RecoveryAction(
            action="reconcile_operation",
            rationale=(
                "The stream stopped mid-flight, so whether the provider "
                "completed or billed the call is unknown. Reconcile before "
                "any resume — a timeout is not proof of non-delivery."
            ),
        ),
        caps=RecipeCaps(max_attempts=1, max_elapsed_seconds=30.0),
        allowed_operations=("reconcile_operation",),
        prohibited_operations=("blind_retry", "duplicate_dispatch"),
        expected_evidence=("operation state resolved to delivered or not",),
        success_predicate="operation_reconciled",
        failure_predicate="continuity_unprovable",
        requires_reconciliation=True,
        checkpoint_inputs=("last_operation_id", "partial_output"),
        checkpoint_outputs=("reconciled_operation_state",),
    ),
    RecoveryRecipe(
        recipe_id="recover.tool.malformed_output.v1",
        category=FailureCategory.PROVIDER_ERROR,
        family=RecipeFamily.MALFORMED_TOOL_OUTPUT,
        signature_pattern=r"malformed|invalid json|parse|unexpected token|decode",
        trigger_evidence=("tool output failed to parse against its schema",),
        action=RecoveryAction(
            action="verify_tool_capability",
            rationale=(
                "The tool returned something its schema does not describe. "
                "Confirm the installed capability before trusting or retrying "
                "its output."
            ),
        ),
        caps=_ONE_CHEAP_ATTEMPT,
        allowed_operations=("verify_tool_capability",),
        prohibited_operations=("parse_with_heuristics", "assume_schema"),
        expected_evidence=("tool version and capability confirmed",),
        success_predicate="capability_confirmed",
        failure_predicate="capability_unknown",
    ),
    RecoveryRecipe(
        recipe_id="recover.repository.refresh.v1",
        category=FailureCategory.REPEATED_FAILURE,
        family=RecipeFamily.REPOSITORY_MOVEMENT,
        signature_pattern=r"\bconflict|\bstale\b|non-fast-forward|index\.lock|\bmoved\b|\brebase\b",
        trigger_evidence=("repository revision moved under the run",),
        action=RecoveryAction(
            action="refresh_repository_state",
            rationale=(
                "The working tree moved since this run started. Re-read the "
                "current revision before acting on stale assumptions."
            ),
        ),
        requirements=RecipeRequirements(requires_repository=True),
        caps=_ONE_CHEAP_ATTEMPT,
        allowed_operations=("refresh_repository_state", "read_file"),
        # Resolving a conflict is a mutation and needs the normal authority
        # path — recovery may observe the movement, never rewrite history.
        prohibited_operations=("git_reset", "force_push", "auto_merge", "git_commit"),
        expected_evidence=("current HEAD and dirty state re-read",),
        success_predicate="repository_state_refreshed",
        failure_predicate="repository_unreadable",
        checkpoint_inputs=("expected_revision",),
        checkpoint_outputs=("observed_revision",),
    ),
    RecoveryRecipe(
        recipe_id="recover.checks.repeated_failure.v1",
        category=FailureCategory.REPEATED_FAILURE,
        family=RecipeFamily.REPEATED_CHECK_FAILURE,
        signature_pattern=r"\btests?\b|\blint\b|\btype(?:check|s)?\b|\bcompile\b|\bbuild\b|\bsecurity\b",
        trigger_evidence=("the same check failed on consecutive attempts",),
        action=RecoveryAction(
            action="replan_from_evidence",
            rationale=(
                "The same check keeps failing, so the current approach is not "
                "converging. Re-plan against the failure output rather than "
                "re-running the identical fix."
            ),
        ),
        caps=RecipeCaps(max_attempts=2, max_elapsed_seconds=120.0),
        allowed_operations=("replan_from_evidence", "read_file", "run_tests"),
        prohibited_operations=("repeat_identical_fix", "disable_the_check"),
        expected_evidence=("a distinct fix targeting the reported failure",),
        success_predicate="check_passes",
        failure_predicate="same_failure_after_caps",
        terminal_verdict=TerminalVerdict.PARTIAL,
    ),
    RecoveryRecipe(
        recipe_id="recover.verification.repair.v1",
        category=FailureCategory.REPEATED_FAILURE,
        family=RecipeFamily.VERIFICATION_REPAIR,
        signature_pattern=r"verification|verify_failed|unverified|repair",
        trigger_evidence=("verification failed while other checks still pass",),
        action=RecoveryAction(
            action="rerun_affected_verification",
            rationale=(
                "Verification found one failed requirement. Repair that, then "
                "re-run only the affected checks — passing evidence already "
                "collected is not discarded."
            ),
        ),
        caps=RecipeCaps(max_attempts=2, max_elapsed_seconds=180.0),
        allowed_operations=("rerun_affected_verification", "run_tests", "read_file"),
        prohibited_operations=("discard_passing_evidence", "rerun_all_verification"),
        expected_evidence=("previously passing checks retained; failed one addressed",),
        success_predicate="affected_verification_passes",
        failure_predicate="repair_exhausted",
        checkpoint_inputs=("passing_verification_results",),
        checkpoint_outputs=("verification_results",),
        terminal_verdict=TerminalVerdict.PARTIAL,
    ),
    RecoveryRecipe(
        recipe_id="recover.approval.expired.v1",
        category=FailureCategory.PERMISSION_DENIED,
        family=RecipeFamily.APPROVAL_EXPIRY,
        trigger_evidence=("a grant was consumed, expired or revoked mid-run",),
        action=RecoveryAction(
            action="request_approval",
            rationale=(
                "The approval this step relied on is no longer valid. Ask "
                "again — a lapsed grant is never silently re-used."
            ),
        ),
        caps=RecipeCaps(max_attempts=1, max_elapsed_seconds=15.0),
        allowed_operations=("request_approval", "ask_user"),
        prohibited_operations=(
            "reuse_expired_grant",
            "widen_scope",
            "proceed_unapproved",
        ),
        expected_evidence=("a fresh, scoped approval",),
        success_predicate="approval_granted",
        failure_predicate="approval_declined_or_timeout",
        terminal_verdict=TerminalVerdict.AWAITING_INPUT,
    ),
    RecoveryRecipe(
        recipe_id="recover.budget.exhausted.v1",
        category=FailureCategory.QUOTA_OR_RATE_LIMIT,
        family=RecipeFamily.BUDGET_EXHAUSTION,
        signature_pattern=r"budget|cap exceeded|spend limit|firewall",
        trigger_evidence=("the cost firewall refused the next paid route",),
        action=RecoveryAction(
            action="report_budget_exhausted",
            rationale=(
                "The budget ceiling stopped this run. Report it with what was "
                "spent; raising a cap is the user's decision, never Vesta's."
            ),
        ),
        caps=RecipeCaps(max_attempts=1, max_elapsed_seconds=10.0),
        allowed_operations=("report_budget_exhausted", "stop_with_reason"),
        prohibited_operations=("raise_budget", "route_paid_anyway", "ignore_cap"),
        expected_evidence=("spend so far and the cap that was hit",),
        success_predicate="budget_state_reported",
        failure_predicate="budget_state_unreadable",
        terminal_verdict=TerminalVerdict.BLOCKED,
    ),
    RecoveryRecipe(
        recipe_id="recover.crash.continuity.v1",
        category=FailureCategory.PROVIDER_ERROR,
        family=RecipeFamily.CRASH_CONTINUITY,
        signature_pattern=r"crash|restart|interrupted_run|incomplete_operation",
        trigger_evidence=("a prior process left an operation without an outcome",),
        action=RecoveryAction(
            action="reconcile_operation",
            rationale=(
                "A previous process died mid-operation. Establish what "
                "actually happened before resuming; restarting blind can "
                "repeat a side effect that already landed."
            ),
        ),
        caps=RecipeCaps(max_attempts=1, max_elapsed_seconds=60.0),
        allowed_operations=("reconcile_operation", "resume_from_checkpoint"),
        prohibited_operations=("blind_resume", "duplicate_dispatch"),
        expected_evidence=("each outstanding operation resolved or declared unknown",),
        success_predicate="all_operations_reconciled",
        failure_predicate="continuity_unprovable",
        requires_reconciliation=True,
        checkpoint_inputs=("interrupted_checkpoint_id",),
        checkpoint_outputs=("resumed_run_state",),
    ),
)


# Families #653 lists that are deliberately NOT automated, each with the
# reason acceptance criterion 1 requires. Kept beside the library so the
# coverage test can assert every family is accounted for one way or the other.
NON_AUTOMATABLE: dict[RecipeFamily, str] = {}


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


@dataclass(frozen=True)
class ConsideredRecipe:
    """One candidate and why it was or was not chosen."""

    recipe_id: str
    eligible: bool
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "recipe_id": self.recipe_id,
            "eligible": self.eligible,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class SelectionTrace:
    """Every recipe considered, and why the winner won (#653 execution contract).

    "Every considered and excluded recipe is recorded with reason" — without
    this a deterministic selector is still a black box, and nobody can tell a
    correct refusal from a missing rule.
    """

    selected: RecoveryRecipe | None
    considered: tuple[ConsideredRecipe, ...]
    signature: str
    outcome: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "selected": self.selected.recipe_id if self.selected else None,
            "outcome": self.outcome,
            "signature": self.signature,
            "considered": [item.to_dict() for item in self.considered],
        }


@dataclass
class RecipeBudget:
    """Enforces one recipe's caps, and can only ever count down.

    #653: "A recipe cannot loop into itself without decrementing hard caps."
    :meth:`spend` refuses to make any allowance larger, so a recursive or
    misbehaving recipe runs out rather than renewing itself.
    """

    caps: RecipeCaps
    attempts_used: int = 0
    elapsed_seconds: float = 0.0
    spent_usd: float = 0.0

    def remaining_attempts(self) -> int:
        return max(0, self.caps.max_attempts - self.attempts_used)

    def exhausted(self) -> bool:
        return (
            self.remaining_attempts() <= 0
            or self.elapsed_seconds >= self.caps.max_elapsed_seconds
            or self.spent_usd >= self.caps.max_incremental_spend_usd > 0
        )

    def spend(self, *, seconds: float = 0.0, usd: float = 0.0) -> None:
        """Consume one attempt plus the given time/money. Never restores."""

        if seconds < 0 or usd < 0:
            raise RecipeError("a cap can only be consumed, never restored")
        self.attempts_used += 1
        self.elapsed_seconds += seconds
        self.spent_usd += usd

    def to_dict(self) -> dict[str, Any]:
        return {
            "caps": self.caps.to_dict(),
            "attempts_used": self.attempts_used,
            "elapsed_seconds": self.elapsed_seconds,
            "spent_usd": self.spent_usd,
            "remaining_attempts": self.remaining_attempts(),
            "exhausted": self.exhausted(),
        }


def select_recipe_traced(
    envelope: FailureEnvelope,
    *,
    ledger: RecoveryLedger | None = None,
    failing_action: str = "",
    recipes: Sequence[RecoveryRecipe] = RECIPES,
    context: Mapping[str, Any] | None = None,
) -> SelectionTrace:
    """Choose a recipe and record every candidate considered.

    Deterministic: the same envelope, ledger, context and library always
    produce the same selection and the same trace.
    """

    signature = envelope.error_signature or envelope.primary_cause.value
    considered: list[ConsideredRecipe] = []

    if not envelope.is_actionable:
        return SelectionTrace(
            selected=None,
            considered=(),
            signature=signature,
            outcome="diagnosis is not actionable; stopping instead of guessing",
        )

    selected: RecoveryRecipe | None = None
    for recipe in recipes:
        if not recipe.matches(envelope):
            continue  # a non-match is not a *rejection*; keep the trace signal-dense
        if selected is not None:
            considered.append(
                ConsideredRecipe(
                    recipe.recipe_id, False, "a higher-priority recipe already matched"
                )
            )
            continue
        if recipe.action.action == failing_action:
            considered.append(
                ConsideredRecipe(
                    recipe.recipe_id, False, "its action is the one that just failed"
                )
            )
            continue
        if ledger is not None and ledger.already_tried(signature, recipe.recipe_id):
            considered.append(
                ConsideredRecipe(
                    recipe.recipe_id, False, "already tried for this failure signature"
                )
            )
            continue
        violation = recipe.violates(context)
        if violation:
            considered.append(ConsideredRecipe(recipe.recipe_id, False, violation))
            continue
        considered.append(ConsideredRecipe(recipe.recipe_id, True, "selected"))
        selected = recipe

    outcome = (
        f"selected {selected.recipe_id}"
        if selected is not None
        else "no eligible recipe; stopping honestly rather than forcing one"
    )
    return SelectionTrace(
        selected=selected,
        considered=tuple(considered),
        signature=signature,
        outcome=outcome,
    )


def select_recipe(
    envelope: FailureEnvelope,
    *,
    ledger: RecoveryLedger | None = None,
    failing_action: str = "",
    recipes: Sequence[RecoveryRecipe] = RECIPES,
    context: Mapping[str, Any] | None = None,
) -> RecoveryRecipe | None:
    """The first safe, untried, eligible recipe for this failure, or None.

    None is a real answer: "no deterministic recovery applies" is what turns a
    stall into an honest stop instead of another expensive guess.
    """

    return select_recipe_traced(
        envelope,
        ledger=ledger,
        failing_action=failing_action,
        recipes=recipes,
        context=context,
    ).selected


def disable_recipe(
    recipe_id: str, *, recipes: Sequence[RecoveryRecipe] = RECIPES
) -> tuple[RecoveryRecipe, ...]:
    """Return the library with one recipe rolled back (#653 AC10).

    Independent rollback: everything else keeps working, and the disabled
    recipe still appears in selection traces with its reason, so a rollback
    is visible rather than a silent disappearance.
    """

    return tuple(
        replace(item, governance=replace(item.governance, enabled=False))
        if item.recipe_id == recipe_id
        else item
        for item in recipes
    )


def library_coverage() -> dict[str, Any]:
    """Which of the fifteen families the shipped library covers (#653 AC1)."""

    covered: dict[str, list[str]] = {}
    for recipe in RECIPES:
        if recipe.family is not None:
            covered.setdefault(recipe.family.value, []).append(recipe.recipe_id)
    return {
        "report": "opai-recovery-recipe-coverage",
        "families": {
            family.value: {
                "recipes": sorted(covered.get(family.value, [])),
                "automated": bool(covered.get(family.value)),
                "not_automatable_reason": NON_AUTOMATABLE.get(family, ""),
            }
            for family in RecipeFamily
        },
        "recipe_count": len(RECIPES),
    }
