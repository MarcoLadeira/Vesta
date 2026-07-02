"""Task focus modes and output formats — the "tell the AI how to work" layer.

Two honest, real-behavior controls that shape the prompt actually sent to the
model (no mock):

* **Task focus** — a persona/intent (Build, Debug, Explain, Refactor, Test,
  Plan, Review) that prepends a short, neutral preface and *suggests* a run
  mode. The run mode (Ask/Plan/Safe Auto/Approve Edits/Full Auto) stays the
  authoritative safety control in the composer; focus never widens permissions,
  it only sets intent. A read-only focus (Explain/Plan/Review) maps to a
  read-only run mode so intent and safety agree.
* **Output format** — shapes the *form* of the answer (steps, table, code-only,
  bug report, JSON…). Implemented by appending one clear instruction line.

Both are Qt-free and unit-tested. ``compose_prompt`` is the single place the
preface + format instruction get attached, so the behavior is verifiable
without a display and identical across the composer and prompt library.
"""

from __future__ import annotations

import re
from typing import Any

# run_mode values must match opaihub.gui_preferences.MODES.
TASK_MODES: list[dict[str, str]] = [
    {
        "id": "general",
        "label": "General",
        "icon": "\U0001f4ac",
        "run_mode": "safe-auto",
        "desc": "No special framing — just your prompt.",
        "preface": "",
    },
    {
        "id": "build",
        "label": "Build",
        "icon": "\U0001f528",
        "run_mode": "safe-auto",
        "desc": "Implement a feature or change.",
        "preface": "Implement the requested change cleanly and idiomatically, matching the surrounding code.",
    },
    {
        "id": "debug",
        "label": "Debug",
        "icon": "\U0001f41e",
        "run_mode": "safe-auto",
        "desc": "Find and fix a bug.",
        "preface": "Diagnose the root cause first, then apply the smallest fix that addresses it.",
    },
    {
        "id": "explain",
        "label": "Explain",
        "icon": "\U0001f4d6",
        "run_mode": "ask",
        "desc": "Understand code. Read-only.",
        "preface": "Explain clearly and concisely. Do not modify any files.",
    },
    {
        "id": "refactor",
        "label": "Refactor",
        "icon": "♻",
        "run_mode": "approve-edits",
        "desc": "Improve structure; behavior unchanged.",
        "preface": "Preserve existing behavior exactly. Improve clarity and structure only, and explain each change.",
    },
    {
        "id": "test",
        "label": "Test",
        "icon": "\U0001f9ea",
        "run_mode": "safe-auto",
        "desc": "Write or run tests.",
        "preface": "Write focused, deterministic tests. Prefer the project's existing test style and runner.",
    },
    {
        "id": "plan",
        "label": "Plan",
        "icon": "\U0001f5fa",
        "run_mode": "plan",
        "desc": "Design before building. Read-only.",
        "preface": "Produce a concrete, step-by-step plan. Do not edit files yet.",
    },
    {
        "id": "review",
        "label": "Security review",
        "icon": "\U0001f50e",
        "run_mode": "ask",
        "desc": "Audit for risks. Read-only.",
        "preface": "Audit the code for security, correctness, and reliability risks. Do not modify files; report findings.",
    },
]

DEFAULT_TASK_MODE = "general"

