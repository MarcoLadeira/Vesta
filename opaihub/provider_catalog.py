"""Pure loader for the pinned provider capability catalog.

The catalog is packaged data, not a live provider probe.  It can therefore be
replayed byte-for-byte and is safe to read from the CLI, GUI, or test suite
without touching the network, a provider binary, or credentials.
"""

from __future__ import annotations

import json
import math
from datetime import date
from functools import lru_cache
from importlib import resources
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

CATALOG_VERSION = "v1"
PROTOCOL_VERSION = 1

_CATALOG_RESOURCE = "data/provider_catalog/v1.json"
_PROVIDER_IDS = (
    "claude",
    "codex",
    "copilot",
    "kimi",
    "gemini",
    "groq",
    "mistral",
    "ollama",
    "openai-compatible",
)
_CAPABILITY_NAMES = frozenset(
    {
        "chat",
        "code_execution",
        "repo_read",
        "repo_editing",
        "run_tests",
        "streaming",
        "tool_calling",
        "structured_output",
    }
)
_CAPABILITY_STATUSES = frozenset({"supported", "partial", "unsupported"})
_REQUIREMENT_NAMES = frozenset(
    {"api_key", "oauth", "cli", "git_repository", "local_service", "network"}
)
_PRICING_MEASUREMENTS = frozenset({"actual", "derived", "estimated", "unavailable"})
_RECORD_FIELDS = frozenset(
    {
        "catalog_version",
        "protocol_version",
        "provider_id",
        "capabilities",
        "requirements",
        "cancellation",
        "unsupported_behavior",
        "pricing",
    }
)


def catalog_path() -> Path:
    """Return the installed package-data path for the pinned catalog."""

    return Path(__file__).parent / _CATALOG_RESOURCE


def catalog_bytes() -> bytes:
    """Read the package-data bytes without probing any external system."""

    return resources.files("opaihub").joinpath(_CATALOG_RESOURCE).read_bytes()


def _fail(message: str) -> None:
    raise ValueError(f"Invalid provider catalog: {message}")


