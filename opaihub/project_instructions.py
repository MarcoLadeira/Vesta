"""Durable project instructions, as a prompt layer every model receives.

The consistency defect this fixes is subtle and large: Vesta's *account* models
run through their vendor CLIs (`claude`, `codex`, `copilot`), and those CLIs
read the repository's own instruction files themselves. Vesta's **local and
free-tier** models go through `opaihub.ask`, which built a prompt from
languages, markers, test commands, and git status — and `context_pack`
deliberately excludes `AGENTS.md` / `CLAUDE.md` as "not useful code context".

So the same request obeyed the project's rules or ignored them entirely
depending on which model happened to pick it up. A user who wrote "always run
the tests before claiming done" in `AGENTS.md` got that honoured by Claude and
silently ignored by Gemini. That is message-shape variance with a completely
invisible cause, and no amount of rephrasing fixes it.

Large language models retain nothing between completions, so durable
instructions have to be *re-supplied every turn*. This module loads them once
per turn, bounded and deterministic, so every model family starts from the same
contract.

Deliberate boundaries:

* **Project files only.** A user's global `~/.claude/CLAUDE.md` is personal
  configuration that can mention unrelated work; the repository's own files are
  what the request is actually about. Global files are never read here.
* **Bounded.** Instructions are truncated to a character budget on a line
  boundary, and the truncation is stated rather than hidden — a silently
  half-applied rule is worse than a visibly clipped one.
* **Deterministic.** Fixed filename order, no globbing, no clock. The same
  repository always produces the same layer, so it can be asserted on.
* **Text only.** These are checked-in instruction files, read as text and
  never executed or interpreted as commands by this module.
"""

from __future__ import annotations

from pathlib import Path

# Checked in order; every present file contributes, most specific last so it
# reads as the final word. These are the instruction filenames the major coding
# agents already standardise on, which is why a user has probably written one.
INSTRUCTION_FILES = ("AGENTS.md", "CLAUDE.md", "GEMINI.md", ".opai/rules.md")

# Enough for a real set of house rules, small enough that it cannot crowd out
# the actual request on a modest local context window.
DEFAULT_CHAR_BUDGET = 4000

# A single file cannot consume the whole budget and starve the others.
_PER_FILE_FRACTION = 0.6


def _clip(text: str, budget: int) -> tuple[str, bool]:
    """Truncate on a line boundary. Returns ``(text, was_truncated)``."""
    if len(text) <= budget:
        return text, False
    head = text[:budget]
    cut = head.rfind("\n")
    if cut > budget // 2:
        head = head[:cut]
    return head.rstrip(), True


def load_project_instructions(
    project_root: Path, *, char_budget: int = DEFAULT_CHAR_BUDGET
) -> str:
    """The repository's durable instructions, ready to prepend to a prompt.

    Returns ``""`` when the project has none — the common case for a fresh
    repository, and never a fabricated placeholder.
    """
    root = Path(project_root).expanduser()
    per_file = max(200, int(char_budget * _PER_FILE_FRACTION))
    sections: list[str] = []
    remaining = char_budget
    for name in INSTRUCTION_FILES:
        if remaining <= 0:
            break
        path = root / name
        try:
            if not path.is_file():
                continue
            raw = path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            # An unreadable instruction file must not break the turn; the run
            # continues without it rather than failing on configuration.
            continue
        if not raw:
            continue
        body, truncated = _clip(raw, min(per_file, remaining))
        if not body:
            continue
        header = f"--- {name} ---"
        if truncated:
            header += " (truncated)"
        sections.append(f"{header}\n{body}")
        remaining -= len(body) + len(header)
    return "\n\n".join(sections)


def build_system_prompt(
    base: str, project_root: Path, *, char_budget: int = DEFAULT_CHAR_BUDGET
) -> str:
    """``base`` plus the project's instruction layer, when it has one.

    The instructions go *after* Vesta's own behavioural rules and before the
    turn's content: Vesta's safety and honesty rules are not the project's to
    override, but within them the project's house rules are authoritative.
    """
    instructions = load_project_instructions(project_root, char_budget=char_budget)
    if not instructions:
        return base
    return (
        f"{base}\n\n"
        "The user's project defines the following standing instructions. They "
        "apply to every request in this repository and override your general "
        "defaults, except where they conflict with the rules above.\n\n"
        f"{instructions}"
    )
