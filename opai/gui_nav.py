"""Navigation model for the OPai premium desktop workspace.

Qt-free on purpose: the sidebar in ``gui_desktop.py`` renders these items and the
main area is a stack of views keyed by ``id``. Keeping the model here means the
information architecture is unit-tested without a display, and adding a new
workspace view is a data change, not a surgery on the window class.

The IA mirrors the prompt's required zones: a primary workspace (Chat), the
data-backed dashboard views that already exist in ``gui_view_model`` but were
never surfaced, a prompt library, and settings — grouped so the sidebar reads as
sections rather than a flat list.
"""

from __future__ import annotations

from typing import Any

# Each item: id (stable key + stack page), label, group (sidebar section header),
# and either kind="view" (a bespoke page) or kind="dashboard" with section= (a
# page rendered from a gui_view_model section). Nav is text-only on purpose:
# glyph icons render inconsistently across fonts/platforms, and a clean labelled
# list reads more premium (and less childish) than coloured emoji.
NAV_ITEMS: list[dict[str, Any]] = [
    {"id": "chat", "label": "Chat", "group": "Workspace", "kind": "view"},
    {"id": "prompts", "label": "Prompt Library", "group": "Workspace", "kind": "view"},
    {
        "id": "home",
        "label": "Money Saved",
        "group": "Dashboard",
        "kind": "dashboard",
        "section": "home",
    },
    {
        "id": "firewall",
        "label": "Cost Firewall",
        "group": "Dashboard",
        "kind": "dashboard",
        "section": "firewall",
    },
    {
        "id": "context",
        "label": "Context Waste",
        "group": "Dashboard",
        "kind": "dashboard",
        "section": "context",
    },
    {
        "id": "benchmark",
        "label": "Benchmark",
        "group": "Dashboard",
        "kind": "dashboard",
        "section": "benchmark",
    },
    {
        "id": "agents",
        "label": "Agents",
        "group": "Dashboard",
        "kind": "dashboard",
        "section": "agents",
    },
    {
        "id": "proof",
        "label": "Proof Bundle",
        "group": "Dashboard",
        "kind": "dashboard",
        "section": "proof",
    },
    {
        "id": "workflows",
        "label": "Workflows",
        "group": "Dashboard",
        "kind": "dashboard",
        "section": "workflows",
    },
    {"id": "settings", "label": "Settings", "group": "System", "kind": "view"},
]

DEFAULT_VIEW = "chat"


def nav_groups() -> list[tuple[str, list[dict[str, Any]]]]:
    """Return nav items grouped by section header, in declared order."""
    groups: list[tuple[str, list[dict[str, Any]]]] = []
    for item in NAV_ITEMS:
        group = str(item.get("group") or "")
        if not groups or groups[-1][0] != group:
            groups.append((group, []))
        groups[-1][1].append(item)
    return groups


def nav_ids() -> list[str]:
    return [item["id"] for item in NAV_ITEMS]


def find_nav(item_id: str) -> dict[str, Any] | None:
    for item in NAV_ITEMS:
        if item["id"] == item_id:
            return item
    return None


def dashboard_sections() -> list[str]:
    """The gui_view_model section ids that the dashboard nav surfaces."""
    return [item["section"] for item in NAV_ITEMS if item.get("kind") == "dashboard"]
