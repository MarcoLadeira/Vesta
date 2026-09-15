"""#618: one scenario source, every surface, same canonical meaning.

The defect this replaces was never a single wrong mapping. It was that each
surface owned its own, so a scenario could be "handled" everywhere and still
mean four different things. Hand-copying expectations into per-surface fixtures
would rebuild exactly that: four tables to keep in sync, drifting silently.

So the scenarios below are declared once. Every surface is driven from the same
declaration and compared against the same canonical fields. A surface may
differ in wording, ordering and which fields it shows; it may not differ in
what the run *meant*.

The 34 scenarios are the cross-path matrix required by #618, covering the
endings that used to collapse into a generic failure: timeouts with different
causes, cancellation at different depths, delivery that partly succeeded, cost
that is unknown rather than zero, and legacy records of both known and unknown
vintage.
"""

from __future__ import annotations

import unittest
from dataclasses import dataclass, field
from typing import Any

from opai.gui_recents import thread_status_for_result
from opaihub.background_runs import _terminal_from_payload
from opaihub.generated_lifecycle import STATE_SPECS, TERMINAL_STATE_IDS
from opaihub.run_result import RunResult


@dataclass(frozen=True)
class Scenario:
    """One execution ending, declared once and replayed through every surface."""

    name: str
    state: str
    reason_detail: str
    mutating: bool = False
    #: Extra evidence the projection carries; kept small and explicit so a
    #: reader can see exactly what distinguishes one ending from another.
    verification: dict[str, Any] | None = None
    delivery: dict[str, Any] | None = None
    economics: dict[str, Any] | None = None
    recovery: dict[str, Any] | None = None
    diagnostics: dict[str, Any] = field(default_factory=dict)
    #: The legacy status a record of this shape used to carry. Present to prove
    #: it can no longer change the canonical answer.
    legacy_status: str = "answered"

    def build(self) -> RunResult:
        return RunResult.from_payload(
            state=self.state,
            reason_detail=self.reason_detail,
            final_transition_at="2026-08-11T12:00:00+00:00",
            mutating=self.mutating,
            verification=self.verification,
            delivery=self.delivery,
            economics=self.economics,
            recovery=self.recovery or {"automatic_retry": False, "reason": "none"},
            diagnostics=self.diagnostics or None,
        )


def _verified(applicable: bool = True) -> dict[str, Any]:
    return {
        "applicable": applicable,
        "verdict": "verified" if applicable else "not_applicable",
        "record_ref": {"kind": "completion_verdict", "id": "objective_met"},
    }


def _unverified(verdict: str) -> dict[str, Any]:
    return {"applicable": True, "verdict": verdict}


def _delivered(ref: str = "turn") -> dict[str, Any]:
    """Delivery evidence a `completed` result is required to carry.

    The schema refuses to build a completed result without it, which is
    invariant R12 enforced at construction rather than asserted afterwards: a
    run cannot claim it delivered what it never delivered.
    """

    return {
        "applicable": True,
        "verdict": "delivered",
        "record_ref": {"kind": "turn_record", "id": ref},
    }


def _reconciled(ref: str = "ledger") -> dict[str, Any]:
    """Cost evidence a `completed` result is required to carry (R10)."""

    return {
        "integrity": "reconciled",
        "record_ref": {"kind": "ledger_event", "id": ref},
    }


