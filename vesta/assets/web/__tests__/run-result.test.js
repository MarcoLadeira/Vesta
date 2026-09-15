import { describe, expect, it } from "vitest";
import "../generated-lifecycle.js";
import VestaRunResult from "../run-result.js";

function canonical(state, reason = "Canonical terminal reason.") {
  const spec = globalThis.VestaLifecycle.stateSpecs[state];
  return {
    schema_version: globalThis.VestaLifecycle.schemaVersion,
    identity: {},
    lifecycle: {
      state,
      reason: "terminal_resolution",
      reason_detail: reason,
      final_transition_at: "2026-08-11T12:00:00Z",
      reconciliation: "reconciled",
    },
    provider: {},
    recovery: { automatic_retry: false, reason: "none" },
    verification: {},
    delivery: {},
    economics: {},
    authority: { mutating: false },
    diagnostics: { record_refs: [] },
    presentation: { label: spec.label, category: spec.presentation_category },
    compatibility: {
      state: "compatible",
      source_schema_version: globalThis.VestaLifecycle.schemaVersion,
      automatic_retry: false,
    },
  };
}

describe("RunResult browser presentation", () => {
  it("uses canonical state, reason, and label over conflicting legacy fields", () => {
    const item = VestaRunResult.fromResult({
      status: "answered",
      completion_verdict: {
        verdict: "completed",
        reason: "Legacy completion claim.",
        next_action: "Review the evidence.",
      },
      run_result: canonical("partial", "Verification remained incomplete."),
    });

    expect(item.state).toBe("partial");
    expect(item.label).toBe("Partially completed");
    expect(item.reason).toBe("Verification remained incomplete.");
    expect(item.nextAction).toBe("Review the evidence.");
    expect(item.canonical).toBe(true);
  });

  it("degrades an explicit malformed envelope instead of trusting legacy success", () => {
    const item = VestaRunResult.fromResult({
      completion_verdict: { verdict: "completed" },
      run_result: { schema_version: 1 },
    });

    expect(item.state).toBe("needs_attention");
    expect(item.label).toBe("Needs attention");
    expect(item.automaticRetry).toBe(false);
  });

  it("keeps a compatibility adapter for payloads without RunResult", () => {
    const item = VestaRunResult.fromResult({
      completion_verdict: { verdict: "timeout", reason: "Legacy timeout." },
    });

    expect(item.state).toBe("timeout");
    expect(item.reason).toBe("Legacy timeout.");
    expect(item.canonical).toBe(false);
  });
});
