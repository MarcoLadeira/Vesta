"""Run only explicitly selected, budget-bounded non-production provider canaries.

This wrapper turns every skipped/unready selected provider into a non-zero
credential state. It is intentionally useful only inside the protected provider
environment; ordinary PRs never receive the required gates or credentials.
"""

from __future__ import annotations

import os
from decimal import Decimal, InvalidOperation
from typing import Any


PRODUCT_EXIT = 1
INFRASTRUCTURE_EXIT = 3
CREDENTIAL_EXIT = 4
MAX_CANARY_BUDGET_USD = Decimal("1.00")
REMOTE_STATUS_SOURCES = {
    "answered_by_account": None,
    "answered_by_free_api": "free_api",
    "answered_by_paid_api": "paid_api",
}
REMOTE_STATUS_PROVIDER_TYPES = {
    "answered_by_account": "cloud",
    "answered_by_free_api": "free_api",
    "answered_by_paid_api": "paid_api",
}


def _csv(name: str) -> tuple[str, ...]:
    return tuple(
        value.strip() for value in os.environ.get(name, "").split(",") if value.strip()
    )


def _budget_reason() -> str | None:
    raw = os.environ.get("VESTA_PROVIDER_CANARY_MAX_USD", "")
    try:
        value = Decimal(raw)
    except InvalidOperation:
        return "VESTA_PROVIDER_CANARY_MAX_USD must be a decimal"
    if not value.is_finite() or value <= 0 or value > MAX_CANARY_BUDGET_USD:
        return (
            f"VESTA_PROVIDER_CANARY_MAX_USD must be > 0 and <= {MAX_CANARY_BUDGET_USD}"
        )
    return None


def _model_map(providers: tuple[str, ...], models: tuple[str, ...]) -> dict[str, str]:
    from vestahub.model_identity import model_provider

    selected: dict[str, str] = {}
    for model in models:
        provider = model_provider(model)
        if provider in providers:
            if provider in selected:
                raise ValueError(f"provider {provider} has more than one canary model")
            selected[provider] = model
    missing = sorted(set(providers) - set(selected))
    if missing:
        raise ValueError(
            "selected providers lack an explicit model: " + ", ".join(missing)
        )
    return selected


def _run_provider(provider_id: str, model_id: str) -> tuple[str | None, dict | None]:
    from vestahub.provider_canary import run_selected_provider_canary

    return run_selected_provider_canary(provider_id, model_id)


def _prerequisite_reason() -> str | None:
    required_values = {
        "VESTA_LIVE_PROVIDER_SMOKE": "1",
        "VESTA_CONFIRM_CLOUD_TESTS": "YES",
        "VESTA_PROVIDER_CANARY_NON_PRODUCTION": "YES",
    }
    for name, expected in required_values.items():
        if os.environ.get(name) != expected:
            return f"requires {name}={expected}"
    return _budget_reason()


def _nonnegative_int(value: Any, *, positive: bool = False) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and value >= (1 if positive else 0)
    )


