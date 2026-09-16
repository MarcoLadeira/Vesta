"""The mode menu may not promise a stop the permission rules won't make.

``command_policy`` says of itself: "The matrix in AUTONOMY_RULES is the single
place a run mode is turned into run/ask/block, so the GUI permissions panel,
the CLI and the tool executor cannot drift apart about what a mode means."
``gui_permissions`` says of itself: "the panel can't drift into a comforting
lie." Both are true of the surfaces they name. The composer's mode menu is a
third surface, it names the same modes, and nothing was checking it.

Two rows had drifted, in the same direction, for the same reason -- someone
changed a rule and corrected the panel while the menu kept the old sentence:

* Auto-apply said "Pushing still asks first" long after ``push`` became
  ``allow``. A promised confirmation that never came.
* Approve edits said "Apply edits" while ``edit`` was ``ask``. Edits never
  applied; they asked.

The first version of this file only checked *push* claims, which is exactly
why it did not catch the second one. It now reads every clause of every row.

It still does not review prose. Per clause it asks one question: this text
mentions a capability -- does its claim about stopping match the rule?
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from vesta.gui_permissions import _MODE_RULES


_COMPOSER = (
    Path(__file__).resolve().parents[1] / "vesta" / "assets" / "web" / "composer.js"
)

# "without asking" flips the meaning of the asking word inside it, so it is
# removed before looking for a promise. Missing this would read "Apply edits
# without asking" as a promise to ask.
_NEGATED = re.compile(r"without\s+(asking|confirmation|approval|confirming)", re.I)
_PROMISES_A_STOP = re.compile(
    r"\b(ask|asks|confirm|confirms|approve|approves|review|reviews)\b", re.I
)
# Which capability a clause is talking about. Order matters: "safe commands"
# is run_safe, not run_any, and reading it as run_any would fail every mode
# that runs curated commands while still gating arbitrary ones.
_CAPABILITY_PATTERNS = (
    ("run_safe", re.compile(r"safe command", re.I)),
    ("run_any", re.compile(r"\bcommand", re.I)),
    ("push", re.compile(r"\bpush", re.I)),
    ("edit", re.compile(r"\bedit|\bchang", re.I)),
)
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


def _claims(description: str) -> list[tuple[str, bool]]:
    """Every (capability, promises-a-stop) pair a description makes.

    Split per clause, because one row routinely makes two opposite claims:
    "Apply edits without asking; still asks before commands."
    """

    claims = []
    for clause in re.split(r"[;.]", description):
        if not clause.strip():
            continue
        promises = bool(_PROMISES_A_STOP.search(_NEGATED.sub("", clause)))
        for capability, pattern in _CAPABILITY_PATTERNS:
            if pattern.search(clause):
                claims.append((capability, promises))
                break
    return claims


def test_the_menu_was_actually_read():
    """A parser that silently found nothing would pass every test below."""

    descriptions = _mode_descriptions()

    assert set(descriptions) == set(_MODE_RULES) | {"ask", "plan"}
    assert all(descriptions.values())


def test_every_row_makes_at_least_one_checkable_claim():
    """A guard whose patterns matched nothing would also be vacuously green."""

    checkable = [mode for mode, text in _mode_descriptions().items() if _claims(text)]

    assert len(checkable) >= 4


@pytest.mark.parametrize("mode", sorted(_mode_descriptions()))
def test_a_row_never_promises_a_stop_the_rules_do_not_make(mode: str):
    description = _mode_descriptions()[mode]
    rules = _MODE_RULES.get(mode, {})

    for capability, promises_a_stop in _claims(description):
        rule = rules.get(capability, "block")
        assert promises_a_stop == (rule == "ask"), (
            f"{mode!r} tells the user {description!r}, but {capability!r} in "
            f"that mode is {rule!r}. Fix whichever one is wrong -- and if it "
            f"is the rule, command_policy's matrix needs the same change."
        )


def test_auto_accept_edits_reaches_the_level_that_had_no_mode():
    """The gap this mode fills.

    ``AUTONOMY_RULES`` has had four levels since it was written, named after
    Claude Code's so the two tools mean the same thing by the same word. The
    GUI could reach three of them. The one it could not reach -- local work
    proceeds, shared work still asks -- is the one most people actually want.
    """
    from vestahub.autonomy import VALID_MODES
    from vestahub.command_policy import AUTO_EDITS, normalize_autonomy

    reaching = [m for m in VALID_MODES if normalize_autonomy(m) == AUTO_EDITS]

    assert reaching == ["auto-edits"]


def test_every_autonomy_level_is_reachable_from_the_menu():
    """A level nobody can select is a promise the product does not keep."""
    from vestahub.autonomy import VALID_MODES
    from vestahub.command_policy import AUTONOMY_RULES, normalize_autonomy

    reachable = {normalize_autonomy(mode) for mode in VALID_MODES}

    assert reachable == set(AUTONOMY_RULES)


def test_the_panel_row_mirrors_the_matrix_it_claims_to_mirror():
    """gui_permissions and command_policy must agree about the new mode.

    The two tables describe different things about it, and the difference is
    the whole mode. Accept Edits auto-accepts *file edits*; a command is not a
    file edit -- `git commit`, `npm install` and a test runner can each do far
    more than the edit tools can -- so commands keep asking. 40d6dc0 settled
    that ("the command row now matches NORMAL, and the whole difference
    between Manual and Accept Edits lives in the edit capability"), and this
    assertion was left describing the behaviour it replaced: it read the
    *command* matrix and expected the answer for edits.
    """
    from vestahub.command_policy import ASK, AUTONOMY_RULES, Capability
    from vesta.gui_permissions import _MODE_RULES

    level = AUTONOMY_RULES["auto-edits"]
    row = _MODE_RULES["auto-edits"]

    # The edit capability is the panel's own, and it is what "Accept Edits"
    # names: edits apply without asking.
    assert row["edit"] == "allow" and row["create"] == "allow"
    # Everything the command matrix governs still asks, and the panel says so.
    assert level[Capability.WRITE_LOCAL] == ASK
    assert row["run_any"] == "ask"
    assert level[Capability.WRITE_REMOTE] == ASK
    assert row["push"] == "ask"


def test_modes_are_ordered_from_strict_to_permissive():
    """The menu presented a tightening as a step toward more autonomy.

    ``approve-edits`` asks before even the curated safe commands that
    ``safe-auto`` runs, so it is the stricter of the two -- and it was listed
    second, below the looser one, in every surface that enumerates modes.
    """
    from vestahub.autonomy import VALID_MODES
    from vesta.gui_permissions import CAPABILITIES, _MODE_RULES

    weight = {"block": 0, "ask": 1, "allow": 2}
    freedom = [
        sum(
            weight[_MODE_RULES.get(mode, {}).get(cap, "block")]
            for cap, _ in CAPABILITIES
        )
        for mode in VALID_MODES
    ]

    assert freedom == sorted(freedom), dict(zip(VALID_MODES, freedom))
