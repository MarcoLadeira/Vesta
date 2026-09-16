"""Tests for the per-turn execution guard (Task 6)."""

from __future__ import annotations

import threading
import unittest
from pathlib import Path

from vestahub.completion import CompletionState, ProviderBlockedReason
from vestahub.execution_guard import (
    ExecutionGuard,
    ExecutionGuardContext,
    GuardOutcome,
)

ROOT = Path("/tmp/vesta-guard-tests")


def gate_result(**overrides):
    base = {
        "decision": "allow",
        "allowed": True,
        "requires_confirmation": False,
        "denied": False,
        "panic": False,
        "reasons": ["Within budget and policy."],
    }
    base.update(overrides)
    return base


def fake_gate(result):
    def _gate(project_root, **kwargs):
        _gate.calls.append(kwargs)
        return result

    _gate.calls = []
    return _gate


def context(**overrides):
    fields = {"project_root": ROOT, "turn_index": 1}
    fields.update(overrides)
    return ExecutionGuardContext(**fields)


class ExecutionGuardTests(unittest.TestCase):
    def test_free_zero_cost_turn_is_allowed(self):
        guard = ExecutionGuard(gate=fake_gate(gate_result()))
        decision = guard.check(context(is_free=True, provider_type="free_api"))
        self.assertTrue(decision.allowed)
        self.assertIs(decision.outcome, GuardOutcome.ALLOW)

    def test_cancelled_before_dispatch(self):
        cancel = threading.Event()
        cancel.set()
        guard = ExecutionGuard(gate=fake_gate(gate_result()))
        decision = guard.check(context(cancel=cancel))
        self.assertIs(decision.outcome, GuardOutcome.CANCEL)
        self.assertIs(decision.to_completion_state(), CompletionState.CANCELLED)

    def test_cancel_is_checked_before_the_gate(self):
        cancel = threading.Event()
        cancel.set()
        gate = fake_gate(gate_result())
        ExecutionGuard(gate=gate).check(context(cancel=cancel))
        self.assertEqual(gate.calls, [])  # no spend decision attempted after cancel

    def test_panic_blocks_with_panic_reason(self):
        guard = ExecutionGuard(
            gate=fake_gate(
                gate_result(denied=True, panic=True, reasons=["Panic mode is ON"])
            )
        )
        decision = guard.check(context(provider_type="cloud", estimated_cost_usd=0.2))
        self.assertIs(decision.outcome, GuardOutcome.BLOCKED)
        self.assertIs(decision.blocked_reason, ProviderBlockedReason.PANIC)
        self.assertIs(decision.to_completion_state(), CompletionState.PROVIDER_BLOCKED)

    def test_daily_cap_blocks(self):
        guard = ExecutionGuard(
            gate=fake_gate(
                gate_result(denied=True, reasons=["Daily budget exceeded: ..."])
            )
        )
        decision = guard.check(context(provider_type="cloud", estimated_cost_usd=0.5))
        self.assertIs(decision.blocked_reason, ProviderBlockedReason.DAILY_CAP)

    def test_monthly_cap_blocks(self):
        guard = ExecutionGuard(
            gate=fake_gate(
                gate_result(denied=True, reasons=["Monthly budget exceeded: ..."])
            )
        )
        decision = guard.check(context(provider_type="cloud", estimated_cost_usd=0.5))
        self.assertIs(decision.blocked_reason, ProviderBlockedReason.MONTHLY_CAP)

    def test_per_task_policy_denial_maps_to_task_cap(self):
        guard = ExecutionGuard(
            gate=fake_gate(
                gate_result(
                    denied=True, reasons=["Policy denied this route: per-task budget"]
                )
            )
        )
        decision = guard.check(context(provider_type="cloud", estimated_cost_usd=0.9))
        self.assertIs(decision.blocked_reason, ProviderBlockedReason.TASK_CAP)

    def test_confirmation_needed_without_consent(self):
        guard = ExecutionGuard(
            gate=fake_gate(
                gate_result(
                    requires_confirmation=True, reasons=["Policy requires confirmation"]
                )
            )
        )
        decision = guard.check(
            context(provider_type="cloud", estimated_cost_usd=0.1, allow_cloud=False)
        )
        self.assertIs(decision.outcome, GuardOutcome.NEEDS_CONSENT)
        self.assertIs(decision.to_completion_state(), CompletionState.NEEDS_CONSENT)

    def test_confirmation_satisfied_by_prior_consent(self):
        guard = ExecutionGuard(
            gate=fake_gate(
                gate_result(
                    requires_confirmation=True, reasons=["Policy requires confirmation"]
                )
            )
        )
        decision = guard.check(
            context(provider_type="cloud", estimated_cost_usd=0.1, allow_cloud=True)
        )
        self.assertTrue(decision.allowed)

    def test_provider_auth_blocks_before_the_gate(self):
        gate = fake_gate(gate_result())
        guard = ExecutionGuard(gate=gate)
        decision = guard.check(context(provider_status={"auth": True}))
        self.assertIs(decision.blocked_reason, ProviderBlockedReason.AUTH)
        self.assertEqual(gate.calls, [])  # provider health short-circuits spend logic

    def test_provider_rate_quota_billing_signals(self):
        for signal, reason in (
            ("rate_limit", ProviderBlockedReason.RATE_LIMIT),
            ("quota", ProviderBlockedReason.QUOTA),
            ("billing", ProviderBlockedReason.BILLING),
        ):
            guard = ExecutionGuard(gate=fake_gate(gate_result()))
            decision = guard.check(context(provider_status={signal: True}))
            self.assertIs(decision.blocked_reason, reason, signal)

    def test_advisory_token_threshold_does_not_block(self):
        # A large estimated token count is advisory only: the guard passes it to
        # the gate but never blocks on it by itself.
        gate = fake_gate(gate_result())
        guard = ExecutionGuard(gate=gate)
        decision = guard.check(
            context(is_free=True, estimated_tokens=5_000_000, estimated_cost_usd=0.0)
        )
        self.assertTrue(decision.allowed)
        self.assertEqual(gate.calls[0]["estimated_tokens"], 5_000_000)

    def test_gate_receives_turn_cost_and_provider_type(self):
        gate = fake_gate(gate_result())
        ExecutionGuard(gate=gate).check(
            context(provider_type="account", tier="L3", estimated_cost_usd=0.31)
        )
        self.assertEqual(gate.calls[0]["provider_type"], "account")
        self.assertEqual(gate.calls[0]["tier"], "L3")
        self.assertEqual(gate.calls[0]["next_cost_usd"], 0.31)


if __name__ == "__main__":
    unittest.main()
