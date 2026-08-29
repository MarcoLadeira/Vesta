"""The mode menu may not promise a confirmation the autonomy matrix won't give.

``command_policy`` says of itself: "The matrix in AUTONOMY_RULES is the single
place a run mode is turned into run/ask/block, so the GUI permissions panel,
the CLI and the tool executor cannot drift apart about what a mode means."
``gui_permissions`` says of itself: "the panel can't drift into a comforting
lie." Both were true of the surfaces they name. The composer's mode menu is a
third surface, it names the same modes, and nothing was checking it.

It had drifted. When Full Auto's ``push`` moved from ``ask`` to ``allow``, the
permissions panel was corrected and this menu was not, so the row a user reads
*while choosing the mode* went on saying "Pushing still asks first" about a
mode that pushes without asking. That is the worst possible place for that
particular sentence to be wrong.

This is narrow on purpose. It does not review the prose. It asks one question
of each row -- if the copy talks about pushing, does its claim about asking
match the matrix? -- because that is the claim that was wrong and the claim a
user acts on.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from opaihub.command_policy import (
    ASK,
    AUTONOMY_RULES,
    Capability,
    normalize_autonomy,
)


_COMPOSER = (
    Path(__file__).resolve().parents[1] / "opai" / "assets" / "web" / "composer.js"
)

# "ask", "asks", "confirm", "approve", "check with you" -- the vocabulary a
# description uses to promise a stop. Deliberately generous: a false positive
# here costs one reworded sentence, a false negative costs a silent push.
_PROMISES_A_STOP = re.compile(r"\b(ask|asks|confirm|confirms|approve|approves)\b", re.I)
_ENTRY = re.compile(r"""^\s*"?([a-z-]+)"?\s*:\s*"((?:[^"\\]|\\.)*)"\s*,?\s*$""")


def _mode_descriptions() -> dict[str, str]:
    """Read MODE_DESC out of composer.js.

    Parsed rather than duplicated: a copy here would be one more thing to
    drift, which is the exact failure this file exists to catch.
    """

    source = _COMPOSER.read_text(encoding="utf-8")
    start = source.index("var MODE_DESC = {")
    body = source[start : source.index("};", start)]
    found = {}
    for line in body.splitlines()[1:]:
        match = _ENTRY.match(line)
        if match:
            found[match.group(1)] = match.group(2)
    return found


def test_the_menu_was_actually_read():
    """A parser that silently found nothing would pass every test below."""

    descriptions = _mode_descriptions()

    assert set(descriptions) >= {
        "ask",
        "plan",
        "safe-auto",
        "approve-edits",
        "full-auto",
    }
    assert all(descriptions.values())


@pytest.mark.parametrize("mode", sorted(_mode_descriptions()))
def test_a_mode_row_never_promises_a_stop_the_matrix_does_not_make(mode: str):
    description = _mode_descriptions()[mode]
    if "push" not in description.casefold():
        return
    level = normalize_autonomy(mode)
    verdict = AUTONOMY_RULES[level][Capability.WRITE_REMOTE]
    promises_a_stop = bool(_PROMISES_A_STOP.search(description))

    assert promises_a_stop == (verdict == ASK), (
        f"{mode!r} tells the user {description!r}, but a push at autonomy "
        f"level {level!r} is {verdict!r}. Fix whichever one is wrong -- and if "
        f"it is the matrix, the permissions panel needs the same change."
    )