OUTPUT_FORMATS: list[dict[str, str]] = [
    {"id": "normal", "label": "Normal", "instruction": ""},
    {
        "id": "steps",
        "label": "Step-by-step",
        "instruction": "Answer as a numbered, step-by-step guide.",
    },
    {
        "id": "code",
        "label": "Code only",
        "instruction": "Respond with code only and minimal prose.",
    },
    {
        "id": "table",
        "label": "Table",
        "instruction": "Present the answer as a Markdown table where it fits.",
    },
    {
        "id": "checklist",
        "label": "Checklist",
        "instruction": "Respond as a Markdown checklist of actionable items.",
    },
    {
        "id": "bug",
        "label": "Bug report",
        "instruction": "Format as a bug report with Summary, Steps to reproduce, Expected, Actual, and Suggested fix.",
    },
    {
        "id": "issue",
        "label": "Git issue",
        "instruction": "Format as a GitHub issue with a title line, context, acceptance criteria, and a task checklist.",
    },
    {
        "id": "json",
        "label": "JSON",
        "instruction": "Respond with a single valid JSON object and no surrounding prose.",
    },
    {
        "id": "markdown",
        "label": "Markdown",
        "instruction": "Respond in clean, well-structured Markdown with headings.",
    },
]

DEFAULT_OUTPUT_FORMAT = "normal"

_TASK_BY_ID = {m["id"]: m for m in TASK_MODES}
_FORMAT_BY_ID = {f["id"]: f for f in OUTPUT_FORMATS}


def task_mode(mode_id: str | None) -> dict[str, str]:
    return _TASK_BY_ID.get(str(mode_id or ""), _TASK_BY_ID[DEFAULT_TASK_MODE])


def output_format(format_id: str | None) -> dict[str, str]:
    return _FORMAT_BY_ID.get(str(format_id or ""), _FORMAT_BY_ID[DEFAULT_OUTPUT_FORMAT])


def run_mode_for_task(mode_id: str | None) -> str:
    """The run mode a task focus suggests (authoritative safety stays the picker)."""
    return task_mode(mode_id)["run_mode"]


def compose_prompt(
    prompt: str,
    *,
    task_mode_id: str | None = None,
    output_format_id: str | None = None,
) -> str:
    """Attach the task preface (top) and the format instruction (bottom).

    A plain ``normal`` format with the default ``build`` focus returns the
    prompt with only the build preface; passing ``None``/unknown ids degrades to
    safe defaults. The user's own text is never altered, only framed.
    """
    body = (prompt or "").strip()
    preface = task_mode(task_mode_id)["preface"].strip()
    instruction = output_format(output_format_id)["instruction"].strip()
    parts: list[str] = []
    if preface:
        parts.append(preface)
    if body:
        parts.append(body)
    if instruction:
        parts.append(instruction)
    return "\n\n".join(parts)


def task_modes() -> list[dict[str, str]]:
    return list(TASK_MODES)


def output_formats() -> list[dict[str, str]]:
    return list(OUTPUT_FORMATS)


_STEP_RE = re.compile(r"^\s{0,4}(?:(\d{1,2})[.)]\s+|[-*]\s+)(.+)$")


def parse_plan_steps(text: str) -> list[str]:
    """Extract actionable steps from a plan-mode answer (issue #130).

    Pure derivation from the model's real output — numbered lines (``1.`` /
    ``2)``) or bullets become steps; markdown emphasis is stripped; anything
    under two steps returns ``[]`` (prose isn't a plan). The GUI renders the
    result as an editable checklist; building re-prompts the real pipeline
    with the steps the user kept. Nothing is invented.
    """
    steps: list[str] = []
    for raw_line in str(text or "").splitlines():
        match = _STEP_RE.match(raw_line)
        if not match:
            continue
        body = match.group(2).strip()
        # Strip markdown bold/italic/code wrappers around the step title.
        body = re.sub(r"^[*_`]+|[*_`]+$", "", body).strip()
        if len(body) < 4:
            continue
        steps.append(body[:300])
    return steps if len(steps) >= 2 else []


def task_summary(mode_id: str | None, format_id: str | None) -> dict[str, Any]:
    """Compact display payload for the control panel / session inspector."""
    mode = task_mode(mode_id)
    fmt = output_format(format_id)
    return {
        "focus": mode["label"],
        "focus_desc": mode["desc"],
        "suggested_run_mode": mode["run_mode"],
        "format": fmt["label"],
        "read_only": mode["run_mode"] in {"ask", "plan"},
    }
