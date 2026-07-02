import { describe, expect, it } from "vitest";
import OPaiMessageState from "../message-state.js";

const { beginRequest, canApply, fromBackendStatus, transition } = OPaiMessageState;

describe("message terminal truth", () => {
  it("rejects completed after failed", () => {
    const failed = transition({ requestId: "r1", status: "streaming" }, "failed");

    expect(transition(failed, "completed").status).toBe("failed");
  });

  it("maps every non-answer backend status to failed", () => {
    expect(fromBackendStatus("failed")).toBe("failed");
    expect(fromBackendStatus("account_error")).toBe("failed");
    expect(fromBackendStatus("account_not_connected")).toBe("failed");
    expect(fromBackendStatus("answered")).toBe("completed");
    expect(fromBackendStatus("cancelled")).toBe("cancelled");
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