def _require_mapping(value: Any, *, field: str, provider_id: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        _fail(f"{provider_id}.{field} must be an object")
    return value


def _require_exact_keys(
    value: Mapping[str, Any], *, field: str, provider_id: str, keys: frozenset[str]
) -> None:
    if set(value) != keys:
        _fail(f"{provider_id}.{field} has an incomplete or unknown field")


def _require_iso_date(value: Any, *, field: str, provider_id: str) -> None:
    if not isinstance(value, str):
        _fail(f"{provider_id}.{field} must be an ISO date")
    try:
        date.fromisoformat(value)
    except ValueError:
        _fail(f"{provider_id}.{field} must be an ISO date")


def _validate_record(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail("each record must be an object")
    if set(value) != _RECORD_FIELDS:
        _fail("record has an incomplete or unknown field")

    provider_id = value["provider_id"]
    if not isinstance(provider_id, str) or provider_id not in _PROVIDER_IDS:
        _fail("record has an unknown provider_id")
    if value["catalog_version"] != CATALOG_VERSION:
        _fail(f"{provider_id}.catalog_version must be {CATALOG_VERSION!r}")
    if value["protocol_version"] != PROTOCOL_VERSION:
        _fail(f"{provider_id}.protocol_version must be {PROTOCOL_VERSION}")

    capabilities = _require_mapping(
        value["capabilities"], field="capabilities", provider_id=provider_id
    )
    _require_exact_keys(
        capabilities,
        field="capabilities",
        provider_id=provider_id,
        keys=_CAPABILITY_NAMES,
    )
    if any(status not in _CAPABILITY_STATUSES for status in capabilities.values()):
        _fail(f"{provider_id}.capabilities has an invalid status")

    requirements = _require_mapping(
        value["requirements"], field="requirements", provider_id=provider_id
    )
    _require_exact_keys(
        requirements,
        field="requirements",
        provider_id=provider_id,
        keys=_REQUIREMENT_NAMES,
    )
    if any(not isinstance(required, bool) for required in requirements.values()):
        _fail(f"{provider_id}.requirements must contain booleans")

    cancellation = _require_mapping(
        value["cancellation"], field="cancellation", provider_id=provider_id
    )
    _require_exact_keys(
        cancellation,
        field="cancellation",
        provider_id=provider_id,
        keys=frozenset({"mode", "slo_seconds"}),
    )
    if not isinstance(cancellation["mode"], str) or not cancellation["mode"]:
        _fail(f"{provider_id}.cancellation.mode must be non-empty")
    if (
        not isinstance(cancellation["slo_seconds"], (int, float))
        or isinstance(cancellation["slo_seconds"], bool)
        or (
            isinstance(cancellation["slo_seconds"], float)
            and not math.isfinite(cancellation["slo_seconds"])
        )
        or cancellation["slo_seconds"] <= 0
    ):
        _fail(f"{provider_id}.cancellation.slo_seconds must be positive")

    unsupported = _require_mapping(
        value["unsupported_behavior"],
        field="unsupported_behavior",
        provider_id=provider_id,
    )
    _require_exact_keys(
        unsupported,
        field="unsupported_behavior",
        provider_id=provider_id,
        keys=frozenset({"mode", "message"}),
    )
    if unsupported["mode"] != "fail_closed" or not isinstance(
        unsupported["message"], str
    ):
        _fail(f"{provider_id}.unsupported_behavior must fail closed")

    pricing = _require_mapping(
        value["pricing"], field="pricing", provider_id=provider_id
    )
    _require_exact_keys(
        pricing,
        field="pricing",
        provider_id=provider_id,
        keys=frozenset(
            {
                "provenance",
                "measurement",
                "observed_at",
                "expiry",
                "price_usd",
                "routing_eligible",
            }
        ),
    )
    if not isinstance(pricing["provenance"], str) or not pricing["provenance"]:
        _fail(f"{provider_id}.pricing.provenance must be non-empty")
    if pricing["measurement"] not in _PRICING_MEASUREMENTS:
        _fail(f"{provider_id}.pricing.measurement is invalid")
    _require_iso_date(
        pricing["observed_at"], field="pricing.observed_at", provider_id=provider_id
    )
    if pricing["expiry"] is not None:
        _require_iso_date(
            pricing["expiry"], field="pricing.expiry", provider_id=provider_id
        )
    if pricing["price_usd"] is not None:
        if isinstance(pricing["price_usd"], bool) or not isinstance(
            pricing["price_usd"], (int, float)
        ):
            _fail(f"{provider_id}.pricing.price_usd must be numeric or null")
    if pricing["routing_eligible"] is not False:
        _fail(f"{provider_id}.pricing must never be routing eligible")

    return value


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _reject_duplicate_object_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Build a JSON object only when every key occurs once."""

    value = {}
    for key, item in pairs:
        if key in value:
            _fail(f"duplicate JSON object key: {key!r}")
        value[key] = item
    return value


def _reject_non_standard_json_constant(value: str) -> None:
    """Reject JSON extensions such as NaN and Infinity everywhere in the catalog."""

    _fail(f"non-standard JSON constant: {value!r}")


def _reject_non_finite_numbers(value: Any) -> None:
    """Reject float overflows recursively before validating catalog records."""

    if isinstance(value, float):
        if not math.isfinite(value):
            _fail("non-finite JSON number")
        return
    if isinstance(value, dict):
        for item in value.values():
            _reject_non_finite_numbers(item)
    elif isinstance(value, list):
        for item in value:
            _reject_non_finite_numbers(item)


def _parse_catalog(data: bytes) -> tuple[Mapping[str, Any], ...]:
    try:
        payload = json.loads(
            data,
            object_pairs_hook=_reject_duplicate_object_keys,
            parse_constant=_reject_non_standard_json_constant,
        )
    except (TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid provider catalog: malformed JSON") from exc
    if not isinstance(payload, list):
        _fail("top level must be a list")
    _reject_non_finite_numbers(payload)

    records = tuple(_validate_record(record) for record in payload)
    provider_ids = tuple(record["provider_id"] for record in records)
    if len(set(provider_ids)) != len(provider_ids):
        _fail("duplicate provider_id")
    if provider_ids != _PROVIDER_IDS:
        _fail("provider inventory must exactly match the v1 order")
    return tuple(_freeze(record) for record in records)


@lru_cache(maxsize=1)
def _catalog_records() -> tuple[Mapping[str, Any], ...]:
    return _parse_catalog(catalog_bytes())


def provider_ids() -> tuple[str, ...]:
    """Return the version-pinned provider IDs in their replay order."""

    return tuple(record["provider_id"] for record in _catalog_records())


def provider_record(provider_id: str) -> Mapping[str, Any]:
    """Return one immutable record; unknown providers fail closed."""

    normalized = str(provider_id or "").strip().lower()
    for record in _catalog_records():
        if record["provider_id"] == normalized:
            return record
    raise ValueError(f"Unsupported provider catalog entry: {provider_id!r}")


def all_catalog_records() -> tuple[Mapping[str, Any], ...]:
    """Return every immutable v1 catalog record in replay order."""

    return _catalog_records()
