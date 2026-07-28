"""Capability- and cost-aware candidate ordering for OPai Auto mode.

Auto's job: pick the **cheapest model that can actually complete the task**,
and if that model fails, move on to the next one *without* dead-ending or
asking the user to prompt again.

This module builds the ordered *fallback chain* Auto walks. The pipeline
(`opaihub.gui_pipeline`) executes candidates in order, advancing on a
retryable failure. The ordering policy, in one place so it is testable:

1. **Local first.** A live-detection sentinel (`"auto"`) always leads: a local
   model is free and private, so Auto tries one before anything leaves the
   device.
2. **Then configured free APIs**, cheapest tier of cloud. Ordered *within* the
   free group by reliability (recent failures sink), then least-recently-used
   (so Auto rotates instead of always picking whichever is first in the list),
   then a stable id sort.
3. **Then connected paid accounts**, ordered the same way. These sit at the
   tail because they cost real money — the pipeline confirms before the first
   paid call unless the user already authorized paid/cloud.

Nothing here is hardcoded to a specific model. The chain is derived entirely
from the live catalog, the task classification, and the local reliability
memory, so a user's own configured free and paid models are what Auto chooses
among.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import provider_balance as balance
from . import provider_blocks as blocks
from . import provider_reliability as reliability
from .model_intelligence import classify_task

# Pipeline statuses that mean "this provider could not do it, try the next one".
# Approvals, confirmations, cancels, and genuine answers are NOT here — those
# stop the fallback walk.
RETRYABLE_STATUSES = frozenset(
    {
        "runner_error",
        "failed",
        "error",
        "needs_model",
        "model_unavailable",
        "no_local_model",
        "capability_mismatch",
        "empty",
    }
)

# Statuses that must never trigger an automatic fallback: they need the user,
# or they already succeeded.
TERMINAL_STATUSES = frozenset(
    {
        "answered",
        "cancelled",
        "blocked",
        "needs_command_approval",
        "needs_edit_approval",
        "needs_free_confirmation",
        "needs_limit_confirmation",
        "needs_auto_confirmation",
        "needs_confirmation",
    }
)

_UNAVAILABLE_ACCOUNT_STATUSES = frozenset(
    {"misconfigured", "provider_unavailable", "invalid", "expired", "disconnected"}
)

# Error codes that describe the *transport*, not the provider's ability to
# answer: a blip, a dropped stream, a slow endpoint. Gemini's free tier in
# particular returns "provider temporarily unavailable" intermittently, and the
# identical prompt succeeds a second later. Abandoning the provider on the first
# blip is what makes OPai feel random, so these earn one immediate re-attempt on
# the SAME provider before the chain advances.
TRANSIENT_ERROR_CODES = frozenset(
    {
        "PROVIDER_UNAVAILABLE",
        "PROVIDER_TIMEOUT",
        "NETWORK_ERROR",
        "STREAM_ABORTED",
        "NO_RESPONSE",
    }
)

# One retry, not a loop: a second failure is evidence the provider is genuinely
# down, and the fallback chain is the better answer than a third attempt.
MAX_TRANSIENT_RETRIES = 1
# Short enough that the user reads it as the same request still working, long
# enough for a momentary 503 to clear.
TRANSIENT_RETRY_DELAY_SECONDS = 1.5


def is_transient_error(error: Any) -> bool:
    """True when a structured error is a transport blip worth re-attempting."""
    if not isinstance(error, dict):
        return False
    return str(error.get("code") or "").upper() in TRANSIENT_ERROR_CODES


def should_retry_same_provider(error: Any, attempts: int) -> bool:
    """Whether to re-run the identical request on the same provider.

    ``attempts`` is how many retries this provider has already been given for
    the current turn.
    """
    return is_transient_error(error) and attempts < MAX_TRANSIENT_RETRIES


def provider_of(model_id: str) -> str:
    """Extract the provider slug from a model id.

    ``account:claude:haiku`` → ``claude``; ``free:kimi:kimi-k2.6`` → ``kimi``;
    ``ollama:qwen`` → ``ollama``; ``auto`` → ``""`` (no real provider).
    """
    text = str(model_id or "").strip()
    if not text or text == "auto":
        return ""
    parts = text.split(":")
    if parts[0] in {"account", "free"} and len(parts) > 1:
        return parts[1].lower()
    return parts[0].lower()


def _provider_of_entry(item: dict[str, Any]) -> str:
    provider = str(item.get("provider") or "").strip().lower()
    return provider or provider_of(str(item.get("id") or ""))


def can_edit_repository(item: dict[str, Any]) -> bool:
    """Whether this catalog entry may be given repository write access.

    The catalog sets ``repo_editing`` to False for a CLI OPai will refuse to
    launch with write access (Copilot's, today, because it cannot expose a
    bounded edit-tool set). Routing an editing task there is a guaranteed
    refusal, so Auto must know *before* it picks, not after it fails. Missing
    means "no known limitation" and stays eligible.
    """
    return item.get("repo_editing") is not False


def is_retryable_status(status: str) -> bool:
    return str(status or "").strip() in RETRYABLE_STATUSES


def answer_is_empty(answer: Any) -> bool:
    return not str(answer or "").strip()


def reason_slug(status: str = "", error: Any = None) -> str:
    """A short reliability reason from a status / structured error code."""
    code = ""
    if isinstance(error, dict):
        code = str(error.get("code") or "")
    code = code.upper()
    if code.startswith("AUTH_"):
        return "auth"
    if code == "PROVIDER_RATE_LIMITED":
        return "rate-limit"
    if code == "PROVIDER_QUOTA_EXHAUSTED":
        return "quota"
    if code == "PROVIDER_TIMEOUT":
        return "timeout"
    if code == "PROVIDER_UNAVAILABLE":
        return "unavailable"
    status = str(status or "").strip()
    return {
        "capability_mismatch": "capability",
        "model_unavailable": "misconfigured",
        "needs_model": "misconfigured",
        "no_local_model": "no-local",
        "empty": "no-answer",
        "runner_error": "runner-error",
    }.get(status, status or "error")


def unavailable_account_providers(catalog: dict[str, Any]) -> set[str]:
    """Providers whose most recent connection health is a known failure."""
    return {
        str(conn.get("providerId") or "").lower()
        for conn in (catalog.get("connections") or [])
        if str(conn.get("authStatus") or "").lower() in _UNAVAILABLE_ACCOUNT_STATUSES
    }


def _rank_bucket(
    project_root: Path, candidates: list[dict[str, Any]], *, now: float | None
) -> list[dict[str, Any]]:
    """Order candidates in one cost bucket: healthy + sticky first, then LRU.

    Sort key (all ascending):
    ``(in_cooldown, reliability_penalty, not_sticky, last_used, id)``.

    A provider that just failed sinks (cooldown, penalty). Among equally healthy
    providers, one that answered successfully inside the stickiness window leads
    — otherwise LRU rotation would sort the provider that *just worked* last,
    and a follow-up turn would actively route away from it. Two consecutive
    turns of one conversation landing on two providers, with different style and
    different context, is visible inconsistency with no cause the user can see.

    Outside that window the original least-recently-used rotation applies, so
    load still spreads across free tiers instead of hammering one entry.
    """

    def key(item: dict[str, Any]) -> tuple[int, float, int, float, str]:
        provider = _provider_of_entry(item)
        return (
            1 if reliability.in_cooldown(project_root, provider, now=now) else 0,
            reliability.reliability_penalty(project_root, provider, now=now),
            0 if reliability.is_sticky(project_root, provider, now=now) else 1,
            reliability.last_used(project_root, provider),
            str(item.get("id") or ""),
        )

    return sorted(candidates, key=key)


def resolve_auto_chain(
    project_root: Path,
    task: str,
    catalog: dict[str, Any],
    *,
    allow_paid: bool = False,
    needs_edit: bool = False,
    now: float | None = None,
) -> list[dict[str, Any]]:
    """Build the ordered Auto fallback chain from the live catalog.

    Each element is ``{"id", "kind", "provider", "paid", "reason"}``. The first
    element is always the local live-detection sentinel. ``allow_paid`` only
    affects the reason text — paid accounts are always included so the pipeline
    can offer a confirmed escalation; it decides whether to run or confirm.

    ``needs_edit`` says this turn will write to the repository, which excludes
    providers that cannot be given a bounded edit-tool set (Copilot's CLI
    today). Those providers stay in the chain for read-only work.
    """
    root = project_root.expanduser().resolve()
    models = list(catalog.get("models") or [])
    unavailable = unavailable_account_providers(catalog)

    # A provider known to be out of credit cannot answer no matter how it is
    # ranked — calling it only burns a fallback step on a guaranteed refusal.
    # Unlike a reliability cooldown (a heuristic that only *deprioritizes*),
    # exhaustion is observed fact, so these are excluded outright. The verdict
    # expires (provider_balance.EXHAUSTED_TTL_SECONDS) so a recharge made
    # outside OPai is rediscovered automatically.
    def has_credit(item: dict[str, Any]) -> bool:
        if item.get("out_of_credit") is True:
            return False
        return not balance.is_exhausted(root, _provider_of_entry(item), now=now)

    # Same logic as out-of-credit, for the other class of guaranteed refusal:
    # a CLI too old for its model, a broken provider config, or (for editing
    # turns only) a provider that cannot be sandboxed for repository writes.
    # Calling these spends a fallback step on a refusal OPai has already seen.
    def is_usable(item: dict[str, Any]) -> bool:
        if needs_edit and not can_edit_repository(item):
            return False
        return not blocks.is_blocked(
            root, _provider_of_entry(item), needs_edit=needs_edit, now=now
        )

    free = [
        item
        for item in models
        if item.get("kind") == "free"
        and item.get("available") is True
        and has_credit(item)
        and is_usable(item)
    ]
    accounts = [
        item
        for item in models
        if item.get("kind") == "account"
        and item.get("available") is True
        and _provider_of_entry(item) not in unavailable
        and has_credit(item)
        and is_usable(item)
    ]

    classification = classify_task(root, task)
    task_type = classification.get("task_type", "general_coding")

    chain: list[dict[str, Any]] = [
        {
            "id": "auto",
            "kind": "local",
            "provider": "local",
            "paid": False,
            "reason": "local-first (private, no cost)",
        }
    ]
    for item in _rank_bucket(root, free, now=now):
        chain.append(
            {
                "id": str(item["id"]),
                "kind": "free",
                "provider": _provider_of_entry(item),
                "paid": False,
                "reason": f"cheapest capable free API for {task_type}",
            }
        )
    for item in _rank_bucket(root, accounts, now=now):
        chain.append(
            {
                "id": str(item["id"]),
                "kind": "account",
                "provider": _provider_of_entry(item),
                "paid": True,
                "reason": (
                    f"paid escalation for {task_type}"
                    if allow_paid
                    else f"paid escalation for {task_type} (needs confirmation)"
                ),
            }
        )
    return chain


def best_alternative(
    project_root: Path,
    catalog: dict[str, Any],
    *,
    exclude_providers: set[str] | frozenset[str] | None = None,
    exclude_ids: set[str] | frozenset[str] | None = None,
    needs_edit: bool = False,
    now: float | None = None,
) -> dict[str, Any] | None:
    """The best model that can still run after another one failed.

    This exists so a failure is never a dead end. When a provider refuses —
    capped, stale CLI, write-incapable, or simply down — OPai can name one
    concrete model the user can continue with in a single click instead of
    leaving them to guess in the picker. Cheapest-first, same ordering policy as
    the Auto chain: on-device, then free, then paid.

    Returns ``None`` only when nothing at all is runnable, which is the one case
    where an honest dead end is the truthful answer.
    """
    root = project_root.expanduser().resolve()
    skip_providers = {str(p or "").lower() for p in (exclude_providers or set())}
    skip_ids = {str(i or "") for i in (exclude_ids or set())}
    unavailable = unavailable_account_providers(catalog)

    def eligible(item: dict[str, Any], kind: str) -> bool:
        if item.get("kind") != kind:
            return False
        if str(item.get("id") or "") in skip_ids:
            return False
        provider = _provider_of_entry(item)
        if provider and provider in skip_providers:
            return False
        if needs_edit and not can_edit_repository(item):
            return False
        if kind != "local":
            if item.get("available") is not True:
                return False
            if item.get("out_of_credit") is True or balance.is_exhausted(
                root, provider, now=now
            ):
                return False
            if blocks.is_blocked(root, provider, needs_edit=needs_edit, now=now):
                return False
        if kind == "account" and provider in unavailable:
            return False
        return True

    models = list(catalog.get("models") or [])
    for kind in ("local", "free", "account"):
        bucket = [item for item in models if eligible(item, kind)]
        if kind != "local":
            bucket = _rank_bucket(root, bucket, now=now)
        if bucket:
            item = bucket[0]
            return {
                "id": str(item.get("id") or ""),
                "label": str(item.get("label") or item.get("id") or ""),
                "kind": kind,
                "provider": _provider_of_entry(item),
                "paid": kind == "account",
            }
    return None


def routing_blockers(
    project_root: Path,
    catalog: dict[str, Any],
    *,
    needs_edit: bool = False,
    now: float | None = None,
) -> list[dict[str, str]]:
    """Why each configured provider cannot serve this turn, in plain language.

    When Auto runs out of candidates the honest answer is not "no model
    available" — OPai knows exactly which provider is capped, which CLI is
    stale, and which cannot be given write access. Listing that turns a dead
    end into a short, fixable to-do list.

    One entry per provider, ordered by provider id so the message is stable.
    """
    root = project_root.expanduser().resolve()
    unavailable = unavailable_account_providers(catalog)
    seen: dict[str, str] = {}
    for item in catalog.get("models") or []:
        if item.get("kind") not in {"free", "account"}:
            continue
        provider = _provider_of_entry(item)
        if not provider or provider in seen:
            continue
        # Most specific cause first — a capped account and a stale CLI need
        # completely different fixes, and only the exact one is useful.
        if item.get("out_of_credit") is True or balance.is_exhausted(
            root, provider, now=now
        ):
            snapshot = balance.balance_snapshot(root, provider, now=now)
            seen[provider] = (
                f"{snapshot['displayName']} is out of credit. "
                f"{snapshot['rechargeHint']}"
            )
            continue
        block = blocks.active_block(root, provider, now=now)
        if block and (block["scope"] != "edit" or needs_edit):
            seen[provider] = (
                f"{balance.provider_display_name(provider)}: "
                f"{block['title']} {block['remedy']}"
            )
            continue
        if provider in unavailable:
            seen[provider] = (
                f"{balance.provider_display_name(provider)} is not connected. "
                "Sign in again in Settings."
            )
            continue
        # Checked before the edit-capability rule on purpose: a provider that
        # cannot run *anything* has a more fundamental cause, and its own
        # disabled_reason carries the exact fix (Codex's stale CLI sets both
        # available=False and repo_editing=False — the CLI update is the honest
        # reason, not "cannot take write access").
        if item.get("available") is not True:
            reason = str(item.get("disabled_reason") or "").strip()
            seen[provider] = (
                f"{balance.provider_display_name(provider)}: {reason}"
                if reason
                else f"{balance.provider_display_name(provider)} is not set up yet."
            )
            continue
        if needs_edit and not can_edit_repository(item):
            seen[provider] = (
                f"{balance.provider_display_name(provider)} cannot be given safe "
                "repository write access from this CLI. Use it for Ask or Plan, "
                "or update its CLI for scoped tools."
            )
    return [
        {"provider": provider, "reason": reason}
        for provider, reason in sorted(seen.items())
    ]


def routing_diagnostics(
    project_root: Path,
    task: str,
    catalog: dict[str, Any],
    *,
    needs_edit: bool = False,
    now: float | None = None,
) -> dict[str, Any]:
    """Lightweight, secret-free view of how Auto ordered its candidates.

    For the internal diagnostics surface only — never shown as noise to a
    normal user, but available when a route needs explaining.
    """
    chain = resolve_auto_chain(
        project_root, task, catalog, needs_edit=needs_edit, now=now
    )
    root = project_root.expanduser().resolve()
    skipped = sorted(
        {
            _provider_of_entry(item)
            for item in catalog.get("models") or []
            if item.get("kind") in {"free", "account"}
            and (
                item.get("out_of_credit") is True
                or balance.is_exhausted(root, _provider_of_entry(item), now=now)
            )
        }
        - {""}
    )
    live_blocks = blocks.blocked_providers(root, needs_edit=needs_edit, now=now)
    return {
        "task_type": classify_task(project_root, task).get("task_type"),
        "chain": [
            {"id": c["id"], "provider": c["provider"], "paid": c["paid"]}
            for c in chain
        ],
        "reliability": reliability.reliability_snapshot(project_root, now=now),
        # Providers Auto refused to call because they are out of credit — the
        # explanation surface for "why isn't X in the chain?".
        "skipped_out_of_credit": skipped,
        # The other half of that answer: providers that are funded and connected
        # but provably cannot serve this request (stale CLI, invalid config, or
        # no bounded edit tools on an editing turn).
        "skipped_blocked": {
            provider: block["reason"] for provider, block in sorted(live_blocks.items())
        },
    }
