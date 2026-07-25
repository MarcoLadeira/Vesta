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

  it("names the project the first task will read (Bug 11 transparency)", () => {
    const ctx = {
      esc: (v) => String(v),
      boot: { workspace: { label: "MarcoLadeiraWebsite", dirty_paths: ["a", "b", "c", "d", "e"] } },
    };
    const target = OPaiOnboarding.firstTaskTarget(ctx);
    expect(target).toContain("MarcoLadeiraWebsite");
    expect(target).toContain("5 uncommitted changes");
    expect(target).toContain("never edits");
  });

  it("keeps the first task opt-in: the primary CTA finishes, never sends (Bug 11)", () => {
    // A brand-new user reflexively clicking the emphasized primary button must
    // not run a task against their real work folder.
    expect(OPaiOnboarding.lastStepPrimaryAction()).toBe("finish");
    expect(OPaiOnboarding.lastStepPrimaryAction()).not.toBe("send");
  });
});
