"""One behavioural contract every provider adapter must satisfy (#295, gate 10).

OPai routes across CLI accounts (claude, codex, copilot), local server shapes
(Ollama, any OpenAI-compatible endpoint) and free-tier APIs. They do not share a
base class and should not: a killable subprocess and an HTTP stream have
genuinely different mechanics, and forcing one hierarchy over both buys nothing
but coupling.

What they must share is *observable behaviour*. Everything above them — the
pipeline, Auto routing and its fallback chain, the run-state machine, receipts,
the cost ledger — is written against one set of promises. Where an adapter
quietly breaks one, the symptom reaches the user as **"sometimes it works and
sometimes it doesn't"**, which is the complaint this epic exists to end.

Today those promises live in docstrings and in the head of whoever wrote each
adapter, so they are neither enforced nor drift-visible. This module states them
as named clauses — each recording the user-visible symptom of its breach — and
provides a harness that checks any adapter against every clause with no network
call and no spend.

The families are reached through :class:`Probe`, which normalises their
different entry points into one :class:`Outcome`, and driven by a
:class:`Script` that says what the simulated provider does. The harness owns the
scenarios; each probe owns only the mechanics of its own transport. That split
*is* the common protocol Workstream C asks for, expressed where it can be
enforced rather than as a refactor of two working hierarchies.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from opai.provider_contract import ERROR_CODES

#: A clause check gets this long before the adapter is considered stuck. Far
#: longer than any in-memory fake needs, far shorter than a real provider
#: timeout, so a hang fails loudly instead of stalling the suite.
CLAUSE_TIMEOUT_SECONDS = 10.0

#: ``available()`` is called while building the model picker and while Auto
#: scores candidates — on the UI path, once per candidate. Anything slower than
#: this is felt as the app hanging.
AVAILABILITY_BUDGET_SECONDS = 2.0


@dataclass(frozen=True)
class Clause:
    """One promise, with the symptom its breach produces.

    The symptom is not decoration. A clause nobody can trace to a user-visible
    failure is a style preference, and style preferences do not belong in a
    gate that blocks a release.
    """

    id: str
    statement: str
    symptom: str


CLAUSES: tuple[Clause, ...] = (
    Clause(
        id="available_never_raises",
        statement="available() returns a bool for any configuration; it never raises.",
        symptom="A provider that is merely unreachable takes down the picker or "
        "Auto's candidate scan, so models that do work stop being offered.",
    ),
    Clause(
        id="available_is_bounded",
        statement=f"available() returns within {AVAILABILITY_BUDGET_SECONDS}s.",
        symptom="The window freezes while a dead endpoint is probed, and the "
        "user cannot even switch to a provider that is up.",
    ),
    Clause(
        id="cancel_is_honoured",
        statement="A cancel set before or during a run ends the call promptly.",
        symptom="Stop appears to do nothing; the run keeps spending after the "
        "user has moved on.",
    ),
    Clause(
        id="cancel_is_reported",
        statement="A cancelled run reports cancellation — not an empty answer, "
        "not a failure.",
        symptom="Stop is rendered as 'the model had nothing to say', or as an "
        "error the user then tries to debug.",
    ),
    Clause(
        id="timeout_is_distinct",
        statement="A timeout is reported as a timeout, distinguishably from a "
        "provider failure.",
        symptom="A retryable stall and an unretryable refusal look identical, "
        "so Auto retries what cannot succeed and gives up on what can.",
    ),
    Clause(
        id="partial_output_survives",
        statement="Text already emitted is preserved when the run then fails or "
        "is cancelled.",
        symptom="Work the user watched appear on screen vanishes, and the same "
        "tokens are paid for twice on the retry.",
    ),
    Clause(
        id="failure_is_described",
        statement="A provider failure yields a non-empty description rather "
        "than a bare empty result.",
        symptom="A dead end with nothing to act on — the 'no answer, no reason' "
        "case that leaves the user stuck.",
    ),
    Clause(
        id="failure_is_classified",
        statement="A failure classifies into the shared error vocabulary "
        "(`opai.provider_contract.ERROR_CODES`), carrying a retryability flag.",
        symptom="Every provider invents its own wording, so the UI cannot tell "
        "the user what to do and Auto cannot tell what is worth retrying.",
    ),
    Clause(
        id="timeout_is_retryable",
        statement="A timeout classifies as PROVIDER_TIMEOUT, which is marked "
        "retryable — unlike an auth or config failure.",
        symptom="Auto burns attempts on a refusal that will never succeed, and "
        "abandons a stall that would have worked on the next try.",
    ),
    Clause(
        id="failure_is_not_success",
        statement="A failed run is never reported as a successful answer.",
        symptom="False completion: the run claims it worked and nothing happened.",
    ),
    Clause(
        id="empty_output_is_not_success",
        statement="An empty provider response is a failure, not a successful "
        "empty answer.",
        symptom="The blank reply that started this epic — the run says it "
        "succeeded and there is nothing there.",
    ),
    Clause(
        id="cost_is_real_or_unknown",
        statement="Cost is a real measurement or explicitly unknown; never a "
        "fabricated zero.",
        symptom="Spend is under-reported, so the budget guard lets a run past a "
        "cap the user set.",
    ),
    Clause(
        id="listener_errors_are_contained",
        statement="An exception from an on_text listener does not abort the run.",
        symptom="A rendering bug in one activity line destroys an otherwise "
        "healthy run, losing the work with it.",
    ),
    Clause(
        id="success_never_raises",
        statement="A healthy run returns a result rather than raising.",
        symptom="An unhandled exception escapes into the pipeline, which cannot "
        "attribute it to a provider and so reports an internal error.",
    ),
)

CLAUSE_IDS: tuple[str, ...] = tuple(c.id for c in CLAUSES)
CLAUSES_BY_ID: dict[str, Clause] = {c.id: c for c in CLAUSES}


@dataclass(frozen=True)
class Script:
    """What the simulated provider does. Owned by the harness, obeyed by probes.

    Every scenario here is one OPai has actually shipped a bug for.
    """

    chunks: tuple[str, ...] = ()
    #: ``ok`` finish normally · ``fail`` error after the chunks · ``timeout``
    #: exceed the deadline · ``empty`` succeed with no content at all.
    then: str = "ok"
    error: str = "provider connection reset"
    #: ``None`` means the provider reported no usage, which must stay unknown.
    cost: float | None = None


@dataclass
class Outcome:
    """The normalised terminal result of one adapter call.

    Deliberately not a union of the families' shapes: each field means one
    thing regardless of which adapter produced it, because the layers above
    read them without knowing which one they hold.
    """

    text: str = ""
    cancelled: bool = False
    timed_out: bool = False
    error: str = ""
    #: ``None`` is *unknown*, which is not the same as ``0.0`` meaning free.
    cost: float | None = None

    @property
    def failed(self) -> bool:
        return bool(self.error)

    @property
    def succeeded(self) -> bool:
        return not self.failed and not self.cancelled and not self.timed_out


class Probe(Protocol):
    """How to exercise one adapter family with no network call and no spend."""

    name: str

    def available(self) -> bool: ...

    def run(
        self,
        script: Script,
        *,
        cancel: threading.Event | None = None,
        timeout: float = 30.0,
        on_text: Callable[[str], None] | None = None,
    ) -> Outcome: ...


@dataclass(frozen=True)
class ClauseResult:
    clause: str
    passed: bool
    detail: str = ""


@dataclass
class ConformanceReport:
    adapter: str
    results: list[ClauseResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return bool(self.results) and all(r.passed for r in self.results)

    def failures(self) -> list[ClauseResult]:
        return [r for r in self.results if not r.passed]

    def get(self, clause: str) -> ClauseResult | None:
        for result in self.results:
            if result.clause == clause:
                return result
        return None


def _classify(outcome: Outcome) -> str:
    """Map an outcome onto OPai's shipped error vocabulary.

    Reuses `opai.provider_contract` rather than inventing a second taxonomy:
    a conformance matrix that graded adapters against a vocabulary the product
    does not actually use would prove nothing about the product.
    """
    from opai.provider_contract import classify_error_code

    return classify_error_code(outcome.error, timed_out=outcome.timed_out)


def _retryable(code: str) -> bool | None:
    """Whether ``code`` is retryable, or ``None`` if the code declares nothing.

    ``None`` is a conformance failure in itself: Auto has to decide whether to
    try again, and "no opinion" makes that decision arbitrary.
    """
    from opai.provider_contract import _ERROR_SPECS

    spec = _ERROR_SPECS.get(code)
    if not spec or "retryable" not in spec:
        return None
    return bool(spec["retryable"])


def _call(fn: Callable[[], Any]) -> tuple[Any, float, BaseException | None]:
    """Invoke ``fn``, returning its value, elapsed time and any exception.

    Exceptions are captured rather than propagated: several clauses are exactly
    "this must not raise", and a harness that dies on the first breach cannot
    produce a matrix — which is the whole deliverable.
    """
    start = time.monotonic()
    try:
        return fn(), time.monotonic() - start, None
    except BaseException as exc:  # noqa: BLE001 - "does not raise" is a clause
        return None, time.monotonic() - start, exc


def check(probe: Probe) -> ConformanceReport:
    """Run every clause against ``probe``, clause by clause, never raising."""
    report = ConformanceReport(adapter=probe.name)
    add = report.results.append

    # -- availability ----------------------------------------------------
    value, elapsed, raised = _call(probe.available)
    add(
        ClauseResult(
            "available_never_raises",
            raised is None and isinstance(value, bool),
            f"raised {type(raised).__name__}" if raised else f"returned {value!r}",
        )
    )
    add(
        ClauseResult(
            "available_is_bounded",
            elapsed <= AVAILABILITY_BUDGET_SECONDS,
            f"{elapsed:.2f}s",
        )
    )

    # -- a healthy run ---------------------------------------------------
    good = Script(chunks=("Hello ", "world."), cost=0.0123)
    outcome, _, raised = _call(lambda: probe.run(good))
    add(
        ClauseResult(
            "success_never_raises",
            raised is None and isinstance(outcome, Outcome),
            f"raised {type(raised).__name__}" if raised else "returned an Outcome",
        )
    )
    # `None` is a conforming answer: several CLIs genuinely do not report usage,
    # and saying so is honest. A hard `0.0` on a paid run is the breach — it
    # reads as free and slips past the budget guard.
    add(
        ClauseResult(
            "cost_is_real_or_unknown",
            isinstance(outcome, Outcome) and (outcome.cost is None or outcome.cost > 0),
            f"cost={outcome.cost!r}" if outcome else "no outcome",
        )
    )

    # -- cancellation ----------------------------------------------------
    cancel = threading.Event()
    cancel.set()
    outcome, elapsed, raised = _call(
        lambda: probe.run(Script(chunks=("partial ",)), cancel=cancel)
    )
    if raised is not None or not isinstance(outcome, Outcome):
        detail = f"raised {type(raised).__name__}"
        add(ClauseResult("cancel_is_honoured", False, detail))
        add(ClauseResult("cancel_is_reported", False, detail))
    else:
        add(
            ClauseResult(
                "cancel_is_honoured",
                elapsed <= CLAUSE_TIMEOUT_SECONDS,
                f"{elapsed:.2f}s",
            )
        )
        add(
            ClauseResult(
                "cancel_is_reported",
                outcome.cancelled and not outcome.failed,
                f"cancelled={outcome.cancelled} error={outcome.error!r}",
            )
        )

    # -- timeout ---------------------------------------------------------
    outcome, _, raised = _call(lambda: probe.run(Script(then="timeout"), timeout=0.25))
    add(
        ClauseResult(
            "timeout_is_distinct",
            isinstance(outcome, Outcome)
            and outcome.timed_out
            and not outcome.cancelled,
            f"raised {type(raised).__name__}"
            if raised
            else f"timed_out={outcome.timed_out} cancelled={outcome.cancelled}"
            if outcome
            else "no outcome",
        )
    )
    if isinstance(outcome, Outcome):
        code = _classify(outcome)
        add(
            ClauseResult(
                "timeout_is_retryable",
                code == "PROVIDER_TIMEOUT" and _retryable(code) is True,
                f"code={code} retryable={_retryable(code)}",
            )
        )
    else:
        add(ClauseResult("timeout_is_retryable", False, "no outcome"))

    # -- failure after partial output ------------------------------------
    seen: list[str] = []
    partial = Script(chunks=("I found the ", "bug in "), then="fail")
    outcome, _, raised = _call(lambda: probe.run(partial, on_text=seen.append))
    if raised is not None or not isinstance(outcome, Outcome):
        detail = f"raised {type(raised).__name__}"
        for clause in (
            "partial_output_survives",
            "failure_is_described",
            "failure_is_classified",
            "failure_is_not_success",
        ):
            add(ClauseResult(clause, False, detail))
    else:
        add(
            ClauseResult(
                "partial_output_survives",
                "I found the " in outcome.text,
                f"text={outcome.text[:40]!r} streamed={''.join(seen)[:40]!r}",
            )
        )
        add(
            ClauseResult(
                "failure_is_described",
                bool(outcome.error.strip()),
                f"error={outcome.error!r}",
            )
        )
        code = _classify(outcome)
        add(
            ClauseResult(
                "failure_is_classified",
                code in ERROR_CODES and _retryable(code) is not None,
                f"code={code} retryable={_retryable(code)}",
            )
        )
        add(
            ClauseResult(
                "failure_is_not_success",
                not outcome.succeeded,
                f"succeeded={outcome.succeeded}",
            )
        )

    # -- an empty answer -------------------------------------------------
    outcome, _, raised = _call(lambda: probe.run(Script(then="empty")))
    add(
        ClauseResult(
            "empty_output_is_not_success",
            isinstance(outcome, Outcome) and not outcome.succeeded,
            f"raised {type(raised).__name__}"
            if raised
            else f"succeeded={outcome.succeeded} text={outcome.text!r}"
            if outcome
            else "no outcome",
        )
    )

    # -- a listener that throws -------------------------------------------
    def _bad_listener(_chunk: str) -> None:
        raise RuntimeError("a rendering bug in the activity line")

    outcome, _, raised = _call(
        lambda: probe.run(Script(chunks=("still ", "fine")), on_text=_bad_listener)
    )
    add(
        ClauseResult(
            "listener_errors_are_contained",
            raised is None and isinstance(outcome, Outcome) and outcome.succeeded,
            f"raised {type(raised).__name__}" if raised else "run survived",
        )
    )

    return report


def format_matrix(reports: list[ConformanceReport]) -> str:
    """Render reports as a Markdown matrix — the artefact gate 10 asks for."""
    if not reports:
        return "_no adapters checked_"
    header = "| clause | " + " | ".join(r.adapter for r in reports) + " |"
    rule = "| --- | " + " | ".join("---" for _ in reports) + " |"
    lines = [header, rule]
    for clause in CLAUSE_IDS:
        cells = []
        for report in reports:
            result = report.get(clause)
            cells.append(
                "—" if result is None else ("pass" if result.passed else "**FAIL**")
            )
        lines.append(f"| `{clause}` | " + " | ".join(cells) + " |")
    return "\n".join(lines)