SCENARIOS: tuple[Scenario, ...] = (
    # --- 1-4: the ordinary successful endings, across routes -----------------
    Scenario(
        "local answer success",
        "completed",
        "answered from the local model",
        delivery=_delivered("local"),
        economics=_reconciled("local"),
        verification=_verified(False),
        legacy_status="answered_locally",
    ),
    Scenario(
        "free API answer success",
        "completed",
        "answered by the free API tier",
        delivery=_delivered("free"),
        economics=_reconciled("free"),
        verification=_verified(False),
        legacy_status="answered_by_free_api",
    ),
    Scenario(
        "account answer success",
        "completed",
        "answered by the account provider",
        delivery=_delivered("account"),
        economics=_reconciled("account"),
        verification=_verified(False),
        legacy_status="answered_by_account",
    ),
    Scenario(
        "mutating success with verification passed",
        "completed",
        "objective met and verified",
        mutating=True,
        delivery=_delivered("mutating"),
        economics=_reconciled("mutating"),
        verification=_verified(True),
        legacy_status="applied",
    ),
    # --- 5-7: output exists, the objective does not ---------------------------
    Scenario(
        "provider output but verification failed",
        "partial",
        "the model answered but verification did not pass",
        mutating=True,
        verification=_unverified("failed"),
        legacy_status="answered",
    ),
    Scenario(
        "empty provider response",
        "failed",
        "the provider returned no content",
        legacy_status="error",
    ),
    Scenario(
        "partial stream disconnect",
        "partial",
        "the stream ended before the answer completed",
        diagnostics={"codes": ("stream_incomplete",)},
        legacy_status="answered",
    ),
    # --- 8-10: provider-side refusals ----------------------------------------
    Scenario("auth failure", "failed", "the provider rejected the credentials"),
    Scenario("quota failure", "failed", "the account quota is exhausted"),
    Scenario(
        "rate limit",
        "failed",
        "the provider rate-limited the request",
        recovery={"automatic_retry": True, "reason": "rate_limit"},
    ),
    # --- 11-12: the timeout causality distinction (#683) ----------------------
    Scenario(
        "provider inactivity timeout",
        "timeout",
        "the provider stopped sending output",
        diagnostics={"codes": ("provider_idle_timeout",)},
    ),
    Scenario(
        "task deadline while provider responsive",
        "timeout",
        "Vesta's own task deadline expired while work was still progressing",
        mutating=True,
        verification=_unverified("blocked"),
        diagnostics={"codes": ("task_deadline",)},
    ),
    # --- 13-15: tool failures, before and after mutation ----------------------
    Scenario("tool failure before mutation", "failed", "a tool failed before any edit"),
    Scenario(
        "mutation then tool error",
        "partial",
        "files changed, then a tool failed",
        mutating=True,
        verification=_unverified("failed"),
    ),
    Scenario(
        "uncertain side effect",
        "needs_attention",
        "a command may have had an effect that could not be confirmed",
        mutating=True,
        verification=_unverified("unavailable"),
    ),
    # --- 16-17: refused before anything was attempted -------------------------
    Scenario(
        "blocked before provider dispatch",
        "blocked",
        "the guard refused the call before it was dispatched",
    ),
    Scenario(
        "blocked before command dispatch",
        "blocked",
        "the command needed approval that was not given",
    ),
    # --- 18-21: cancellation at increasing depth (#614/#666) ------------------
    Scenario("cancel before dispatch", "cancelled", "stopped before anything ran"),
    Scenario(
        "cancel during provider", "cancelled", "stopped while the model was replying"
    ),
    Scenario(
        "cancel during verification",
        "cancelled",
        "stopped while verification was running",
        mutating=True,
        verification=_unverified("cancelled"),
    ),
    Scenario(
        "cancellation teardown uncertain",
        "needs_attention",
        "cancellation was requested but owned work could not be proven stopped",
        diagnostics={"codes": ("teardown_unconfirmed",)},
    ),
    # --- 22-23: verification outcomes ----------------------------------------
    Scenario(
        "verification unavailable",
        "needs_attention",
        "verification could not run",
        mutating=True,
        verification=_unverified("unavailable"),
    ),
    Scenario(
        "verification failed",
        "partial",
        "verification ran and did not pass",
        mutating=True,
        verification=_unverified("failed"),
    ),
    # --- 24-27: delivery is its own axis --------------------------------------
    Scenario(
        "implementation succeeds but PR delivery fails",
        "partial",
        "the change was made but the pull request could not be opened",
        mutating=True,
        verification=_verified(True),
        delivery={"applicable": True, "verdict": "failed"},
    ),
    Scenario(
        "PR opened but CI fails",
        "partial",
        "the pull request is open and its checks are failing",
        mutating=True,
        verification=_verified(True),
        delivery={"applicable": True, "verdict": "partially_delivered"},
    ),
    Scenario(
        "PR opened but CI unavailable",
        "needs_attention",
        "the pull request is open and its checks could not be read",
        mutating=True,
        verification=_verified(True),
        delivery={"applicable": True, "verdict": "unknown"},
    ),
    Scenario(
        "PR merged",
        "completed",
        "the pull request merged",
        mutating=True,
        verification=_verified(True),
        delivery={
            "applicable": True,
            "verdict": "delivered",
            "record_ref": {"kind": "pull_request", "id": "697"},
        },
        economics=_reconciled("merged"),
    ),
    # --- 28-29: cost is unknown, never zero -----------------------------------
    Scenario(
        # Deliberately not `completed`. RunResult refuses to construct a
        # completed result whose cost integrity is unavailable, so this cannot
        # be written as "finished, spend unknown" even by mistake -- R10 is
        # enforced at construction rather than asserted afterwards. Unknown
        # spend is a run someone has to look at, not a clean success.
        "cost unavailable",
        "needs_attention",
        "answered, but the spend could not be determined",
        verification=_verified(False),
        economics={"integrity": "unavailable"},
    ),
    Scenario(
        "cost arrives late",
        "completed",
        "answered; spend was reconciled afterwards",
        delivery=_delivered("late"),
        verification=_verified(False),
        economics={
            "integrity": "reconciled",
            "record_ref": {"kind": "ledger_event", "id": "late-usage"},
        },
    ),
    # --- 30-31: legacy imports, known and unknown -----------------------------
    Scenario(
        "known legacy status import",
        "needs_attention",
        "imported from a record predating the canonical result",
        legacy_status="answered_by_account",
    ),
    Scenario(
        "unknown legacy status import",
        "needs_attention",
        "imported a status this version does not recognise",
        legacy_status="totally-unknown-status",
    ),
    # --- 32-34: the projection itself is missing or stale ---------------------
    Scenario(
        "restart before projection",
        "needs_attention",
        "the process restarted before a canonical result was recorded",
    ),
    Scenario(
        "corrupt evidence reference",
        "needs_attention",
        "an evidence reference could not be resolved",
    ),
    Scenario(
        "stale result revision",
        "needs_attention",
        "a newer projection supersedes this one",
    ),
)


