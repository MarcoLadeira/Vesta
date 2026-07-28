"""Structural and safety validation for OPai's skill catalogue.

`opaihub validate` already gates the tools, agents, workflows, mcp_servers, and
models registries in CI. Skills were the one registry it did not cover: 36
`SKILL.md` packages shipped with no schema check, no frontmatter validation, no
orphan detection, and no safety rule. `opaihub skills doctor` checked only that
each file exists.

That mattered because a skill is not inert documentation — it is instruction
text a model acts on, and it can steer tool use and repository mutation. Two
failure shapes were completely unguarded:

*Silent deactivation.* Claude Code selects a skill from its frontmatter `name`
and `description`. If the frontmatter `name` drifts from the registry `id`, or a
description goes empty, the skill still "exists" and still passes `skills
doctor` — it simply never activates again, and nothing says so.

*Undeclared mutation.* A skill in a category that changes repository, database,
or system state needs to say where it stops. `gitops-pr` does this well ("Never
push, merge, delete branches, or deploy without explicit confirmation") and
`database-migration` does too ("write rollback instructions before applying").
Nothing required the next one to.

This module enforces both, plus the registry/disk consistency that is currently
true only by discipline. It is pure validation: it reads files, never writes,
executes, or interprets skill instructions.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

# Registry fields every skill entry must carry. `cost_policy` is OPai-specific
# and required rather than optional: the cost firewall is the product's core
# promise, so a skill that does not declare its tier is not shippable.
REQUIRED_REGISTRY_FIELDS = (
    "id",
    "name",
    "path",
    "category",
    "description",
    "cost_policy",
)

# Frontmatter keys a host needs to select the skill at all.
REQUIRED_FRONTMATTER_FIELDS = ("name", "description")

# Categories whose work changes state outside the conversation — repository,
# database, CI configuration, installed tooling, or a live browser session. A
# skill in one of these must state its own boundary, because the model reads the
# skill body as instruction and there is no other place that limit can live.
MUTATING_CATEGORIES = frozenset(
    {
        "gitops",
        "database",
        "release",
        "refactor",
        "dependency",
        "ci",
        "tools",
        "browser",
    }
)

# Phrases that count as a stated boundary. Deliberately a list of *forms* a
# limit takes rather than a single required sentence, so authors keep their own
# voice — what is enforced is that a limit exists, not its wording.
_BOUNDARY_PATTERNS = (
    r"\bnever\b",
    r"\bdo not\b",
    r"\bdon't\b",
    r"\bwithout explicit confirmation\b",
    r"\bask (?:first|before)\b",
    r"\bconfirm(?:ation)? (?:first|before)\b",
    r"\brollback\b",
    r"\breversible\b",
    r"\bdry[- ]run\b",
    r"\bread[- ]only\b",
)
_BOUNDARY = re.compile("|".join(_BOUNDARY_PATTERNS), re.IGNORECASE)

# A description short enough to be useless for activation, or long enough to
# suggest the body leaked into it.
MIN_DESCRIPTION_CHARS = 20
MAX_DESCRIPTION_CHARS = 400
# Below this a body is a stub, not a procedure.
MIN_BODY_CHARS = 40

_FRONTMATTER = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.DOTALL)


def _parse_frontmatter(text: str) -> tuple[dict[str, str] | None, str]:
    """Return ``(frontmatter, body)``; frontmatter is ``None`` when absent.

    Deliberately a minimal ``key: value`` reader rather than a YAML parser:
    skill frontmatter is a flat header, and refusing to evaluate anything richer
    keeps this validator from becoming a place where file content is executed.
    """
    match = _FRONTMATTER.match(text)
    if not match:
        return None, text
    fields: dict[str, str] = {}
    for line in match.group(1).splitlines():
        key, sep, value = line.partition(":")
        if sep:
            fields[key.strip()] = value.strip()
    return fields, text[match.end() :]


def _load_registry(hub: Path) -> tuple[list[dict[str, Any]], list[str]]:
    """Skill entries from the registry, plus any load-level problems."""
    path = hub / "skills" / "registry.yaml"
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        return [], [f"skills registry is unreadable: {exc.strerror or exc}"]
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        return [], [f"skills registry is not valid JSON: {exc}"]
    if not isinstance(data, dict):
        return [], ["skills registry must be an object"]
    entries = data.get("skills")
    if not isinstance(entries, list):
        return [], ["skills registry has no 'skills' list"]
    return [item for item in entries if isinstance(item, dict)], []


def _validate_entry(hub: Path, entry: dict[str, Any]) -> list[str]:
    """Every problem with one registry entry and the file it points at."""
    issues: list[str] = []
    skill_id = str(entry.get("id") or "").strip()
    label = skill_id or "<entry with no id>"

    missing = [
        f for f in REQUIRED_REGISTRY_FIELDS if not str(entry.get(f) or "").strip()
    ]
    if missing:
        issues.append(f"{label}: registry entry is missing {', '.join(missing)}")
    if not skill_id:
        # Without an id nothing else can be checked coherently.
        return issues

    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", skill_id):
        issues.append(
            f"{skill_id}: id must be lowercase kebab-case — hosts and install "
            "paths derive from it"
        )

    rel = str(entry.get("path") or "").strip()
    if not rel:
        return issues
    path = hub / rel
    if not path.is_file():
        issues.append(f"{skill_id}: registry path does not resolve to a file ({rel})")
        return issues
    if path.name != "SKILL.md":
        issues.append(
            f"{skill_id}: skill file must be named SKILL.md, found {path.name}"
        )

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        issues.append(f"{skill_id}: skill file is unreadable: {exc.strerror or exc}")
        return issues

    frontmatter, body = _parse_frontmatter(text)
    if frontmatter is None:
        issues.append(
            f"{skill_id}: SKILL.md has no --- frontmatter block, so no host can "
            "select it"
        )
        return issues

    for field in REQUIRED_FRONTMATTER_FIELDS:
        if not frontmatter.get(field):
            issues.append(f"{skill_id}: frontmatter is missing '{field}'")

    name = frontmatter.get("name", "")
    if name and name != skill_id:
        # The silent-deactivation case: the file still exists and still passes
        # an existence check, but the host can no longer match it.
        issues.append(
            f"{skill_id}: frontmatter name '{name}' does not match the registry "
            "id — the skill will not activate"
        )

    description = frontmatter.get("description", "")
    if description:
        if len(description) < MIN_DESCRIPTION_CHARS:
            issues.append(
                f"{skill_id}: description is too short to drive activation "
                f"({len(description)} < {MIN_DESCRIPTION_CHARS} chars)"
            )
        elif len(description) > MAX_DESCRIPTION_CHARS:
            issues.append(
                f"{skill_id}: description is {len(description)} chars; keep it "
                f"under {MAX_DESCRIPTION_CHARS} and put detail in the body"
            )

    stripped = body.strip()
    if len(stripped) < MIN_BODY_CHARS:
        issues.append(
            f"{skill_id}: body is {len(stripped)} chars — a skill needs a "
            "procedure, not a stub"
        )

    category = str(entry.get("category") or "").strip().lower()
    if category in MUTATING_CATEGORIES and not _BOUNDARY.search(stripped):
        issues.append(
            f"{skill_id}: category '{category}' changes state outside the "
            "conversation, so the body must state its boundary (what it will "
            "not do, or what it confirms/reverses first)"
        )
    return issues


def validate_skills(hub_root: Path) -> dict[str, Any]:
    """Validate the whole skill catalogue. Returns a registry-shaped report.

    Matches the shape `opaihub validate` already emits for the other registries
    (``registry``, ``count``, ``ok``, ``issues``) so it can join the existing CI
    gate without a new workflow step — this repository runs hosted CI manually
    to stay inside its Actions budget, so adding cost would not be free.
    """
    hub = Path(hub_root)
    entries, issues = _load_registry(hub)

    seen_ids: set[str] = set()
    seen_paths: set[str] = set()
    for entry in entries:
        skill_id = str(entry.get("id") or "").strip()
        if skill_id and skill_id in seen_ids:
            issues.append(f"{skill_id}: duplicate registry id")
        seen_ids.add(skill_id)
        rel = str(entry.get("path") or "").strip()
        if rel and rel in seen_paths:
            issues.append(f"{skill_id}: duplicate registry path {rel}")
        seen_paths.add(rel)
        issues.extend(_validate_entry(hub, entry))

    # Orphans: a directory shipped but never registered is invisible to every
    # surface that reads the registry, so it is dead weight that still looks
    # like a feature to anyone browsing the tree.
    skills_dir = hub / "skills"
    if skills_dir.is_dir():
        for child in sorted(skills_dir.iterdir()):
            if not child.is_dir() or child.name.startswith("."):
                continue
            if child.name not in seen_ids:
                issues.append(
                    f"{child.name}: directory exists but is not in the skills "
                    "registry, so nothing can discover it"
                )

    return {
        "registry": "skills",
        "count": len(entries),
        "ok": not issues,
        "issues": sorted(issues),
    }
