"""OPai open-core edition boundaries (issue #38).

Codifies Free / Pro / Team / Enterprise boundaries as honest, local config -
not billing or SaaS. The active edition is self-declared (OPAI_EDITION env var
or per-project state); there is no license server in alpha. Free always
keeps meaningful local value and no paid feature requires telemetry.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .loader import hub_root, load_registry
from .state import load_state, save_state


EDITION_ORDER = ["free", "pro", "team", "team-governance", "enterprise"]

_FALLBACK: dict[str, Any] = {
    "default_edition": "free",
    "currency": "USD",
    "editions": {
        "free": {"label": "Free", "price": 0, "price_unit": "forever"},
        "pro": {"label": "Pro", "price": 12, "price_unit": "per month"},
        "team": {"label": "Team", "price": 19, "price_unit": "per user / month"},
        "team-governance": {
            "label": "Team Governance",
            "price": 29,
            "price_unit": "per user / month",
        },
        "enterprise": {
            "label": "Enterprise",
            "price": "custom",
            "price_unit": "contact",
        },
    },
    "features": [],
}


def load_editions(project_root: Path | None = None) -> dict[str, Any]:
    path = hub_root(project_root) / "editions.yaml"
    if not path.exists():
        return dict(_FALLBACK)
    try:
        data = load_registry(path)
    except Exception:
        return dict(_FALLBACK)
    if not isinstance(data, dict) or "editions" not in data:
        return dict(_FALLBACK)
    return data


def edition_rank(name: str) -> int:
    try:
        return EDITION_ORDER.index(str(name).lower())
    except ValueError:
        return 0


def current_edition(project_root: Path) -> str:
    """Resolve the active edition: env var > project state > config default."""
    env = os.environ.get("OPAI_EDITION")
    if env and env.lower() in EDITION_ORDER:
        return env.lower()
    state = load_state(project_root)
    declared = state.get("edition")
    if isinstance(declared, str) and declared.lower() in EDITION_ORDER:
        return declared.lower()
    return load_editions(project_root).get("default_edition", "free")


def set_edition(project_root: Path, name: str) -> dict[str, Any]:
    name = str(name).lower()
    if name not in EDITION_ORDER:
        return {
            "status": "error",
            "reason": f"unknown edition '{name}'",
            "editions": EDITION_ORDER,
        }
    root = project_root.expanduser().resolve()
    state = load_state(root)
    state["edition"] = name
    save_state(root, state)
    return {"status": "updated", "edition": name, **edition_summary(root)}


def feature_available(project_root: Path, feature_id: str) -> bool:
    catalog = load_editions(project_root)
    active = current_edition(project_root)
    for feature in catalog.get("features", []):
        if feature.get("id") == feature_id:
            return edition_rank(active) >= edition_rank(
                feature.get("min_edition", "free")
            )
    # Unknown features default to available (do not block on missing metadata).
    return True


def require_feature(project_root: Path, feature_id: str) -> dict[str, Any]:
    """Honest, self-attested gate. Returns availability plus an upsell note.

    There is no DRM in alpha; this surfaces the boundary so callers can
    decide whether to proceed or show an upgrade hint.
    """
    catalog = load_editions(project_root)
    active = current_edition(project_root)
    feature = next(
        (f for f in catalog.get("features", []) if f.get("id") == feature_id), None
    )
    if feature is None:
        return {"feature": feature_id, "available": True, "edition": active}
    min_edition = feature.get("min_edition", "free")
    available = edition_rank(active) >= edition_rank(min_edition)
    result = {
        "feature": feature_id,
        "available": available,
        "edition": active,
        "min_edition": min_edition,
        "description": feature.get("description", ""),
    }
    if not available:
        result["upgrade_hint"] = (
            f"'{feature_id}' is included from the {min_edition.title()} edition. "
            f"Set OPAI_EDITION={min_edition} or run 'opai edition set {min_edition}'."
        )
    return result


def edition_summary(project_root: Path) -> dict[str, Any]:
    catalog = load_editions(project_root)
    active = current_edition(project_root)
    editions = catalog.get("editions", {})
    features = catalog.get("features", [])

    included = [f["id"] for f in features if feature_available(project_root, f["id"])]
    locked = [
        {
            "id": f["id"],
            "min_edition": f.get("min_edition"),
            "description": f.get("description"),
        }
        for f in features
        if not feature_available(project_root, f["id"])
    ]

    catalog_view = []
    for name in EDITION_ORDER:
        meta = editions.get(name, {})
        catalog_view.append(
            {
                "id": name,
                "label": meta.get("label", name.title()),
                "price": meta.get("price"),
                "price_unit": meta.get("price_unit"),
                "price_annual": meta.get("price_annual"),
                "price_annual_unit": meta.get("price_annual_unit"),
                "tagline": meta.get("tagline", ""),
                "active": name == active,
                "features": [
                    f["id"]
                    for f in features
                    if edition_rank(name) >= edition_rank(f.get("min_edition", "free"))
                ],
            }
        )

    return {
        "edition": active,
        "source": "env"
        if os.environ.get("OPAI_EDITION")
        else ("project" if load_state(project_root).get("edition") else "default"),
        "currency": catalog.get("currency", "USD"),
        "included_features": included,
        "locked_features": locked,
        "catalog": catalog_view,
        "notes": [
            "Editions are self-declared in alpha; there is no license server.",
            "Free retains full local-first value; no paid feature requires telemetry.",
        ],
    }
