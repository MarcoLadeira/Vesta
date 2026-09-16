import { beforeAll, describe, expect, it } from "vitest";

// applyAppearance (app.js) applies density/reduced-motion/activity-copy to
// the document root. app.js is a classic browser script (no exports); a
// minimal window + document.documentElement stub makes it importable and
// callable in the node test environment. Exposed via window.__vesta.

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

let vesta;
let root;

beforeAll(async () => {
  globalThis.window = { addEventListener: () => {}, VestaIcons: { icon: () => "<svg/>" } };
  root = { classList: makeClassList(), dataset: {} };
  globalThis.document = { documentElement: root };
  window.document = globalThis.document;
  window.VestaChatComponents = (await import("../chat-components.js")).default;
  await import("../theme.js");
  await import("../app.js");
  vesta = globalThis.window.__vesta;
});

describe("applyAppearance: theme", () => {
  it("puts the saved theme on the root", () => {
    vesta.applyAppearance({ theme: "light" });
    expect(root.dataset.theme).toBe("light");
    expect(root.dataset.themePreference).toBe("light");
  });

  it("treats a payload without a theme (an older boot) as the default Viber Coder theme", () => {
    vesta.applyAppearance({ theme: "light" });
    vesta.applyAppearance({});
    expect(root.dataset.theme).toBe("viber-coder");
    expect(root.dataset.themePreference).toBe("viber-coder");
  });

  it("wears the Dark theme when it is chosen", () => {
    vesta.applyAppearance({ theme: "dark" });
    expect(root.dataset.theme).toBe("dark");
  });

  it("leaves the other appearance preferences to apply as before", () => {
    vesta.applyAppearance({ theme: "light", density: "compact", reducedMotion: "on" });
    expect(root.dataset.theme).toBe("light");
    expect(root.classList.contains("density-compact")).toBe(true);
    expect(root.dataset.motion).toBe("on");
    vesta.applyAppearance({ theme: "dark" });
  });
});

describe("applyAppearance: activity copy (drag-select the AI activity rail)", () => {
  it("leaves the rail selectable when the pref is missing (older saved prefs, or 'on')", () => {
    vesta.applyAppearance({});
    expect(root.classList.contains("activity-select-off")).toBe(false);
    vesta.applyAppearance({ activityCopy: "on" });
    expect(root.classList.contains("activity-select-off")).toBe(false);
  });

  it("only turns the rail unselectable on the explicit 'off' value", () => {
    vesta.applyAppearance({ activityCopy: "off" });
    expect(root.classList.contains("activity-select-off")).toBe(true);
  });

  it("turning it back on removes the class again (not add-only)", () => {
    vesta.applyAppearance({ activityCopy: "off" });
    expect(root.classList.contains("activity-select-off")).toBe(true);
    vesta.applyAppearance({ activityCopy: "on" });
    expect(root.classList.contains("activity-select-off")).toBe(false);
  });

  it("an unrecognized value is treated as on, not off", () => {
    vesta.applyAppearance({ activityCopy: "off" });
    vesta.applyAppearance({ activityCopy: "sometimes" });
    expect(root.classList.contains("activity-select-off")).toBe(false);
  });

  it("does not disturb density/reduced-motion applied in the same call", () => {
    vesta.applyAppearance({ density: "compact", reducedMotion: "on", activityCopy: "off", responseDensity: "detailed" });
    expect(root.classList.contains("density-compact")).toBe(true);
    expect(root.dataset.motion).toBe("on");
    expect(root.classList.contains("activity-select-off")).toBe(true);
    expect(root.dataset.responseDensity).toBe("detailed");
    expect(vesta.state.responseDensity).toBe("detailed");
  });

  it("accepts raw saved response_density and falls back to balanced", () => {
    vesta.applyAppearance({ response_density: "compact" });
    expect(root.dataset.responseDensity).toBe("compact");
    expect(root.classList.contains("response-density-compact")).toBe(true);
    vesta.applyAppearance({ responseDensity: "invalid" });
    expect(root.dataset.responseDensity).toBe("balanced");
    expect(root.classList.contains("response-density-compact")).toBe(false);
  });
});
