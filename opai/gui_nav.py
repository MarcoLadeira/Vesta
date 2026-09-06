"""Navigation model for the OPai desktop workspace.

Qt-free on purpose: the sidebars (web + Qt fallback) render these items and the
main area is a stack of views keyed by ``id``. Keeping the model here means the
information architecture is unit-tested without a display.

**Simple by default, powerful on demand.** A first-time user should read the
sidebar like ChatGPT: Chat, Prompts, your recent chats — done. The seven
data-backed dashboard views stay one click away inside a single collapsed
"Insights" group, and Settings lives as a quiet fixed control in the sidebar
footer (``hidden`` here so it never adds nav noise, while ``find_nav`` and the
command palette still resolve it).
"""

from __future__ import annotations

from typing import Any

# Each item: id (stable key + stack page), label, group (sidebar section header;
# "" renders without a header), and either kind="view" (a bespoke page) or
# kind="dashboard" with section= (a page rendered from a gui_view_model
# section). ``hidden`` items are routable (find_nav/palette) but not rendered
# in the nav list.
NAV_ITEMS: list[dict[str, Any]] = [
    {"id": "chat", "label": "Chat", "group": "", "kind": "view"},
    # Moved off the sidebar and into Settings -> Tools & insights. The sidebar
    # is for the conversation and the chats you have had; a library you open
    # occasionally and seven dashboards do not belong above your own history.
    # Still routable here, so the command palette and deep links keep working.
    {
        "id": "prompts",
        "label": "Prompt Library",
        "group": "",
        "kind": "view",
        "hidden": True,
    },
    {
        "id": "home",
        "label": "Money Saved",
        "group": "Insights",
        "kind": "dashboard",
        "section": "home",
        "hidden": True,
    },
    {
        "id": "firewall",
        "label": "Cost Firewall",
        "group": "Insights",
        "kind": "dashboard",
        "section": "firewall",
        "hidden": True,
    },
    {
        "id": "context",
        "label": "Context Waste",
        "group": "Insights",
        "kind": "dashboard",
        "section": "context",
        "hidden": True,
    },
    {
        "id": "benchmark",
        "label": "Benchmark",
        "group": "Insights",
        "kind": "dashboard",
        "section": "benchmark",
        "hidden": True,
    },
    {
        "id": "agents",
        "label": "Agents",
        "group": "Insights",
        "kind": "dashboard",
        "section": "agents",
        "hidden": True,
    },
    {
        "id": "proof",
        "label": "Proof Bundle",
        "group": "Insights",
        "kind": "dashboard",
        "section": "proof",
        "hidden": True,
    },
    {
        "id": "workflows",
        "label": "Workflows",
        "group": "Insights",
        "kind": "dashboard",
        "section": "workflows",
        "hidden": True,
    },
    # Settings renders as the fixed gear row in the sidebar footer, not as a
    # nav item — hidden keeps the list short while staying routable.
    {
        "id": "settings",
        "label": "Settings",
        "group": "",
        "kind": "view",
        "hidden": True,
    },
]

DEFAULT_VIEW = "chat"

# Groups that start folded: their pages are one click away without occupying
# ten rows of a first-time user's attention.
COLLAPSED_GROUPS = {"Insights"}


def nav_groups() -> list[tuple[str, list[dict[str, Any]]]]:
    """Visible nav items grouped by section header, in declared order."""
    groups: list[tuple[str, list[dict[str, Any]]]] = []
    for item in NAV_ITEMS:
        if item.get("hidden"):
            continue
        group = str(item.get("group") or "")
        if not groups or groups[-1][0] != group:
            groups.append((group, []))
        groups[-1][1].append(item)
    return groups


def group_collapsed(name: str) -> bool:
    """Whether a nav group starts folded in the sidebar."""
    return name in COLLAPSED_GROUPS


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
