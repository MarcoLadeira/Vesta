"""Retry safety for external side effects (#616).

The invariant this module exists to enforce, stated by #616:

    A timeout, exception, process crash or missing response is not proof
    that an external action did not start or finish.

OPai already retries. ``auto_router.should_retry_same_provider`` re-runs *the
identical request* when a provider returns a transport-style error, and three
of the five codes it accepts — ``PROVIDER_TIMEOUT``, ``STREAM_ABORTED`` and
``NO_RESPONSE`` — are exactly the cases the invariant names. Silence is not
evidence of non-delivery. A stream that aborted mid-token proves the opposite:
the provider began generating, and bills for what it generated.

What was missing was not a retry limit but a *classification*. This module
supplies the two halves of the judgement:

``OperationClass``
    How an operation may be repeated, per #616's five classes. Declared per
    operation kind, so the rule is data rather than scattered conditionals.

``DispatchProof``
    What the failure actually tells us about whether the effect landed.
    Derived from the error code, because that is the only evidence available
    at the moment a retry is decided.

An operation is repeated automatically only when repetition is harmless, or
when the failure *proves* nothing left the machine. Everything else is handed
back for a human decision rather than being quietly duplicated.

Deliberately not a scheduler, a queue or a store. ``idempotency`` already
provides exact-once execution for operations that reach dispatch; this answers
the question that comes first — whether a second dispatch may be attempted at
all. The two compose: idempotency protects a *replayed turn*, this protects an
*automatic retry*.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class OperationClass(str, Enum):
    """How an operation may be repeated (#616's five retry classes).

    Ordered from most to least permissive. The order is load-bearing:
    ``UNKNOWN_KIND_CLASS`` picks the most restrictive member, and widening
    that default must be a deliberate edit rather than an accident of
    dictionary ordering.
    """

    #: 1. Pure or read-only. Repeating changes nothing outside OPai.
    SAFELY_REPEATABLE = "safely_repeatable"
    #: 2. The external system deduplicates via an idempotency key we send.
    PROVIDER_IDEMPOTENT = "provider_idempotent"
    #: 3. The effect can be observed externally before deciding to repeat.
    RECONCILABLE = "reconcilable"
    #: 4. Repeatable only after proving no dispatch occurred.
    NEEDS_PROOF_OF_NO_DISPATCH = "needs_proof_of_no_dispatch"
    #: 5. Never automatically repeated. A person decides.
    CONTINUITY_UNCERTAIN = "continuity_uncertain"


class DispatchProof(str, Enum):
    """What a failure proves about whether the effect reached the outside."""

    #: The operation demonstrably never left OPai.
    NOT_DISPATCHED = "not_dispatched"
    #: It may or may not have landed. The honest default.
    UNKNOWN = "unknown"
    #: It demonstrably started. Cost or effect may already exist.
    DISPATCHED = "dispatched"


# Error code -> what it proves. Only codes that genuinely establish
# non-delivery may map to NOT_DISPATCHED; everything else stays UNKNOWN,
# because "we did not hear back" is not "it did not happen".
_ERROR_PROOF: dict[str, DispatchProof] = {
    # The provider refused the connection before inference began. Nothing was
    # generated and nothing is billable.
    "PROVIDER_UNAVAILABLE": DispatchProof.NOT_DISPATCHED,
    "CONNECTION_REFUSED": DispatchProof.NOT_DISPATCHED,
    "AUTH_INVALID": DispatchProof.NOT_DISPATCHED,
    "CONFIG_INVALID": DispatchProof.NOT_DISPATCHED,
    "MODEL_UNAVAILABLE": DispatchProof.NOT_DISPATCHED,
    # A cancel that landed before the request was handed to the provider.
    "CANCELLED_BEFORE_DISPATCH": DispatchProof.NOT_DISPATCHED,
    # The request was accepted and tokens were already streaming back. The
    # provider did work, and charges for it.
    "STREAM_ABORTED": DispatchProof.DISPATCHED,
    # Genuinely ambiguous: the request may have been fully served and the
    # response lost on the way home.
    "PROVIDER_TIMEOUT": DispatchProof.UNKNOWN,
    "NO_RESPONSE": DispatchProof.UNKNOWN,
    "NETWORK_ERROR": DispatchProof.UNKNOWN,
}

#: An unrecognised operation kind is treated as the most dangerous thing it
#: could be. #616: "Unknown defaults to the restrictive class for paid,
#: mutating or irreversible work."
UNKNOWN_KIND_CLASS = OperationClass.CONTINUITY_UNCERTAIN

# Declared class per operation kind. Adding an outward effect without adding
# it here does not fail open — it inherits UNKNOWN_KIND_CLASS.
_REGISTRY: dict[str, OperationClass] = {
    # -- Reads. Repeating costs nothing and changes nothing. ---------------
    "read_file": OperationClass.SAFELY_REPEATABLE,
    "find_files": OperationClass.SAFELY_REPEATABLE,
    "search_code": OperationClass.SAFELY_REPEATABLE,
    "git_status": OperationClass.SAFELY_REPEATABLE,
    "github_pr_status": OperationClass.SAFELY_REPEATABLE,
    "github_get_issue": OperationClass.SAFELY_REPEATABLE,
    "github_search_issues": OperationClass.SAFELY_REPEATABLE,
    # -- Free inference. No money, no outward trace. ------------------------
    # Re-running a free model is the retry that makes flaky endpoints usable,
    # and it is genuinely harmless: nothing is billed and nothing is visible
    # outside this machine.
    "model_call_free": OperationClass.SAFELY_REPEATABLE,
    # -- Paid inference. The case #616 was filed for. -----------------------
    # A repeated paid call is a second charge that no local record proves was
    # avoidable, so it needs evidence the first never landed.
    "model_call_paid": OperationClass.NEEDS_PROOF_OF_NO_DISPATCH,
    # -- Filesystem. Rewriting identical bytes converges. --------------------
    "write_file": OperationClass.RECONCILABLE,
    # A patch applied twice fails on context mismatch rather than applying
    # twice, and the working tree can be inspected before retrying.
    "apply_patch": OperationClass.RECONCILABLE,
    # -- Git. Local history is observable before acting. --------------------
    "git_create_branch": OperationClass.RECONCILABLE,
    "git_add": OperationClass.RECONCILABLE,
    # Guarded by an idempotency key at the call site; a repeat would otherwise
    # create a second commit with the same content.
    "git_commit": OperationClass.PROVIDER_IDEMPOTENT,
    # Pushing an already-pushed ref is "Everything up-to-date".
    "git_push": OperationClass.RECONCILABLE,
    # -- GitHub writes. Outward, visible, not undoable by OPai. -------------
    "open_pr": OperationClass.PROVIDER_IDEMPOTENT,
    "github_comment": OperationClass.PROVIDER_IDEMPOTENT,
    # Reviewer requests are set-valued, so a repeat is absorbed.
    "github_request_review": OperationClass.RECONCILABLE,
    # -- Arbitrary commands. Unknowable by construction. --------------------
    # `run_command` can be `rm -rf`, `npm publish` or `curl -X POST`. There is
    # no general way to tell whether repeating one is safe.
    "run_command": OperationClass.CONTINUITY_UNCERTAIN,
}


def classify_operation(kind: str) -> OperationClass:
    """The declared retry class for ``kind``; restrictive when unknown."""

    return _REGISTRY.get(str(kind or "").strip(), UNKNOWN_KIND_CLASS)


def dispatch_proof(error_code: str) -> DispatchProof:
    """What ``error_code`` proves about whether the effect reached the outside.

    Unrecognised codes are ``UNKNOWN`` rather than ``NOT_DISPATCHED``: a code
    nobody has classified is not evidence of anything.
    """

    return _ERROR_PROOF.get(
        str(error_code or "").strip().upper(), DispatchProof.UNKNOWN
    )


def model_call_kind(*, is_free: bool) -> str:
    """The operation kind for a provider turn.

    Free and paid inference are the same mechanical act with opposite retry
    consequences, so they are separate kinds rather than one kind with a flag.
    """

    return "model_call_free" if is_free else "model_call_paid"


@dataclass(frozen=True)
class RetryDecision:
    """Whether an automatic retry is permitted, and the reason either way."""

    allowed: bool
    operation_class: OperationClass
    proof: DispatchProof
    reason: str

    def __bool__(self) -> bool:
        return self.allowed


def _denied(
    operation_class: OperationClass, proof: DispatchProof, reason: str
) -> RetryDecision:
    return RetryDecision(False, operation_class, proof, reason)


def retry_decision(
    kind: str,
    *,
    error_code: str = "",
    attempts: int = 0,
    max_attempts: int = 1,
) -> RetryDecision:
    """Decide whether ``kind`` may be automatically re-dispatched.

    ``attempts`` is how many retries have already been spent on this operation
    in the current turn; ``max_attempts`` is the budget. Exhausting the budget
    denies before any class reasoning, so a permissive class can never loop.
    """

    operation_class = classify_operation(kind)
    proof = dispatch_proof(error_code)

    if attempts >= max_attempts:
        return _denied(
            operation_class,
            proof,
            "the retry budget for this turn is already spent",
        )

    if operation_class is OperationClass.SAFELY_REPEATABLE:
        return RetryDecision(
            True, operation_class, proof, "repeating this has no external effect"
        )

    if proof is DispatchProof.NOT_DISPATCHED:
        return RetryDecision(
            True,
            operation_class,
            proof,
            "the failure proves the operation never left OPai",
        )

    if operation_class is OperationClass.CONTINUITY_UNCERTAIN:
        return _denied(
            operation_class,
            proof,
            "this operation cannot be repeated safely without review",
        )

    if operation_class in (
        OperationClass.PROVIDER_IDEMPOTENT,
        OperationClass.RECONCILABLE,
    ):
        return RetryDecision(
            True,
            operation_class,
            proof,
            "a duplicate would be absorbed rather than applied twice",
        )

    # NEEDS_PROOF_OF_NO_DISPATCH, without that proof.
    detail = (
        "the operation was already dispatched"
        if proof is DispatchProof.DISPATCHED
        else "whether the operation landed is unknown"
    )
    return _denied(operation_class, proof, f"{detail}; repeating it may duplicate it")
