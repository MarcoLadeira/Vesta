"""Deterministic project context packs (power-efficiency roadmap Phase 3).

Instead of letting an agent dump the whole repo into a model, Vesta builds a
tiny, targeted pack: changed files with short redacted snippets, the tests most
likely to cover them, and cheap project markers - all under a character budget.
Read-only by default; ``write=True`` persists it for reuse.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .command_runner import redact
from .cost_model import estimate_tokens_for_chars, load_cost_model
from .evidence import MARKERS
from .state import state_dir
from .test_select import _git_changed_files, likely_tests_for


DEFAULT_CHAR_BUDGET = 6000
DEFAULT_MAX_FILES = 20
DEFAULT_HEAD_LINES = 20

# Vesta-managed instruction/ignore files are not useful code context and would
# only waste the pack's token budget, so they are excluded.
_MANAGED_FILES = {
    "AGENTS.md",
    "CLAUDE.md",
    "GEMINI.md",
    ".opaiignore",
    ".claudeignore",
    ".aiexclude",
    ".aiignore",
    ".codeiumignore",
}
_MANAGED_PREFIXES = (
    ".cursor/",
    ".clinerules",
    ".github/copilot-instructions",
    ".opaihub/",
    ".opcoding",
)


def _is_managed(path: str) -> bool:
    normalized = path.replace("\\", "/")
    name = normalized.rsplit("/", 1)[-1]
    if name in _MANAGED_FILES or normalized.startswith(_MANAGED_PREFIXES):
        return True
    # AI-client ignore files (.cursorignore, .geminiignore, .aiderignore, ...)
    # are tool config, not code context.
    return name.startswith(".") and name.endswith("ignore")


def _file_snippet(path: Path, head_lines: int) -> dict[str, Any]:
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    snippet = ""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            lines = []
            for index, line in enumerate(handle):
                if index >= head_lines:
                    break
                lines.append(line.rstrip("\n"))
        snippet = redact("\n".join(lines))
    except OSError:
        snippet = ""
    return {"size_bytes": size, "head": snippet}


def build_context_pack(
    project_root: Path,
    *,
    changed_only: bool = True,
    max_files: int = DEFAULT_MAX_FILES,
    head_lines: int = DEFAULT_HEAD_LINES,
    char_budget: int = DEFAULT_CHAR_BUDGET,
    write: bool = False,
) -> dict[str, Any]:
    root = project_root.expanduser().resolve()
    cost_model = load_cost_model(root)

    changed = [
        path
        for path in (_git_changed_files(root) if changed_only else [])
        if not _is_managed(path)
    ]
    markers = [marker for marker in MARKERS if (root / marker).exists()]

    files: list[dict[str, Any]] = []
    adjacent_tests: list[str] = []
    used_chars = 0
    truncated = False

    for source in changed[:max_files]:
        path = root / source
        if not path.is_file():
            continue
        entry = {"path": source, **_file_snippet(path, head_lines)}
        snippet_len = len(entry["head"])
        if used_chars + snippet_len > char_budget:
            # Keep the path but drop the body to stay under budget.
            entry["head"] = ""
            entry["omitted_for_budget"] = True
            truncated = True
        else:
            used_chars += snippet_len
        files.append(entry)
        for test in likely_tests_for(source, root):
            if test not in adjacent_tests:
                adjacent_tests.append(test)

    if len(changed) > max_files:
        truncated = True

    pack = {
        "report": "opai-context-pack",
        "project": str(root),
        "scope": "changed" if changed_only else "project",
        "markers": markers,
        "changed_file_count": len(changed),
        "files": files,
        "adjacent_tests": adjacent_tests,
        "char_budget": char_budget,
        "used_chars": used_chars,
        "estimated_tokens": estimate_tokens_for_chars(used_chars, cost_model),
        "truncated": truncated,
        "notes": [
            "Deterministic pack; no model was used to build it.",
            "Snippets are redacted for secrets before inclusion.",
            "Load this instead of whole files to keep model context small.",
        ],
    }

    if write:
        path = state_dir(root) / "context" / "pack.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(pack, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        pack["pack_path"] = str(path)
    return pack
