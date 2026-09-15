"""Prompt library — curated, safe starting prompts grouped by category.

Static, secret-free templates the user can drop into the composer. Each prompt
carries a recommended task focus (see ``gui_modes``) so selecting it sets a
sensible intent. Qt-free and unit-tested; filtering powers the library page and
the command palette.
"""

from __future__ import annotations

from typing import Any

CATEGORIES = [
    "Coding",
    "Debugging",
    "Testing",
    "Refactor",
    "UX/UI",
    "Product",
    "Security",
    "Research",
    "Business",
]

PROMPTS: list[dict[str, Any]] = [
    {
        "id": "explain_repo",
        "title": "Explain this repo",
        "category": "Coding",
        "desc": "High-level tour of the codebase.",
        "template": "Give me a high-level tour of this codebase: its purpose, main modules, and how they fit together.",
        "mode": "explain",
        "tags": ["onboarding", "overview"],
    },
    {
        "id": "implement_feature",
        "title": "Implement a feature",
        "category": "Coding",
        "desc": "Build a described feature end to end.",
        "template": "Implement the following feature, matching the existing code style and adding tests:\n\n<describe the feature>",
        "mode": "build",
        "tags": ["feature"],
    },
    {
        "id": "find_bug",
        "title": "Find a likely bug",
        "category": "Debugging",
        "desc": "Hunt for a probable defect in recent work.",
        "template": "Review my recent changes and look for a likely bug. Explain the root cause before suggesting a fix.",
        "mode": "debug",
        "tags": ["bug"],
    },
    {
        "id": "explain_error",
        "title": "Explain this error",
        "category": "Debugging",
        "desc": "Decode a stack trace or error message.",
        "template": "Explain this error and the most likely causes, then suggest a fix:\n\n<paste the error / stack trace>",
        "mode": "debug",
        "tags": ["error", "stacktrace"],
    },
    {
        "id": "write_tests",
        "title": "Write tests for a file",
        "category": "Testing",
        "desc": "Add focused unit tests.",
        "template": "Write focused unit tests for <file/function>, covering the main paths and edge cases, in this project's existing test style.",
        "mode": "test",
        "tags": ["unit", "coverage"],
    },
    {
        "id": "summarize_changes",
        "title": "Summarize my changes",
        "category": "Coding",
        "desc": "Describe uncommitted work.",
        "template": "Summarize my uncommitted changes and what they accomplish, as if writing a commit message.",
        "mode": "explain",
        "tags": ["git", "commit"],
    },
    {
        "id": "refactor_module",
        "title": "Refactor for clarity",
        "category": "Refactor",
        "desc": "Improve structure without changing behavior.",
        "template": "Refactor <file/module> for clarity and structure. Preserve behavior exactly and explain each change.",
        "mode": "refactor",
        "tags": ["cleanup"],
    },
    {
        "id": "plan_feature",
        "title": "Plan before building",
        "category": "Product",
        "desc": "Step-by-step implementation plan.",
        "template": "Produce a concrete, step-by-step plan to build <feature>, including files to touch and risks. Do not write code yet.",
        "mode": "plan",
        "tags": ["plan", "design"],
    },
    {
        "id": "security_audit",
        "title": "Security review",
        "category": "Security",
        "desc": "Audit for risks, read-only.",
        "template": "Audit <file/area> for security and correctness risks (injection, secrets, auth, unsafe calls). Report findings; do not modify files.",
        "mode": "review",
        "tags": ["audit", "risk"],
    },
    {
        "id": "improve_ux",
        "title": "Critique the UX",
        "category": "UX/UI",
        "desc": "Find usability and clarity problems.",
        "template": "Review <screen/flow> for usability, clarity, and accessibility problems. Prioritize the top issues with concrete fixes.",
        "mode": "review",
        "tags": ["usability", "a11y"],
    },
    {
        "id": "research_options",
        "title": "Compare approaches",
        "category": "Research",
        "desc": "Weigh trade-offs between options.",
        "template": "Compare approaches for <problem>, with trade-offs, and recommend one with reasoning. Do not edit files.",
        "mode": "explain",
        "tags": ["compare", "tradeoffs"],
    },
    {
        "id": "release_notes",
        "title": "Draft release notes",
        "category": "Business",
        "desc": "Turn changes into user-facing notes.",
        "template": "Draft concise, user-facing release notes from my recent changes, grouped by Added / Changed / Fixed.",
        "mode": "explain",
        "tags": ["release", "changelog"],
    },
]


def categories_present() -> list[str]:
    """Categories that actually have at least one prompt, in CATEGORIES order."""
    have = {p["category"] for p in PROMPTS}
    return [c for c in CATEGORIES if c in have]


def filter_prompts(
    query: str = "", category: str | None = None
) -> list[dict[str, Any]]:
    """AND-match ``query`` tokens across title/desc/tags, optionally within a category."""
    q = (query or "").strip().lower()
    tokens = q.split()
    out: list[dict[str, Any]] = []
    for prompt in PROMPTS:
        if category and prompt["category"] != category:
            continue
        hay = " ".join(
            [
                prompt["title"],
                prompt["desc"],
                prompt["category"],
                " ".join(prompt.get("tags", [])),
            ]
        ).lower()
        if all(tok in hay for tok in tokens):
            out.append(prompt)
    return out


def find_prompt(prompt_id: str) -> dict[str, Any] | None:
    for prompt in PROMPTS:
        if prompt["id"] == prompt_id:
            return prompt
    return None