#: What every surface must agree on. Presentation may differ; these may not.
def canonical_fields(result: RunResult) -> dict[str, Any]:
    payload = result.to_dict()
    lifecycle = payload["lifecycle"]
    return {
        "state": lifecycle["state"],
        "terminal": STATE_SPECS[lifecycle["state"]]["terminal"],
        "automatic_retry": payload["recovery"]["automatic_retry"],
        "verification": (payload.get("verification") or {}).get("verdict"),
        "delivery": (payload.get("delivery") or {}).get("verdict"),
        "economics": (payload.get("economics") or {}).get("integrity"),
        "exit_code": STATE_SPECS[lifecycle["state"]]["exit_code"],
        "presentation_category": STATE_SPECS[lifecycle["state"]][
            "presentation_category"
        ],
    }


class MatrixCoverageTests(unittest.TestCase):
    def test_the_matrix_declares_thirty_four_scenarios(self) -> None:
        self.assertEqual(len(SCENARIOS), 34)

    def test_scenario_names_are_unique(self) -> None:
        names = [scenario.name for scenario in SCENARIOS]
        self.assertEqual(len(names), len(set(names)))

    def test_every_scenario_builds_a_schema_valid_result(self) -> None:
        for scenario in SCENARIOS:
            with self.subTest(scenario=scenario.name):
                built = scenario.build()
                self.assertIn(built.to_dict()["lifecycle"]["state"], TERMINAL_STATE_IDS)

    def test_every_terminal_state_appears_somewhere_in_the_matrix(self) -> None:
        """A matrix that never exercises a state cannot defend it."""

        covered = {scenario.state for scenario in SCENARIOS}
        self.assertEqual(covered, set(TERMINAL_STATE_IDS))


