"""Post-edit structural verification for OPai Build apps (#276).

After ``opai build`` applies model-proposed files, this proves the app still
stands — deterministically, offline, in milliseconds. It is honest about what
it checks: *structure*, not behavior. The checks target the real failure
modes of full-file regeneration:

- **Truncated output** (the classic one): a model answer cut off mid-file
  leaves unbalanced braces/brackets/parens. The balance scanner is string- and
  comment-aware so braces inside literals never false-positive.
- **Broken wiring**: the entrypoint must exist, carry a doctype, and every
  local asset it references (``<script src>``, ``<link href>``, ``<img src>``)
  must exist on disk.
- **Invalid data**: ``.json`` files must parse.

``verify_app`` returns per-check results; the build loop surfaces them and,
in ``--strict`` mode, rolls the edit back from the backups when verification
fails. No browser, no network, no node — hermetic by construction.
"""

from __future__ import annotations

import json
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from .boundary_errors import safe_detail

_PAIRS = {"{": "}", "(": ")", "[": "]"}
_CLOSERS = {v: k for k, v in _PAIRS.items()}

# Chars after which a JS "/" starts a regex literal, not division — the
# standard quick-lexer heuristic (value position vs operand position).
_REGEX_PRECEDERS = set("([{,;=:!&|?+-*%<>~^")


def scan_balance(text: str, *, language: str = "js") -> dict[str, Any]:
    """Brace/bracket/paren balance outside strings, comments, and regexes.

    Understands ``//`` and ``/* */`` comments, single/double/backtick strings
    with escapes, and JS regex literals (``.replace(/[&<>"']/g, …)`` must not
    read as an unterminated string). CSS: block comments and quotes only. An
    unbalanced result on a full file almost always means truncated or mangled
    generation.
    """
    stack: list[str] = []
    state = ""  # "", "'", '"', "`", "line", "block", "regex"
    prev = ""
    last_sig = ""  # last significant code char — regex/division disambiguation
    pending_slash = False
    in_class = False  # inside [...] within a regex literal
    escaped = False
    for ch in text:
        if state in {"'", '"', "`"}:
            if not escaped and ch == state:
                state = ""
            escaped = (ch == "\\") and not escaped
            continue
        if state == "line":
            if ch == "\n":
                state = ""
            continue
        if state == "block":
            if prev == "*" and ch == "/":
                state = ""
                prev = ""
                continue
            prev = ch
            continue
        if state == "regex":
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == "[":
                in_class = True
            elif ch == "]":
                in_class = False
            elif ch == "/" and not in_class:
                state = ""
                last_sig = "/"
            elif ch == "\n":
                return {"ok": False, "detail": "unterminated regex literal"}
            continue
        # code state
        if pending_slash:
            pending_slash = False
            if ch == "/":
                state = "line"
                prev = ""
                continue
            if ch == "*":
                state = "block"
                prev = ""
                continue
            if last_sig == "" or last_sig in _REGEX_PRECEDERS:
                # The slash opened a regex literal; ch is its first character.
                state = "regex"
                in_class = ch == "["
                escaped = ch == "\\"
                continue
            last_sig = "/"  # it was division — fall through to process ch
        if language == "js" and ch == "/":
            pending_slash = True
            prev = ch
            continue
        if ch in {"'", '"'} or (ch == "`" and language == "js"):
            state = ch
            escaped = False
        elif prev == "/" and ch == "*":  # CSS comment open (JS goes via pending_slash)
            state = "block"
            prev = ""
            continue
        elif ch in _PAIRS:
            stack.append(ch)
        elif ch in _CLOSERS:
            if not stack or stack[-1] != _CLOSERS[ch]:
                return {"ok": False, "detail": f"unexpected '{ch}'"}
            stack.pop()
        if not ch.isspace():
            last_sig = ch
        prev = ch
    if state == "block":
        return {"ok": False, "detail": "unterminated block comment"}
    if state == "regex":
        return {"ok": False, "detail": "unterminated regex literal"}
    if state in {"'", '"', "`"}:
        return {"ok": False, "detail": "unterminated string"}
    if stack:
        return {
            "ok": False,
            "detail": f"{len(stack)} unclosed '{stack[-1]}' — likely truncated output",
        }
    return {"ok": True, "detail": ""}


class _AssetCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.assets: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        wanted = {"script": "src", "link": "href", "img": "src"}
        attr_name = wanted.get(tag)
        if not attr_name:
            return
        for name, value in attrs:
            if name == attr_name and value:
                ref = value.strip()
                if ref and not ref.startswith(
                    ("http://", "https://", "data:", "//", "#", "mailto:")
                ):
                    self.assets.append(ref.split("?")[0].split("#")[0])


def _check_entrypoint(root: Path, entrypoint: str) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    path = root / entrypoint
    if not path.exists():
        return [
            {
                "path": entrypoint,
                "check": "entrypoint",
                "ok": False,
                "detail": "entrypoint file is missing",
            }
        ]
    html = path.read_text(encoding="utf-8", errors="replace")
    has_doctype = html.lstrip().lower().startswith("<!doctype")
    checks.append(
        {
            "path": entrypoint,
            "check": "doctype",
            "ok": has_doctype,
            "detail": "" if has_doctype else "missing <!doctype>",
        }
    )
    collector = _AssetCollector()
    collector.feed(html)
    for ref in collector.assets:
        exists = (root / ref).exists()
        checks.append(
            {
                "path": entrypoint,
                "check": f"asset:{ref}",
                "ok": exists,
                "detail": "" if exists else f"referenced file '{ref}' does not exist",
            }
        )
    return checks


def verify_app(
    app_root: Path,
    *,
    entrypoint: str | None = None,
    files: list[str] | None = None,
) -> dict[str, Any]:
    """Structural verification of an app: ``{ok, checks, passed, failed}``.

    ``files`` limits per-file checks (e.g. only what an edit touched); the
    entrypoint wiring is always checked when one is known.
    """
    root = Path(app_root)
    checks: list[dict[str, Any]] = []
    if entrypoint:
        checks.extend(_check_entrypoint(root, entrypoint))
    for rel in sorted(set(files or [])):
        path = root / rel
        suffix = path.suffix.lower()
        if not path.exists():
            checks.append(
                {"path": rel, "check": "exists", "ok": False, "detail": "file missing"}
            )
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            checks.append(
                {
                    "path": rel,
                    "check": "readable",
                    "ok": False,
                    "detail": safe_detail(exc),
                }
            )
            continue
        if suffix == ".json":
            try:
                json.loads(text)
                checks.append({"path": rel, "check": "json", "ok": True, "detail": ""})
            except ValueError as exc:
                checks.append(
                    {
                        "path": rel,
                        "check": "json",
                        "ok": False,
                        "detail": safe_detail(exc),
                    }
                )
        elif suffix in {".js", ".mjs", ".jsx", ".ts", ".tsx"}:
            result = scan_balance(text, language="js")
            checks.append({"path": rel, "check": "balance", **result})
        elif suffix == ".css":
            result = scan_balance(text, language="css")
            checks.append({"path": rel, "check": "balance", **result})
    failed = [c for c in checks if not c["ok"]]
    return {
        "ok": not failed,
        "checks": checks,
        "passed": len(checks) - len(failed),
        "failed": len(failed),
    }
