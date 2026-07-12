"""Free public-alpha availability policy.

The old self-declared edition ladder was an alpha planning artifact. It must
not control access while OPai launches fully free. This module keeps the
diagnostic API and legacy CLI command stable, but reports implementation
availability rather than a commercial entitlement.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from .loader import hub_root, load_registry


FREE_ALPHA_EDITION = "free"
FREE_ALPHA_LAUNCH = "free-public-alpha"

_FALLBACK: dict[str, Any] = {
    "schema_version": 2,
    "launch": FREE_ALPHA_LAUNCH,
    "currency": "USD",
    "editions": {
        FREE_ALPHA_EDITION: {
            "label": "Free Public Alpha",
            "price": 0,
            "price_unit": "alpha",
            "tagline": "Every implemented OPai alpha capability is free.",
        }
    },
    "features": [],
}


def _fallback() -> dict[str, Any]:
    return copy.deepcopy(_FALLBACK)


def load_editions(project_root: Path | None = None) -> dict[str, Any]:
    """Load the free-alpha availability catalog, falling back safely."""
    path = hub_root(project_root) / "editions.yaml"
    if not path.exists():
        return _fallback()
    try:
        data = load_registry(path)
    except Exception:
        return _fallback()
    if not isinstance(data, dict) or not isinstance(data.get("editions"), dict):
        return _fallback()
    return data


def _feature(catalog: dict[str, Any], feature_id: str) -> dict[str, Any] | None:
    features = catalog.get("features", [])
    if not isinstance(features, list):
        return None
    return next(
        (
            feature
            for feature in features
            if isinstance(feature, dict) and feature.get("id") == feature_id
        ),
        None,
    )


def _implemented(feature: dict[str, Any] | None) -> bool:
    return (
        feature is None or feature.get("availability", "implemented") == "implemented"
    )


def current_edition(project_root: Path) -> str:
    """Return the only launch edition; environment and state cannot gate alpha."""
    del project_root
    return FREE_ALPHA_EDITION


def set_edition(project_root: Path, name: str) -> dict[str, Any]:
    """Keep legacy selection calls harmless without persisting entitlement state."""
    del project_root
    return {
        "status": "free_alpha",
        "edition": FREE_ALPHA_EDITION,
        "requested_edition": str(name).lower(),
        "reason": "OPai public alpha has no paid editions or feature gates.",
    }


def feature_available(project_root: Path, feature_id: str) -> bool:
    """Return implementation availability, never a commercial access decision."""
    return _implemented(_feature(load_editions(project_root), feature_id))


def require_feature(project_root: Path, feature_id: str) -> dict[str, Any]:
    """Describe whether a capability is implemented without an upsell path."""
    feature = _feature(load_editions(project_root), feature_id)
    available = _implemented(feature)
    result = {
        "feature": feature_id,
        "available": available,
        "edition": FREE_ALPHA_EDITION,
        "availability": "free_alpha" if available else "planned",
        "description": feature.get("description", "") if feature else "",
    }
    if not available:
        result.update(
            {
                "status": "not_implemented",
                "reason": (
                    f"'{feature_id}' is planned but is not implemented in the "
                    "free public alpha yet."
                ),
            }
        )
    return result


def edition_summary(project_root: Path) -> dict[str, Any]:
    """Return a truthful free-alpha availability report for legacy diagnostics."""
    catalog = load_editions(project_root)
    features = [
        feature
        for feature in catalog.get("features", [])
        if isinstance(feature, dict) and isinstance(feature.get("id"), str)
    ]
    included = [feature["id"] for feature in features if _implemented(feature)]
    planned = [
        {"id": feature["id"], "description": feature.get("description", "")}
        for feature in features
        if not _implemented(feature)
    ]
    free = catalog.get("editions", {}).get(FREE_ALPHA_EDITION, {})
    if not isinstance(free, dict):
        free = {}
    catalog_view = {
        "id": FREE_ALPHA_EDITION,
        "label": free.get("label", "Free Public Alpha"),
        "price": 0,
        "price_unit": free.get("price_unit", "alpha"),
        "tagline": free.get("tagline", "Every implemented alpha capability is free."),
        "active": True,
        "features": included,
    }

    return {
        "edition": FREE_ALPHA_EDITION,
        "launch": catalog.get("launch", FREE_ALPHA_LAUNCH),
        "source": "free_alpha_policy",
        "currency": catalog.get("currency", "USD"),
        "included_features": included,
        "planned_features": planned,
        "catalog": [catalog_view],
        "notes": [
            "Every implemented OPai alpha capability is free.",
            "Planned capabilities are not payment tiers and receive no upgrade prompt.",
        ],
    }
