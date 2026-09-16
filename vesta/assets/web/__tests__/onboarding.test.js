import { describe, it, expect } from "vitest";

import VestaOnboarding from "../onboarding.js";

describe("onboarding gating (#250)", () => {
  it("shows only until the tour has been seen", () => {
    expect(VestaOnboarding.shouldShow({ prefs: { onboardingSeen: false } })).toBe(true);
    expect(VestaOnboarding.shouldShow({ prefs: { onboardingSeen: true } })).toBe(false);
    // Absent flag on a fresh profile -> show.
    expect(VestaOnboarding.shouldShow({ prefs: {} })).toBe(true);
    expect(VestaOnboarding.shouldShow({})).toBe(true);
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
    expect(VestaOnboarding.connectedCount(ctx)).toBe(2);
    expect(VestaOnboarding.connectedCount({ boot: {} })).toBe(0);
  });

  it("exposes a local suggested first task and three steps", () => {
    expect(VestaOnboarding.TOTAL_STEPS).toBe(3);
    expect(typeof VestaOnboarding.SUGGESTED_TASK).toBe("string");
    expect(VestaOnboarding.SUGGESTED_TASK.length).toBeGreaterThan(0);
  });

  it("names the project the first task will read (Bug 11 transparency)", () => {
    const ctx = {
      esc: (v) => String(v),
      boot: { workspace: { label: "MarcoLadeiraWebsite", dirty_paths: ["a", "b", "c", "d", "e"] } },
    };
    const target = VestaOnboarding.firstTaskTarget(ctx);
    expect(target).toContain("MarcoLadeiraWebsite");
    expect(target).toContain("5 uncommitted changes");
    expect(target).toContain("never edits");
  });

  it("keeps the first task opt-in: the primary CTA finishes, never sends (Bug 11)", () => {
    // A brand-new user reflexively clicking the emphasized primary button must
    // not run a task against their real work folder.
    expect(VestaOnboarding.lastStepPrimaryAction()).toBe("finish");
    expect(VestaOnboarding.lastStepPrimaryAction()).not.toBe("send");
  });
});
