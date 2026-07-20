import { describe, expect, it } from "vitest";
import OPaiMessageState from "../message-state.js";

const { beginRequest, canApply, fromBackendStatus, transition } = OPaiMessageState;

describe("message terminal truth", () => {
  it("rejects completed after failed", () => {
    const failed = transition({ requestId: "r1", status: "streaming" }, "failed");

    expect(transition(failed, "completed").status).toBe("failed");
  });

  it("falls back to legacy buckets when no verdict is present", () => {
    expect(fromBackendStatus("failed")).toBe("failed");
    expect(fromBackendStatus("account_error")).toBe("failed");
    expect(fromBackendStatus("account_not_connected")).toBe("failed");
    expect(fromBackendStatus("answered")).toBe("completed");
    expect(fromBackendStatus("cancelled")).toBe("cancelled");
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

  it("treats partial/blocked/timeout as distinct terminal states", () => {
    for (const end of ["partial", "blocked", "timeout"]) {
      const done = transition({ requestId: "r1", status: "streaming" }, end);
      expect(done.status).toBe(end);
      // Terminal: a late reply must not mutate it, and completed cannot follow.
      expect(canApply(done, "r1")).toBe(false);
      expect(transition(done, "completed").status).toBe(end);
      // But a retry is offered from each of them.
      expect(transition(done, "retrying").status).toBe("retrying");
    }
  });

  it("passes through the transient verifying state before a verdict", () => {
    const verifying = transition({ requestId: "r1", status: "streaming" }, "verifying");
    expect(verifying.status).toBe("verifying");
    // verifying is not terminal — the real verdict still applies.
    expect(canApply(verifying, "r1")).toBe(true);
    expect(transition(verifying, "partial").status).toBe("partial");
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
