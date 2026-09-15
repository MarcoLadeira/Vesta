"""The confirm list must mean what the file says it means.

``risky_commands.yaml`` states its own rule, in a comment written when
``git commit`` was removed from it: a command is gated because it "touches
shared/remote state or is hard to undo". ``git merge`` and ``git rebase`` were
gated anyway, and neither is either -- both are local, and both are undone by
``--abort`` mid-operation or ``git reset --hard ORIG_HEAD`` after, with the
reflog behind them.

That was not a cosmetic mismatch. The PreToolUse hook has no interactive
channel, so a ``confirm`` verdict is a hard deny in an autonomous run: an
agent that had pushed a branch and opened a PR could not merge ``origin/main``
to resolve the conflict, and the block told it not to work around the refusal.
It is the same failure the comment describes for ``git commit``, still live
for its neighbours on the list.

The tell that the rule was matching a word rather than an effect: ``git merge
--abort`` and ``git rebase --abort``, the undo operations, were gated too.

These tests hold the list to its own sentence, and hold the two copies of it
to each other.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from vestahub.command_policy import Capability, classify_command_capability
from vestahub.sandbox import classify_command


_REPO = Path(__file__).resolve().parents[1]
_LIVE = _REPO / "hub" / "security" / "risky_commands.yaml"
_PACKAGED = _REPO / "vestahub" / "data" / "hub" / "security" / "risky_commands.yaml"


def _confirm_patterns() -> list[str]:
    data = yaml.safe_load(_LIVE.read_text(encoding="utf-8"))
    return list(data.get("confirm", []))


# A pattern is a glob, so it needs a realistic command before the capability
# classifier can say anything true about it. Probing the bare pattern reports
# `rm -rf` as merely local, which is an artefact of the missing operand.
_PROBES = {
    "git clean -f*": "git clean -fd",
    "git branch -d *": "git branch -d feature/x",
    "rm -rf": "rm -rf build",
    "rm -fr": "rm -fr build",
    "rm -r": "rm -r node_modules",
    "rm --recursive*": "rm --recursive build",
    "Remove-Item -Recurse": "Remove-Item -Recurse build",
}

# Entries the capability classifier does not rate as remote-or-destructive, and
# which are gated anyway on purpose. Each needs a reason that survives reading:
# "we were not sure" belongs in neither this list nor the policy.
_JUSTIFIED_EXCEPTIONS = {
    # -d refuses an unmerged branch, but a branch pointer is still the only
    # easy handle on its commits; losing it means going to the reflog.
    "git branch -d *": "deleting a branch is the only handle on its commits",
    # Deletes build caches, volumes and images that can take a long time to
    # rebuild and are not in version control.
    "docker system prune": "removes unversioned state that is slow to rebuild",
    "docker compose down -v": "-v destroys named volumes, including databases",
    # A production deploy is outward-facing; the classifier simply does not
    # recognise the executable.
    "vercel --prod": "deploys to production; classifier does not know the tool",
    # `gh api` is a GET by default and the classifier rates it READ, correctly:
    # it becomes WRITE_REMOTE with -X POST and DESTRUCTIVE with DELETE. Gating
    # the whole verb is the deliberate fail-safe for a command whose danger is
    # carried entirely in its flags.
    "gh api": "danger depends on the method flag, so the verb is gated wholesale",
}


def test_both_copies_of_the_policy_are_identical():
    """Two files, two audiences, one policy.

    ``hub_root`` prefers a repo-local ``hub/`` and falls back to the packaged
    copy, so a developer in the repo and a user of an installed Vesta read
    different files. Nothing syncs them. A safety policy that can differ by
    installation shape is one edit away from being two policies.
    """

    assert _LIVE.read_text(encoding="utf-8") == _PACKAGED.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "command",
    [
        "git merge origin/main",
        "git merge --ff-only origin/main",
        "git merge --abort",
        "git rebase origin/main",
        "git rebase --abort",
    ],
    ids=lambda value: value.replace(" ", "-"),
)
def test_local_history_operations_are_not_gated(command: str):
    """The reported bug: a conflict that could not be resolved.

    None of these touch a remote, and every one of them is undoable.
    """

    assert classify_command(command, _REPO)["decision"] == "allow"


@pytest.mark.parametrize(
    "command",
    [
        "git push",
        "git push --force",
        "git reset --hard",
        "git clean -fd",
        "git branch -D scratch",
        "gh pr create",
        "gh repo delete",
    ],
    ids=lambda value: value.replace(" ", "-"),
)
def test_what_the_rule_actually_describes_stays_gated(command: str):
    """Ungating merge must not have ungated its neighbours."""

    assert classify_command(command, _REPO)["decision"] in {"confirm", "deny"}


def test_a_rebase_that_needs_a_force_push_is_still_stopped_at_the_push():
    """Why ungating rebase does not open a hole.

    The danger in a rebase is publishing it over a shared branch, and that
    step is classified destructive and refused on its own account.
    """

    assert classify_command("git rebase origin/main", _REPO)["decision"] == "allow"
    assert (
        classify_command_capability("git push --force").capability
        is Capability.DESTRUCTIVE
    )


@pytest.mark.parametrize(
    "pattern", _confirm_patterns(), ids=lambda v: v.replace(" ", "-")
)
def test_every_gated_command_is_remote_or_hard_to_undo(pattern: str):
    """The list held to the sentence written at the top of it.

    A local, reversible command on this list is not a cautious choice: the
    hook turns it into a refusal an autonomous run cannot get past. If a new
    entry lands here, either it genuinely touches shared state -- and the
    capability classifier should say so -- or it needs a reason in
    ``_JUSTIFIED_EXCEPTIONS`` that someone can read and disagree with.
    """

    if pattern in _JUSTIFIED_EXCEPTIONS:
        pytest.skip(f"documented exception: {_JUSTIFIED_EXCEPTIONS[pattern]}")
    probe = _PROBES.get(pattern, pattern.replace("*", "x").strip())
    capability = classify_command_capability(probe).capability

    assert capability in {Capability.WRITE_REMOTE, Capability.DESTRUCTIVE}, (
        f"{pattern!r} is gated, but {probe!r} classifies as {capability.name}. "
        "The gate has no interactive channel, so gating a local, reversible "
        "command hard-denies it in autonomous runs. Either it belongs off the "
        "list, or it needs a documented reason in _JUSTIFIED_EXCEPTIONS."
    )


def test_the_exception_list_has_no_stale_entries():
    """An exception for a rule that no longer exists hides the next mistake."""

    assert set(_JUSTIFIED_EXCEPTIONS) <= set(_confirm_patterns())