def _validated_cost(value: Any, provider_id: str, model_id: str) -> Decimal | None:
    """Return trustworthy observed cost, or fail closed with ``None``.

    The completion result alone is not qualification evidence. The sandboxed
    call also has to produce one non-local ledger observation whose raw and
    canonical identities match the selected provider/model, whose token usage
    came from the provider, and whose cost was calculated from a known price.
    """

    if not isinstance(value, dict):
        return None
    status = str(value.get("status") or "")
    if status not in REMOTE_STATUS_SOURCES:
        return None
    required_source = REMOTE_STATUS_SOURCES[status]
    if required_source is not None and value.get("source") != required_source:
        return None
    if status == "answered_by_account" and value.get("provider") != provider_id:
        return None
    if status != "answered_by_account" and value.get("model_id") != model_id:
        return None
    if value.get("answer") != "OK":
        return None
    if value.get("completion_state") != "completed":
        return None

    observation = value.get("canary_observation")
    if not isinstance(observation, dict):
        return None
    from vestahub.provider_canary import CANARY_PROMPT
    from vestahub.model_identity import canonical_usage_model_id
    from vestahub.ledger import EVENT_MODEL_CALL, task_fingerprint

    if observation.get("event_type") != EVENT_MODEL_CALL:
        return None
    if observation.get("task_hash") != task_fingerprint(CANARY_PROMPT):
        return None
    if observation.get("provider_id") != provider_id:
        return None
    if observation.get("model_id") != model_id:
        return None
    if observation.get("canonical_model_id") != canonical_usage_model_id(model_id):
        return None
    if observation.get("provider_type") != REMOTE_STATUS_PROVIDER_TYPES[status]:
        return None
    if observation.get("confirmed") is not True:
        return None
    if observation.get("is_local_route") is not False:
        return None
    if observation.get("model_calls") != 1:
        return None
    tokens = observation.get("tokens")
    input_tokens = observation.get("input_tokens")
    output_tokens = observation.get("output_tokens")
    if not _nonnegative_int(tokens, positive=True):
        return None
    if not _nonnegative_int(input_tokens):
        return None
    if not _nonnegative_int(output_tokens):
        return None
    if tokens != input_tokens + output_tokens:
        return None
    if observation.get("measurement") != "provider":
        return None
    if observation.get("cost_price_known") is not True:
        return None
    raw_cost = observation.get("estimated_actual_usd")
    if isinstance(raw_cost, bool) or not isinstance(raw_cost, (int, float)):
        return None
    try:
        cost = Decimal(str(raw_cost))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return cost if cost.is_finite() and cost >= 0 else None


def main() -> int:
    reason = _prerequisite_reason()
    if reason is not None:
        print(f"provider canary credential unavailable: {reason}")
        return CREDENTIAL_EXIT

    providers = tuple(
        value.lower() for value in _csv("VESTA_LIVE_PROVIDER_SMOKE_PROVIDERS")
    )
    models = _csv("VESTA_LIVE_MODELS")
    if not providers or not models:
        print(
            "provider canary credential unavailable: providers and models are required"
        )
        return CREDENTIAL_EXIT
    if len(set(providers)) != len(providers):
        print("provider canary credential unavailable: duplicate provider selector")
        return CREDENTIAL_EXIT
    try:
        selected = _model_map(providers, models)
    except (TypeError, ValueError) as error:
        print(f"provider canary credential unavailable: {error}")
        return CREDENTIAL_EXIT

    budget = Decimal(os.environ["VESTA_PROVIDER_CANARY_MAX_USD"])
    observed_total = Decimal("0")
    for provider_id in providers:
        try:
            unavailable, result = _run_provider(provider_id, selected[provider_id])
        except (OSError, TimeoutError) as error:
            print(
                f"provider canary infrastructure blocked for {provider_id}: {type(error).__name__}"
            )
            return INFRASTRUCTURE_EXIT
        except Exception as error:  # provider adapters expose heterogeneous SDK errors
            from vesta.provider_contract import redact_secrets

            print(
                f"provider canary failed for {provider_id}: {redact_secrets(error)[:500]}"
            )
            return PRODUCT_EXIT
        if unavailable is not None:
            print(
                f"provider canary credential unavailable for {provider_id}: {unavailable}"
            )
            return CREDENTIAL_EXIT
        observed_cost = _validated_cost(result, provider_id, selected[provider_id])
        if observed_cost is None:
            print(
                f"provider canary contract failed for {provider_id}: "
                "invalid identity, response, usage, or cost evidence"
            )
            return PRODUCT_EXIT
        observed_total += observed_cost
        if observed_total > budget:
            print(
                "provider canary contract failed: observed cumulative cost "
                f"USD {observed_total} exceeds run budget USD {budget}"
            )
            return PRODUCT_EXIT
        print(
            f"provider canary qualified: {provider_id} "
            f"({selected[provider_id]}, observed USD {observed_cost})"
        )

    print(f"provider canary qualified {len(providers)} explicitly selected provider(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
