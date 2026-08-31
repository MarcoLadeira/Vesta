import { beforeAll, describe, expect, it } from "vitest";

// applyAppearance (app.js) applies density/reduced-motion/activity-copy to
// the document root. app.js is a classic browser script (no exports); a
// minimal window + document.documentElement stub makes it importable and
// callable in the node test environment. Exposed via window.__opai.

function makeClassList(initial) {
  const set = new Set(initial || []);
  return {
    toggle(name, force) {
      const shouldHave = force === undefined ? !set.has(name) : !!force;
      if (shouldHave) set.add(name);
      else set.delete(name);
      return shouldHave;
    },
    contains(name) {
      return set.has(name);
    },
  };
}

let opai;
let root;

beforeAll(async () => {
  globalThis.window = { addEventListener: () => {}, OPaiIcons: { icon: () => "<svg/>" } };
  root = { classList: makeClassList(), dataset: {} };
  globalThis.document = { documentElement: root };
  window.OPaiChatComponents = (await import("../chat-components.js")).default;
  await import("../app.js");
  opai = globalThis.window.__opai;
});

describe("applyAppearance: activity copy (drag-select the AI activity rail)", () => {
  it("leaves the rail selectable when the pref is missing (older saved prefs, or 'on')", () => {
    opai.applyAppearance({});
    expect(root.classList.contains("activity-select-off")).toBe(false);
    opai.applyAppearance({ activityCopy: "on" });
    expect(root.classList.contains("activity-select-off")).toBe(false);
  });

  it("only turns the rail unselectable on the explicit 'off' value", () => {
    opai.applyAppearance({ activityCopy: "off" });
    expect(root.classList.contains("activity-select-off")).toBe(true);
  });

  it("turning it back on removes the class again (not add-only)", () => {
    opai.applyAppearance({ activityCopy: "off" });
    expect(root.classList.contains("activity-select-off")).toBe(true);
    opai.applyAppearance({ activityCopy: "on" });
    expect(root.classList.contains("activity-select-off")).toBe(false);
  });

  it("an unrecognized value is treated as on, not off", () => {
    opai.applyAppearance({ activityCopy: "off" });
    opai.applyAppearance({ activityCopy: "sometimes" });
    expect(root.classList.contains("activity-select-off")).toBe(false);
  });

  it("does not disturb density/reduced-motion applied in the same call", () => {
    opai.applyAppearance({ density: "compact", reducedMotion: "on", activityCopy: "off", responseDensity: "detailed" });
    expect(root.classList.contains("density-compact")).toBe(true);
    expect(root.dataset.motion).toBe("on");
    expect(root.classList.contains("activity-select-off")).toBe(true);
    expect(root.dataset.responseDensity).toBe("detailed");
    expect(opai.state.responseDensity).toBe("detailed");
  });

  it("accepts raw saved response_density and falls back to balanced", () => {
    opai.applyAppearance({ response_density: "compact" });
    expect(root.dataset.responseDensity).toBe("compact");
    expect(root.classList.contains("response-density-compact")).toBe(true);
    opai.applyAppearance({ responseDensity: "invalid" });
    expect(root.dataset.responseDensity).toBe("balanced");
    expect(root.classList.contains("response-density-compact")).toBe(false);
  });
});
