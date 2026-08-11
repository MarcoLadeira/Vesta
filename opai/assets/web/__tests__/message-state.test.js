import { describe, expect, it } from "vitest";
import "../generated-lifecycle.js";
import "../run-result.js";
import OPaiMessageState from "../message-state.js";

const { beginRequest, canApply, fromBackendStatus, transition } = OPaiMessageState;

describe("message terminal truth", () => {
  function canonical(state, reason = "Canonical terminal reason.") {
    const spec = globalThis.OPaiLifecycle.stateSpecs[state];
    return {
      schema_version: globalThis.OPaiLifecycle.schemaVersion,
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
        source_schema_version: globalThis.OPaiLifecycle.schemaVersion,
        automatic_retry: false,
      },
    };
  }

  it("rejects completed after failed", () => {
    const failed = transition({ requestId: "r1", status: "streaming" }, "failed");

    expect(transition(failed, "completed").status).toBe("failed");
  });

  it("falls back to legacy buckets when no verdict is present", () => {
    expect(fromBackendStatus("failed")).toBe("failed");
    expect(fromBackendStatus("answered")).toBe("completed");
    expect(fromBackendStatus("cancelled")).toBe("cancelled");
  });

  it("degrades unknown backend truth instead of inventing a failure", () => {
    expect(fromBackendStatus("account_error")).toBe("needs_attention");
    expect(fromBackendStatus("totally_new_status")).toBe("needs_attention");
  });

  it("normalizes unknown message states to the incompatible terminal", () => {
    const unknown = { requestId: "r1", status: "future_state" };
    expect(canApply(unknown, "r1")).toBe(false);
    expect(transition(unknown, "running").status).toBe("needs_attention");
    expect(transition({ requestId: "r1", status: "running" }, "future_state").status).toBe("needs_attention");
  });

  it("honors the completion verdict over the legacy status (#402)", () => {
    // The exact leak: a legacy "answered" status whose verdict is not completed
    // must render its honest outcome, never a flat "failed" or a false success.
    expect(fromBackendStatus("answered", { verdict: "partial" })).toBe("partial");
    expect(fromBackendStatus("answered", { verdict: "blocked" })).toBe("blocked");
    expect(fromBackendStatus("failed", { verdict: "timeout" })).toBe("timeout");
    expect(fromBackendStatus("failed", { verdict: "blocked" })).toBe("blocked");
    // A bare verdict string is accepted too.
    expect(fromBackendStatus("answered", "completed")).toBe("completed");
  });

  it("honors RunResult over conflicting legacy terminal claims (#618)", () => {
    expect(
      fromBackendStatus("answered", { verdict: "completed" }, canonical("partial")),
    ).toBe("partial");
    expect(
      fromBackendStatus("failed", { verdict: "failed" }, canonical("cancelled")),
    ).toBe("cancelled");
  });

  it("fails closed when an explicit RunResult is malformed", () => {
    expect(fromBackendStatus("answered", { verdict: "completed" }, {})).toBe("needs_attention");
  });

  it("treats partial/blocked/timeout as distinct terminal states", () => {
    for (const end of ["partial", "blocked", "timeout"]) {
      const done = transition({ requestId: "r1", status: "streaming" }, end);
      expect(done.status).toBe(end);
      // Terminal: a late reply must not mutate it, and completed cannot follow.
      expect(canApply(done, "r1")).toBe(false);
      expect(transition(done, "completed").status).toBe(end);
      // A retry is a new request, never an outbound edge from a terminal.
      expect(transition(done, "retrying").status).toBe(end);
    }
  });

  it("passes through the transient verifying state before a verdict", () => {
    const verifying = transition({ requestId: "r1", status: "streaming" }, "verifying");
    expect(verifying.status).toBe("verifying");
    // verifying is not terminal — the real verdict still applies.
    expect(canApply(verifying, "r1")).toBe(true);
    expect(transition(verifying, "partial").status).toBe("partial");
  });

  it("uses the generated repair transition", () => {
    expect(transition({ requestId: "r1", status: "verifying" }, "running").status).toBe("running");
  });

  it("recognizes the generated degraded terminal", () => {
    const degraded = transition({ requestId: "r1", status: "running" }, "needs_attention");
    expect(degraded.status).toBe("needs_attention");
    expect(canApply(degraded, "r1")).toBe(false);
  });

  it("keeps retry identity separate from the failed request", () => {
    const failed = { requestId: "old", status: "failed" };
    const retry = beginRequest("new", { retryOf: failed.requestId });

    expect(retry.status).toBe("retrying");
    expect(retry.retryOf).toBe("old");
    expect(canApply(retry, "old")).toBe(false);
    expect(canApply(retry, "new")).toBe(true);
  });

  it("does not let late output mutate a cancelled request", () => {
    const cancelled = transition(beginRequest("r1"), "cancelled");

    expect(canApply(cancelled, "r1")).toBe(false);
  });
});
