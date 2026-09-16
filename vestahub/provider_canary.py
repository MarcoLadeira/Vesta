"""Production-owned contract for one protected provider canary call."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from .provider_adapters import ProviderAdapter
from .provider_catalog import CATALOG_VERSION, PROTOCOL_VERSION, provider_record
from .provider_protocol import (
    AdapterRequest,
    AdapterSLO,
    ProtocolViolation,
    ProviderReadiness,
)


CANARY_PROMPT = "Reply with exactly: OK"


def _live_models() -> tuple[str, ...]:
    return tuple(
        item.strip()
        for item in os.environ.get("VESTA_LIVE_MODELS", "").split(",")
        if item.strip()
    )


def _selected_live_providers() -> frozenset[str]:
    return frozenset(
        item.strip().lower()
        for item in os.environ.get("VESTA_LIVE_PROVIDER_SMOKE_PROVIDERS", "").split(",")
        if item.strip()
    )


def model_for_provider(provider_id: str) -> str | None:
    from .model_identity import model_provider

    return next(
        (
            model_id
            for model_id in _live_models()
            if model_provider(model_id) == provider_id
        ),
        None,
    )


def live_provider_prerequisite(provider_id: str) -> str | None:
    """Return the exact opt-in condition that prevents one provider call."""

    if os.environ.get("VESTA_LIVE_PROVIDER_SMOKE") != "1":
        return "requires VESTA_LIVE_PROVIDER_SMOKE=1"
    if os.environ.get("VESTA_CONFIRM_CLOUD_TESTS") != "YES":
        return "requires VESTA_CONFIRM_CLOUD_TESTS=YES"
    if provider_id not in _selected_live_providers():
        return f"requires {provider_id} in VESTA_LIVE_PROVIDER_SMOKE_PROVIDERS"
    if model_for_provider(provider_id) is None:
        return f"requires a {provider_id} model in VESTA_LIVE_MODELS"
    return None


def _safe_readiness_input(
    adapter: ProviderAdapter,
    probe: Mapping[str, Any],
) -> dict[str, bool]:
    """Project only typed, prompt-free diagnostics into readiness."""

    profile = adapter.profile
    contract = profile.contract
    if not isinstance(contract, Mapping):
        return {}
    requirements = contract.get("requirements")
    if not isinstance(requirements, Mapping):
        return {}
    source: dict[str, bool] = {}

    cli_present = probe.get("cliPresent", probe.get("cli_present"))
    if profile.requires_cli and isinstance(cli_present, bool):
        source["installed"] = cli_present

    configured = probe.get("configured")
    if isinstance(configured, bool):
        source["configured"] = configured

    auth_status = str(probe.get("authStatus") or "").strip().lower()
    if auth_status == "connected":
        source.setdefault("configured", True)
        source["authenticated"] = True
        source["healthy"] = True
    elif auth_status in {"not_configured", "invalid", "expired", "disconnected"}:
        source.setdefault("configured", False)
        source["authenticated"] = False
        source["healthy"] = False

    available = probe.get("available")
    if requirements.get("local_service") is True and isinstance(available, bool):
        source["installed"] = available
        source["configured"] = available
        source["healthy"] = available

    connected = probe.get("connected")
    if profile.requires_api_key and isinstance(connected, bool):
        source["healthy"] = connected

    return source


def _contract_prerequisite(adapter: ProviderAdapter, provider_id: str) -> str | None:
    profile = adapter.profile
    record = provider_record(provider_id)
    if profile.catalog_version != CATALOG_VERSION:
        return f"{provider_id} catalog version is incompatible"
    if profile.protocol_version != PROTOCOL_VERSION:
        return f"{provider_id} protocol version is incompatible"
    if dict(profile.capability_status) != dict(record["capabilities"]):
        return f"{provider_id} capability contract does not match the pinned catalog"
    contract = profile.contract
    if not isinstance(contract, Mapping):
        return f"{provider_id} has no readable adapter contract"
    if dict(contract.get("requirements") or {}) != dict(record["requirements"]):
        return f"{provider_id} requirements contract does not match the pinned catalog"
    cancellation = contract.get("cancellation")
    if not isinstance(cancellation, Mapping) or dict(cancellation) != dict(
        record["cancellation"]
    ):
        return f"{provider_id} cancellation contract does not match the pinned catalog"
    try:
        slo = AdapterSLO(cancel_ack_seconds=cancellation["slo_seconds"])
    except (KeyError, ProtocolViolation, TypeError):
        return f"{provider_id} cancellation SLO is invalid"
    if slo.cancel_ack_seconds != float(record["cancellation"]["slo_seconds"]):
        return f"{provider_id} cancellation SLO is incompatible"
    if dict(contract.get("unsupportedBehavior") or {}) != dict(
        record["unsupported_behavior"]
    ):
        return f"{provider_id} unsupported-capability contract does not fail closed"
    try:
        adapter.validate_request(
            AdapterRequest(
                provider_id,
                f"live-smoke-preflight-{provider_id}",
                ("chat",),
                protocol_version=PROTOCOL_VERSION,
            )
        )
    except (ProtocolViolation, ValueError, TypeError):
        return f"{provider_id} adapter request contract is incompatible"
    return None


def _readiness_prerequisite(
    provider_id: str,
    readiness: ProviderReadiness,
    profile: Any,
) -> str | None:
    if not isinstance(readiness, ProviderReadiness):
        return f"{provider_id} readiness probe returned no protocol readiness"
    if (
        readiness.provider_id != provider_id
        or readiness.protocol_version != PROTOCOL_VERSION
    ):
        return f"{provider_id} readiness protocol version is incompatible"
    if profile.requires_cli and readiness.installed is not True:
        return f"requires {provider_id} CLI installed and detected"
    if (
        profile.requires_api_key or profile.requires_oauth
    ) and readiness.configured is not True:
        return f"requires {provider_id} credentials configured"
    if profile.requires_oauth and readiness.authenticated is not True:
        return f"requires {provider_id} authentication verified by its safe diagnostic"
    if readiness.authorised is False:
        return f"requires {provider_id} authorisation before live smoke"
    if readiness.healthy is not True:
        return f"requires a healthy {provider_id} non-completion diagnostic"
    return None


def adapter_prerequisite(
    provider_id: str,
    *,
    adapter_factory: Callable[[str], ProviderAdapter] = ProviderAdapter,
) -> str | None:
    """Probe a selected adapter, then require catalog-backed readiness."""

    try:
        adapter = adapter_factory(provider_id)
        probe = (
            adapter.probe(force=True)
            if adapter.profile.requires_api_key
            else adapter.probe()
        )
    except (OSError, RuntimeError, ValueError, TypeError):
        return f"requires a safe {provider_id} adapter diagnostic"
    if not isinstance(probe, Mapping):
        return f"requires a safe {provider_id} adapter diagnostic"
    contract_reason = _contract_prerequisite(adapter, provider_id)
    if contract_reason is not None:
        return contract_reason
    readiness = adapter.readiness(_safe_readiness_input(adapter, probe))
    return _readiness_prerequisite(provider_id, readiness, adapter.profile)


def run_selected_provider_canary(
    provider_id: str,
    model_id: str,
    *,
    adapter_factory: Callable[[str], ProviderAdapter] = ProviderAdapter,
) -> tuple[str | None, dict[str, Any] | None]:
    """Run one fixed remote prompt and attach its single ledger observation."""

    prerequisite = live_provider_prerequisite(provider_id)
    if prerequisite is not None:
        return prerequisite, None
    from .model_identity import model_provider

    if model_provider(model_id) != provider_id:
        return f"model {model_id!r} is not owned by {provider_id!r}", None
    if model_id not in _live_models():
        return f"requires exact model {model_id} in VESTA_LIVE_MODELS", None
    prerequisite = adapter_prerequisite(
        provider_id,
        adapter_factory=adapter_factory,
    )
    if prerequisite is not None:
        return prerequisite, None

    from vesta.app_state import ask
    from .ledger import EVENT_MODEL_CALL, read_events

    with tempfile.TemporaryDirectory() as temporary_directory:
        root = Path(temporary_directory)
        result = ask(
            root,
            CANARY_PROMPT,
            model_choice=model_id,
            allow_cloud=True,
            allow_edits=False,
            tool_calling_enabled=False,
            mode="ask",
        )
        observations = [
            event
            for event in read_events(root)
            if event.get("event_type") == EVENT_MODEL_CALL
        ]
    if not isinstance(result, dict):
        return None, None
    return None, {
        **result,
        "canary_observation": observations[0] if len(observations) == 1 else None,
    }
