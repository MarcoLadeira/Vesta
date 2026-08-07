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

This module is also the single source of truth for what the mode controls
*mean* together (F20 — Run mode / Task focus / Agent mode must not silently
disagree across surfaces):

* ``describe_controls(run_mode, focus)`` — one dict summarizing the effective
  behavior (edit/command capability, read-only, live agent-mode preview) that
  the web GUI, the classic GUI, and tests all consume.
* ``plan_mode_selection(selected_mode, prefs)`` — the Qt-free decision behind
  the composer's Full Auto pin flow, so the classic GUI mirrors the web
  contract (#137) exactly.
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
        "preface": "Implement the requested change cleanly and idiomatically, matching the surrounding code. This task focus is advisory; the current explicit user request controls permissions.",
    },
    {
        "id": "debug",
        "label": "Debug",
        "icon": "\U0001f41e",
        "run_mode": "safe-auto",
        "desc": "Find and fix a bug.",
        "preface": "Advisory focus: diagnose the root cause first, then apply the smallest fix that addresses it. The current explicit user request controls permissions.",
    },
    {
        "id": "explain",
        "label": "Explain",
        "icon": "\U0001f4d6",
        "run_mode": "ask",
        "desc": "Understand code. Read-only.",
        "preface": "Advisory focus: explain clearly and concisely. A later explicit user request controls whether files may be modified.",
    },
    {
        "id": "refactor",
        "label": "Refactor",
        "icon": "♻",
        "run_mode": "approve-edits",
        "desc": "Improve structure; behavior unchanged.",
        "preface": "Advisory focus: preserve existing behavior exactly, improve clarity and structure, and explain each change. The current explicit user request controls permissions.",
    },
    {
        "id": "test",
        "label": "Test",
        "icon": "\U0001f9ea",
        "run_mode": "safe-auto",
        "desc": "Write or run tests.",
        "preface": "Advisory focus: write focused, deterministic tests using the project's existing style. The current explicit user request controls permissions.",
    },
    {
        "id": "plan",
        "label": "Plan",
        "icon": "\U0001f5fa",
        "run_mode": "plan",
        "desc": "Design before building. Read-only.",
        "preface": "Advisory focus: produce a concrete, step-by-step plan. A later explicit implementation request supersedes this planning hint.",
    },
    {
        "id": "review",
        "label": "Security review",
        "icon": "\U0001f50e",
        "run_mode": "ask",
        "desc": "Audit for risks. Read-only.",
        "preface": "Advisory focus: audit for security, correctness, and reliability risks and report findings. A later explicit user request controls whether files may be modified.",
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


def describe_controls(run_mode: str | None, focus: str | None) -> dict[str, Any]:
    """One honest summary of what the current Run mode + Task focus mean (F20).

    Three concepts overlap in the UI — the *Run mode* (safety authority), the
    *Task focus* (intent persona), and the derived *Agent mode* (per-message
    capability class) — and each surface used to recompute its own reading of
    them, so they could silently disagree (e.g. Full Auto + an Explain focus
    is read-only in effect). This is the single definition the web GUI, the
    classic GUI, and the tests all consume.

    ``agent_mode_preview`` is derived live with the same resolver the pipeline
    uses per message (``opaihub.agent_policy.resolve_agent_policy``) called
    with an empty message and the current focus as the hint: it is the agent
    mode the *next* run falls back to when the message carries no explicit
    action/read-only signal. A real message can still override it — the
    latest explicit request always wins — so this is a preview, never a gate.

    ``read_only`` is the *effective* read-only state: either a read-only run
    mode (Ask/Plan) or a read-only focus (Explain/Plan/Review) makes the next
    run read-only in practice, regardless of what the other control says.
    """
    from opai.gui_permissions import is_read_only, permissions_for
    from opaihub.agent_policy import resolve_agent_policy

    mode = str(run_mode or "").strip() or "safe-auto"
    focus_id = str(focus or "").strip() or DEFAULT_TASK_MODE
    focus_mode = task_mode(focus_id)
    states = {row["id"]: row["state"] for row in permissions_for(mode)}
    edit_state = states.get("edit", "block")
    run_any_state = states.get("run_any", "block")
    # A mode is run-mode-read-only when it is a known read-only mode OR when
    # its permission rules block edits outright — unknown modes degrade to the
    # read-only "ask" ruleset and must report read-only too (fail closed).
    run_mode_read_only = is_read_only(mode) or edit_state == "block"
    focus_read_only = focus_mode["run_mode"] in {"ask", "plan"}
    preview = resolve_agent_policy("", focus_hint=focus_id, run_mode_hint=mode)
    return {
        "run_mode": mode,
        "focus": focus_id,
        "focus_label": focus_mode["label"],
        "suggested_run_mode": focus_mode["run_mode"],
        "read_only": run_mode_read_only or focus_read_only,
        "run_mode_read_only": run_mode_read_only,
        "focus_read_only": focus_read_only,
        "can_edit": edit_state in {"allow", "ask"},
        "can_run_commands": run_any_state in {"allow", "ask"},
        "edit_state": edit_state,
        "run_any_state": run_any_state,
        "agent_mode_preview": preview.mode.value,
        "agent_mode_label": preview.mode.value.title(),
    }


def plan_mode_selection(selected_mode: str, prefs: dict[str, Any]) -> dict[str, Any]:
    """Decide what picking a run mode in the composer must do (F16, #137).

    Qt-free so both GUIs share one rule and the classic pin flow is testable
    without a display. Selecting Full Auto while it is not pinned must NOT
    persist a bare full-auto default (the save would be silently downgraded to
    Safe Auto while the combo still shows Full Auto — the original F16 lie):
    the caller must first run the explicit pin acknowledgement and only then
    persist via ``opaihub.gui_preferences.pin_full_auto``. Any other selection
    (or Full Auto while already pinned) persists directly.
    """
    from opaihub.autonomy import is_full_auto_pinned, resolve_startup_mode

    mode = str(selected_mode or "").strip() or "safe-auto"
    pinned = is_full_auto_pinned(prefs)
    if mode == "full-auto" and not pinned:
        return {
            "action": "confirm_pin",
            "selected_mode": mode,
            "needs_pin_confirmation": True,
            "pinned": False,
            # Where the combo must revert to when the pin is declined: the
            # effective mode for the current preferences — never the stale
            # selection.
            "effective_mode": resolve_startup_mode(prefs).effective_mode,
        }
    return {
        "action": "persist",
        "selected_mode": mode,
        "needs_pin_confirmation": False,
        "pinned": pinned,
        "effective_mode": mode,
    }