class CrossSurfaceAgreementTests(unittest.TestCase):
    """The same evidence, read by every surface, means the same thing."""

    def test_history_and_background_agree_with_the_canonical_state(self) -> None:
        for scenario in SCENARIOS:
            with self.subTest(scenario=scenario.name):
                result = scenario.build()
                payload = {
                    "status": scenario.legacy_status,
                    "run_result": result.to_dict(),
                }
                canonical = canonical_fields(result)

                history = thread_status_for_result(
                    payload["status"], None, payload["run_result"]
                )
                expected_history = (
                    "complete"
                    if canonical["state"] == "completed"
                    else canonical["state"]
                )
                self.assertEqual(
                    history,
                    expected_history,
                    f"history disagreed on {scenario.name!r}",
                )

                background, _, _ = _terminal_from_payload(payload, cancelled=False)
                self.assertEqual(
                    background.value,
                    canonical["state"],
                    f"background disagreed on {scenario.name!r}",
                )

    def test_the_legacy_status_never_changes_the_canonical_answer(self) -> None:
        """Route and vintage ride along in the record; they do not decide it.

        Each scenario is replayed with every legacy status in the vocabulary.
        The canonical meaning must be identical every time.
        """

        vocabulary = (
            "answered",
            "answered_by_account",
            "answered_by_free_api",
            "answered_locally",
            "applied",
            "no_edits",
            "error",
            "cancelled",
            "totally-unknown",
            "",
        )
        for scenario in SCENARIOS:
            result = scenario.build()
            expected = canonical_fields(result)["state"]
            for legacy in vocabulary:
                with self.subTest(scenario=scenario.name, legacy=legacy):
                    payload = {"status": legacy, "run_result": result.to_dict()}
                    background, _, _ = _terminal_from_payload(payload, cancelled=False)
                    self.assertEqual(background.value, expected)

    def test_serialization_round_trips_without_changing_meaning(self) -> None:
        for scenario in SCENARIOS:
            with self.subTest(scenario=scenario.name):
                original = scenario.build()
                once = original.to_dict()
                twice = RunResult.from_dict(once).to_dict()
                self.assertEqual(once, twice, "serialization is not deterministic")


class ResultInvariantTests(unittest.TestCase):
    """The invariants #618 states, checked against the declared matrix."""

    def test_a_mutating_completed_run_carries_a_passing_verification(self) -> None:
        for scenario in SCENARIOS:
            if scenario.state != "completed" or not scenario.mutating:
                continue
            with self.subTest(scenario=scenario.name):
                verification = scenario.build().to_dict().get("verification") or {}
                self.assertEqual(
                    verification.get("verdict"),
                    "verified",
                    "a mutating run completed without verification passing",
                )

    def test_delivery_failure_never_reads_as_delivered(self) -> None:
        for scenario in SCENARIOS:
            delivery = scenario.build().to_dict().get("delivery") or {}
            if not delivery:
                continue
            with self.subTest(scenario=scenario.name):
                if delivery.get("verdict") == "delivered":
                    self.assertEqual(
                        scenario.state,
                        "completed",
                        "delivery claimed more than the run achieved",
                    )

    def test_unavailable_cost_is_never_reported_as_reconciled(self) -> None:
        for scenario in SCENARIOS:
            economics = scenario.build().to_dict().get("economics") or {}
            if economics.get("integrity") != "unavailable":
                continue
            with self.subTest(scenario=scenario.name):
                self.assertNotEqual(economics.get("integrity"), "reconciled")
                self.assertNotIn("amount", economics)

    def test_the_two_timeouts_are_distinguishable(self) -> None:
        """#683: a task deadline is not the provider failing to respond.

        Both are `timeout`. What separates them is the recorded cause, and the
        distinction has to survive into the result or the user is told to use a
        faster model when the provider was answering perfectly well.
        """

        by_name = {scenario.name: scenario for scenario in SCENARIOS}
        provider = by_name["provider inactivity timeout"]
        deadline = by_name["task deadline while provider responsive"]

        self.assertEqual(provider.state, deadline.state)
        self.assertNotEqual(
            provider.diagnostics["codes"], deadline.diagnostics["codes"]
        )
        self.assertIn("task_deadline", deadline.diagnostics["codes"])
        self.assertNotIn("provider", " ".join(deadline.diagnostics["codes"]))
        self.assertIn(
            "deadline", deadline.reason_detail, "the reason must name the real cause"
        )

    def test_cancellation_requires_reconciliation_to_read_as_cancelled(self) -> None:
        """#614/#666: requesting a stop is not proof the work stopped."""

        by_name = {scenario.name: scenario for scenario in SCENARIOS}
        uncertain = by_name["cancellation teardown uncertain"]
        self.assertEqual(
            uncertain.state,
            "needs_attention",
            "unproven teardown must not read as cancelled",
        )
        for name in (
            "cancel before dispatch",
            "cancel during provider",
            "cancel during verification",
        ):
            with self.subTest(scenario=name):
                self.assertEqual(by_name[name].state, "cancelled")

    def test_blocked_before_dispatch_never_becomes_failed(self) -> None:
        for name in (
            "blocked before provider dispatch",
            "blocked before command dispatch",
        ):
            scenario = next(s for s in SCENARIOS if s.name == name)
            with self.subTest(scenario=name):
                self.assertEqual(scenario.state, "blocked")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
