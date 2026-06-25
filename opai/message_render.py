"""Render OPai chat answers as calm, readable rich text.

Account models (Claude, Codex) reply in Markdown, but the desktop chat used to
drop that text into a plain ``QLabel`` - so ``**bold**``, ``# headings``,
``- bullets`` and fenced ``code`` blocks showed up as raw punctuation. This
module turns that Markdown into the small, safe subset of HTML that Qt rich
text understands, styled to match the app theme.

It is deliberately Qt-free and dependency-free so it can be unit-tested without
a display, and so the headless paths never import PySide. The desktop GUI calls
:func:`render_message_html` and hands the result to a rich-text ``QLabel``.
"""

from __future__ import annotations

import html
import re
from typing import Any

# Theme colours the renderer needs. Defaults match opai.gui_desktop so the
# module reads well on its own; the GUI passes its live palette in.
DEFAULT_COLORS: dict[str, str] = {
    "ink": "#e8eaee",
    "muted": "#9aa2af",
    "accent": "#34d399",
    "link": "#7cc0ff",
    "code_bg": "#15171b",
    "code_ink": "#e8eaee",
    "border": "#2c3036",
}

_MONO = '"Cascadia Code","JetBrains Mono",Consolas,monospace'

# Inline spans, applied after HTML-escaping so the angle brackets we emit are
# the only markup in the string.
_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_ITALIC_RE = re.compile(r"(?<![\*\w])\*(?!\s)(.+?)(?<!\s)\*(?!\*)")
_USCORE_ITALIC_RE = re.compile(r"(?<![_\w])_(?!\s)(.+?)(?<!\s)_(?![_\w])")


def _inline(text: str, colors: dict[str, str]) -> str:
    """Apply inline Markdown (code, links, bold, italic) to an escaped line."""
    # Pull inline `code` out first so its contents are never re-parsed.
    spans: list[str] = []

    def _stash_code(match: re.Match[str]) -> str:
        spans.append(
            f'<code style="background:{colors["code_bg"]};color:{colors["code_ink"]};'
            f'font-family:{_MONO};padding:1px 4px;border-radius:4px;">'
            f"{match.group(1)}</code>"
        )
        return f"\x00{len(spans) - 1}\x00"

    text = re.sub(r"`([^`]+)`", _stash_code, text)
    text = _LINK_RE.sub(
        rf'<a href="\2" style="color:{colors["link"]};text-decoration:none;">\1</a>',
        text,
    )
    text = _BOLD_RE.sub(r"<b>\1</b>", text)
    text = _ITALIC_RE.sub(r"<i>\1</i>", text)
    text = _USCORE_ITALIC_RE.sub(r"<i>\1</i>", text)

    def _restore(match: re.Match[str]) -> str:
        return spans[int(match.group(1))]

    return re.sub(r"\x00(\d+)\x00", _restore, text)


def _code_block(lines: list[str], colors: dict[str, str]) -> str:
    body = html.escape("\n".join(lines))
    return (
        f'<pre style="background:{colors["code_bg"]};color:{colors["code_ink"]};'
        f"font-family:{_MONO};font-size:12.5px;padding:11px 13px;"
        f'border:1px solid {colors["border"]};border-radius:10px;'
        f'white-space:pre-wrap;margin:8px 0;">{body}</pre>'
    )


def render_message_html(text: Any, colors: dict[str, str] | None = None) -> str:
    """Convert a Markdown-ish answer into themed rich-text HTML for Qt.

    Supports headings, bullet/numbered lists, fenced and inline code, bold,
    italics, links and blockquotes. Anything it does not recognise survives as
    plain (escaped) text, so a normal sentence always renders as itself.
    """
    palette = {**DEFAULT_COLORS, **(colors or {})}
    raw = "" if text is None else str(text)
    out: list[str] = []
    list_stack: list[str] = []  # "ul" / "ol" currently open

    def close_lists() -> None:
        while list_stack:
            out.append(f"</{list_stack.pop()}>")

    lines = raw.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Fenced code block.
        fence = re.match(r"^\s*```", line)
        if fence:
            close_lists()
            block: list[str] = []
            i += 1
            while i < len(lines) and not re.match(r"^\s*```", lines[i]):
                block.append(lines[i])
                i += 1
            i += 1  # skip closing fence
            out.append(_code_block(block, palette))
            continue

        if not stripped:
            close_lists()
            i += 1
            continue

        # Headings (#, ##, ###...).
        heading = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if heading:
            close_lists()
            level = len(heading.group(1))
            size = {1: 19, 2: 17, 3: 15}.get(level, 14)
            content = _inline(html.escape(heading.group(2)), palette)
            out.append(
                f'<div style="font-size:{size}px;font-weight:700;'
                f'color:{palette["ink"]};margin:10px 0 4px 0;">{content}</div>'
            )
            i += 1
            continue

        # Horizontal rule.
        if re.match(r"^(\*\s*){3,}$|^(-\s*){3,}$|^(_\s*){3,}$", stripped):
            close_lists()
            out.append(
                f'<div style="border-top:1px solid {palette["border"]};'
                'margin:10px 0;"></div>'
            )
            i += 1
            continue

        # Blockquote.
        if stripped.startswith(">"):
            close_lists()
            content = _inline(html.escape(stripped[1:].strip()), palette)
            out.append(
                f'<div style="border-left:3px solid {palette["border"]};'
                f'color:{palette["muted"]};padding:2px 0 2px 12px;margin:6px 0;">'
                f"{content}</div>"
            )
            i += 1
            continue

        # Ordered list item.
        ordered = re.match(r"^(\d+)[.)]\s+(.*)$", stripped)
        if ordered:
            if list_stack[-1:] != ["ol"]:
                close_lists()
                out.append(
                    f'<ol style="margin:6px 0 6px 0;padding-left:22px;'
                    f'color:{palette["ink"]};">'
                )
                list_stack.append("ol")
            out.append(f"<li style='margin:3px 0;'>{_inline(html.escape(ordered.group(2)), palette)}</li>")
            i += 1
            continue

        # Unordered list item.
        bullet = re.match(r"^[-*+]\s+(.*)$", stripped)
        if bullet:
            if list_stack[-1:] != ["ul"]:
                close_lists()
                out.append(
                    f'<ul style="margin:6px 0 6px 0;padding-left:20px;'
                    f'color:{palette["ink"]};">'
                )
                list_stack.append("ul")
            out.append(f"<li style='margin:3px 0;'>{_inline(html.escape(bullet.group(1)), palette)}</li>")
            i += 1
            continue

        # Plain paragraph line. Group consecutive non-blank, non-structural
        # lines into one paragraph with soft line breaks.
        close_lists()
        para: list[str] = []
        while i < len(lines):
            nxt = lines[i]
            s = nxt.strip()
            if (
                not s
                or re.match(r"^\s*```", nxt)
                or re.match(r"^#{1,6}\s+", s)
                or re.match(r"^[-*+]\s+", s)
                or re.match(r"^\d+[.)]\s+", s)
                or s.startswith(">")
            ):
                break
            para.append(_inline(html.escape(s), palette))
            i += 1
        out.append(
            f'<div style="margin:4px 0;line-height:1.5;color:{palette["ink"]};">'
            + "<br>".join(para)
            + "</div>"
        )

    close_lists()
    return "".join(out).strip()
