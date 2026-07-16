import { describe, it, expect } from "vitest";

import OPaiOnboarding from "../onboarding.js";

describe("onboarding gating (#250)", () => {
  it("shows only until the tour has been seen", () => {
    expect(OPaiOnboarding.shouldShow({ prefs: { onboardingSeen: false } })).toBe(true);
    expect(OPaiOnboarding.shouldShow({ prefs: { onboardingSeen: true } })).toBe(false);
    // Absent flag on a fresh profile -> show.
    expect(OPaiOnboarding.shouldShow({ prefs: {} })).toBe(true);
    expect(OPaiOnboarding.shouldShow({})).toBe(true);
  });

  it("counts connected providers from accounts", () => {
    const ctx = {
      boot: {
        accounts: [
          { id: "claude", connected: true },
          { id: "codex", authenticated: true },
          { id: "copilot", connected: false },
        ],
      },
    };
    expect(OPaiOnboarding.connectedCount(ctx)).toBe(2);
    expect(OPaiOnboarding.connectedCount({ boot: {} })).toBe(0);
  });

  it("exposes a local suggested first task and three steps", () => {
    expect(OPaiOnboarding.TOTAL_STEPS).toBe(3);
    expect(typeof OPaiOnboarding.SUGGESTED_TASK).toBe("string");
    expect(OPaiOnboarding.SUGGESTED_TASK.length).toBeGreaterThan(0);
  });
});
