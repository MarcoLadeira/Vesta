"""Vesta's authorship trailer on commits it writes.

When an assistant does the work, the commit should say so. GitHub reads
``Co-Authored-By`` trailers and attributes the commit to that identity, which is
why Claude shows up in a repository's contributor list and on its pull requests.
Vesta wrote commits with no trailer at all, so work it did was indistinguishable
from work the user typed by hand — the history was quietly wrong about who did
what.

Three deliberate choices:

**Co-author, not author.** The user stays the commit's author. They asked for
the change, they are accountable for it, and rewriting authorship would take
their name off their own history. Co-authorship is an addition, not a
substitution.

**Never duplicated.** Trailers are matched case-insensitively before appending,
because git and GitHub treat ``Co-authored-by`` and ``Co-Authored-By`` as the
same trailer while a naive string check does not. A commit amended or retried
must not accumulate the same line twice.

**Never invented for the user.** The identity is Vesta's own, fixed and
non-configurable from a prompt. A trailer whose address could be set by whatever
the model was told would be a way to attribute a commit to someone who did not
make it.
"""

from __future__ import annotations

import re

#: The identity GitHub attributes Vesta's commits to. `noreply` follows the
#: convention for bot/assistant addresses: it is a stable identity, not a
#: mailbox, and must never look like it can receive mail.
VESTA_NAME = "Vesta"
VESTA_EMAIL = "noreply@vesta.dev"
COAUTHOR_TRAILER = f"Co-Authored-By: {VESTA_NAME} <{VESTA_EMAIL}>"

_TRAILER_RE = re.compile(
    r"(?im)^\s*co-authored-by:\s*.*<\s*" + re.escape(VESTA_EMAIL) + r"\s*>\s*$"
)


def has_vesta_trailer(message: str) -> bool:
    """Whether ``message`` already credits Vesta, in any capitalisation."""
    return bool(_TRAILER_RE.search(str(message or "")))


def with_coauthor(message: str) -> str:
    """Return ``message`` with Vesta's co-author trailer appended.

    Git requires trailers in a final block separated from the body by a blank
    line; appending directly onto the last body line would produce a trailer git
    does not parse and GitHub does not attribute. An empty message is returned
    unchanged — a bare trailer with no subject is not a commit message, and
    manufacturing one would hide the real problem from the caller.
    """
    body = str(message or "").rstrip()
    if not body:
        return message
    if has_vesta_trailer(body):
        return body + "\n"

    # If the message already ends in a trailer block, join it rather than
    # starting a second one — git only parses the last block.
    #
    # The subject line is never a trailer block, however much it looks like
    # one. A conventional-commit subject ("fix: parser crash") matches the
    # shape exactly, and treating it as a trailer appended the credit with a
    # single newline — which `git interpret-trailers --parse` does not read,
    # so GitHub would not have attributed it. That is the dominant commit
    # style here, so the feature would have silently done nothing on most
    # commits while appearing to work.
    lines = body.split("\n")
    ends_with_trailer = len(lines) > 1 and bool(
        re.match(r"^[A-Za-z][A-Za-z-]*:\s+\S", lines[-1].strip())
    )
    separator = "\n" if ends_with_trailer else "\n\n"
    return body + separator + COAUTHOR_TRAILER + "\n"


#: Footer added to pull requests Vesta opens. GitHub does not read trailers in a
#: PR body, so contributor attribution there is a matter of saying plainly who
#: wrote the change — a reviewer should not have to check `git log` to find out
#: whether a human or an assistant produced what they are reviewing.
# Plain ASCII deliberately. This string reaches Windows consoles and log files
# on a cp1252 default encoding, where an emoji raises UnicodeEncodeError — a
# decorative character is not worth a crash on a code path that opens PRs.
PR_ATTRIBUTION = f"Opened with [{VESTA_NAME}](https://github.com/MarcoLadeira/OPai)"


def with_pr_attribution(body: str) -> str:
    """Append Vesta's attribution to a pull-request body, exactly once."""
    text = str(body or "").rstrip()
    if PR_ATTRIBUTION in text:
        return text + "\n"
    if not text:
        return PR_ATTRIBUTION + "\n"
    return text + "\n\n---\n" + PR_ATTRIBUTION + "\n"
