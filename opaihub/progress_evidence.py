"""Evidence-based progress scoring for the convergence controller (#569).

The controller's original stagnation guard counted *calls since the last
mutating tool* (`calls_since_milestone`). That is too coarse in both
directions, and the report's crash evidence shows why: a run can spend 60+
steps on `gh issue view`, `grep` and failed commands, learn a great deal, and
still be one `apply_patch` away from finishing — or it can loop on the *same*
failing search forever and look identical to the counter.

This module replaces the counter with a small utility score per observation:

* work that changes the repository scores highest,
* work that produces genuinely *new* evidence scores a little,
* work that repeats something already seen scores **nothing**, and
* an action that fails the same way twice scores **negative**.

Stagnation is then "the score has not improved in N steps", not "no edit
happened recently". A long, productive investigation keeps its budget; a loop
that re-reads the same file or re-runs the same failing command burns its
budget quickly, which is the honest distinction the counter could not make.

Scores are deliberately small integers, not model-derived confidences: the
controller must behave identically on replay, so nothing here may depend on a
provider's opinion of its own progress.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
from typing import Any, Mapping

# Per-observation utility. Tuned to the ordering the report describes
# (edit > failing test > new evidence > lint > nothing), not to absolute
# magnitudes — only the relative shape and the sign matter to the guard.
SCORE_MUTATION = 10  # the repository actually changed
SCORE_TEST_SIGNAL = 7  # a test ran and told us something
SCORE_NEW_EVIDENCE = 3  # a read/search returned something not seen before
SCORE_MINOR = 1  # ran, succeeded, told us little (lint, status)
SCORE_REPEAT = 0  # we already had this exact observation
SCORE_REPEATED_FAILURE = -2  # the same action failed the same way again

# Tools whose success changes repository or remote state.
MUTATING_TOOLS = frozenset(
    {
        "apply_patch",
        "write_file",
        "create_file",
        "delete_file",
        "git_commit",
        "open_pr",
        "github_comment",
    }
)

# Tools that produce a verification signal worth more than a plain read.
TEST_TOOLS = frozenset({"run_tests", "run_command"})

_FINGERPRINT_CHARS = 512


def observation_fingerprint(tool: str, arguments: str, content: str) -> str:
    """A stable digest of "this exact action produced this exact result".

    Bounded so a huge file read cannot dominate memory, and content-based so
    that re-reading an unchanged file collapses onto the same fingerprint even
    when the call id differs.
    """

    digest = hashlib.sha256()
    digest.update(str(tool or "").encode("utf-8", "replace"))
    digest.update(b"\0")
    digest.update(str(arguments or "").encode("utf-8", "replace"))
    digest.update(b"\0")
    digest.update(str(content or "")[:_FINGERPRINT_CHARS].encode("utf-8", "replace"))
    return digest.hexdigest()[:32]


@dataclass
class ProgressLedger:
    """Running evidence score plus the memory needed to detect repetition."""

    score: int = 0
    best_score: int = 0
    steps_since_best: int = 0
    seen_fingerprints: set[str] = field(default_factory=set)
    failure_signatures: dict[str, int] = field(default_factory=dict)
    milestones: int = 0

    def record(self, observation: Mapping[str, Any]) -> int:
        """Score one tool observation and fold it into the ledger.

        Returns the delta applied, so callers can explain a decision instead of
        just reporting a number.
        """

        tool = str(observation.get("tool") or "")
        ok = bool(observation.get("ok"))
        arguments = str(observation.get("arguments") or "")
        content = str(observation.get("content") or "")
        signature = f"{tool}|{arguments}"

        if not ok:
            # A *new* failure is information; the same failure again is not.
            seen = self.failure_signatures.get(signature, 0)
            self.failure_signatures[signature] = seen + 1
            delta = SCORE_REPEATED_FAILURE if seen else SCORE_MINOR
        else:
            # A success clears the failure streak for that exact action.
            self.failure_signatures.pop(signature, None)
            fingerprint = observation_fingerprint(tool, arguments, content)
            if fingerprint in self.seen_fingerprints:
                delta = SCORE_REPEAT
            else:
                self.seen_fingerprints.add(fingerprint)
                if tool in MUTATING_TOOLS:
                    delta = SCORE_MUTATION
                    self.milestones += 1
                elif tool in TEST_TOOLS:
                    delta = SCORE_TEST_SIGNAL
                elif content:
                    delta = SCORE_NEW_EVIDENCE
                else:
                    delta = SCORE_MINOR

        self.score += delta
        if self.score > self.best_score:
            self.best_score = self.score
            self.steps_since_best = 0
        else:
            self.steps_since_best += 1
        return delta

    def repeated_failure_count(self) -> int:
        return max(self.failure_signatures.values(), default=0)

    def is_stagnant(self, *, patience: int) -> bool:
        """True when the score has not reached a new high for `patience` steps.

        This is the whole point of the module: a run that keeps learning keeps
        its budget, and a run that keeps repeating itself loses it — regardless
        of how many tool calls either has made.
        """

        return self.steps_since_best >= max(1, int(patience))

    def summary(self) -> dict[str, Any]:
        """Compact, JSON-safe evidence for a FailureEnvelope or receipt."""

        return {
            "score": self.score,
            "best_score": self.best_score,
            "steps_since_best": self.steps_since_best,
            "distinct_observations": len(self.seen_fingerprints),
            "repeated_failures": self.repeated_failure_count(),
            "milestones": self.milestones,
        }
