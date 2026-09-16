import { afterEach, describe, expect, it, vi } from "vitest";

// theme.js is a classic browser script that keeps its state in a closure, so
// every test loads a fresh copy against its own minimal window: a document root
// with a dataset, matchMedia queries the test controls, and an optional
// startViewTransition that records callbacks instead of running them, so a test
// can decide when a transition "lands".

function makeMedia(matches) {
  return {
    matches,
    handlers: [],
    addEventListener(type, handler) {
      if (type === "change") this.handlers.push(handler);
    },
    fire(next) {
      this.matches = next;
      this.handlers.forEach((handler) => handler({ matches: next }));
    },
  };
}

async function loadTheme({ viewTransitions = true, prefersLight = false, reduce = false, hidden = false } = {}) {
  const root = { dataset: {} };
  const transitions = [];
  const events = [];
  const document = { documentElement: root, hidden };
  if (viewTransitions) {
    document.startViewTransition = (land) => {
      transitions.push(land);
      return { finished: Promise.resolve() };
    };
  }
  const media = {
    "(prefers-color-scheme: light)": makeMedia(prefersLight),
    "(prefers-reduced-motion: reduce)": makeMedia(reduce),
  };
  class CustomEvent {
    constructor(type, init) {
      this.type = type;
      this.detail = init && init.detail;
    }
  }
  globalThis.window = {
    document,
    matchMedia: (query) => media[query],
    dispatchEvent: (event) => events.push(event),
    CustomEvent,
  };
  vi.resetModules();
  await import("../theme.js");
  const land = () => transitions.splice(0).forEach((callback) => callback());
  return { theme: globalThis.window.VestaTheme, root, document, media, transitions, events, land };
}

afterEach(() => {
  delete globalThis.window;
});

describe("theme preferences", () => {
  it("knows Light, Viber Coder, Dark, Vesta and System, and falls back to Viber Coder", async () => {
    const { theme } = await loadTheme();
    expect(theme.THEMES).toEqual(["light", "viber-coder", "dark", "vesta", "system"]);
    expect(theme.DEFAULT_THEME).toBe("viber-coder");
    expect(theme.normalize("light")).toBe("light");
    expect(theme.normalize("dark")).toBe("dark");
    expect(theme.normalize("vesta")).toBe("vesta");
    expect(theme.normalize("sepia")).toBe("viber-coder");
    expect(theme.normalize(undefined)).toBe("viber-coder");
  });

  it("resolves system to Light by day and Viber Coder by night, and nothing else", async () => {
    const { theme } = await loadTheme({ prefersLight: true });
    expect(theme.resolve("system")).toBe("light");
    expect(theme.resolve("system", false)).toBe("viber-coder");
    expect(theme.resolve("dark")).toBe("dark");
    expect(theme.resolve("vesta", false)).toBe("vesta");
    expect(theme.resolve("viber-coder", true)).toBe("viber-coder");
    expect(theme.resolve("light", false)).toBe("light");
  });
});

describe("applying a theme", () => {
  it("paints the first theme immediately, with no transition to fade from", async () => {
    const { theme, root, transitions } = await loadTheme();
    expect(theme.apply("light")).toBe("light");
    expect(root.dataset.theme).toBe("light");
    expect(root.dataset.themePreference).toBe("light");
    expect(transitions).toHaveLength(0);
  });

  it("cross-fades a later change: the attribute lands inside the view transition", async () => {
    const { theme, root, transitions, land } = await loadTheme();
    theme.apply("dark");
    theme.apply("light");
    expect(transitions).toHaveLength(1);
    expect(root.dataset.theme).toBe("dark");
    land();
    expect(root.dataset.theme).toBe("light");
    expect(theme.current()).toEqual({ preference: "light", theme: "light" });
  });

  it("does not start a second transition for the theme already on its way", async () => {
    const { theme, transitions } = await loadTheme();
    theme.apply("dark");
    theme.apply("light");
    theme.apply("light");
    expect(transitions).toHaveLength(1);
  });

  it("lands the newest wish even when an older transition lands last", async () => {
    const { theme, root, transitions, land } = await loadTheme();
    theme.apply("dark");
    theme.apply("light");
    expect(transitions).toHaveLength(1);
    theme.apply("dark", { animate: false });
    expect(root.dataset.theme).toBe("dark");
    land();
    expect(root.dataset.theme).toBe("dark");
  });

  it("holds every other transition off for the frame an instant switch lands in", async () => {
    const { theme, root } = await loadTheme();
    const frames = [];
    globalThis.window.requestAnimationFrame = (callback) => frames.push(callback);
    theme.apply("dark");
    expect(root.dataset.theme).toBe("dark");
    expect(root.dataset.themeSwitching).toBe("instant");
    frames.shift()();
    expect(root.dataset.themeSwitching).toBe("instant");
    frames.shift()();
    expect(root.dataset.themeSwitching).toBeUndefined();
  });

  it("does not hold transitions off for a cross-fade, which animates as one snapshot", async () => {
    const { theme, root, land } = await loadTheme();
    theme.apply("dark");
    delete root.dataset.themeSwitching;
    theme.apply("light");
    land();
    expect(root.dataset.theme).toBe("light");
    expect(root.dataset.themeSwitching).toBeUndefined();
  });

  it("switches instantly when motion is reduced by the Appearance override", async () => {
    const { theme, root, transitions } = await loadTheme();
    theme.apply("dark");
    root.dataset.motion = "on";
    theme.apply("light");
    expect(transitions).toHaveLength(0);
    expect(root.dataset.theme).toBe("light");
  });

  it("follows the OS reduced-motion setting unless the override turns motion on", async () => {
    const reduced = await loadTheme({ reduce: true });
    reduced.theme.apply("dark");
    reduced.theme.apply("light");
    expect(reduced.transitions).toHaveLength(0);
    expect(reduced.root.dataset.theme).toBe("light");

    const forced = await loadTheme({ reduce: true });
    forced.root.dataset.motion = "off";
    forced.theme.apply("dark");
    forced.theme.apply("light");
    expect(forced.transitions).toHaveLength(1);
  });

  it("switches instantly in a hidden window and in an engine without view transitions", async () => {
    const hidden = await loadTheme({ hidden: true });
    hidden.theme.apply("dark");
    hidden.theme.apply("light");
    expect(hidden.transitions).toHaveLength(0);
    expect(hidden.root.dataset.theme).toBe("light");

    const legacy = await loadTheme({ viewTransitions: false });
    legacy.theme.apply("dark");
    legacy.theme.apply("light");
    expect(legacy.root.dataset.theme).toBe("light");
  });

  it("falls back to an instant switch when the transition cannot start", async () => {
    const { theme, root, document } = await loadTheme();
    document.startViewTransition = () => {
      throw new Error("InvalidStateError");
    };
    theme.apply("dark");
    theme.apply("light");
    expect(root.dataset.theme).toBe("light");
  });

  it("announces every change so canvas-drawn surfaces can repaint", async () => {
    const { theme, events, land } = await loadTheme();
    theme.apply("dark");
    theme.apply("light");
    land();
    expect(events.map((event) => [event.type, event.detail])).toEqual([
      ["vesta:themechange", { theme: "dark", preference: "dark" }],
      ["vesta:themechange", { theme: "light", preference: "light" }],
    ]);
  });
});

describe("the system preference", () => {
  it("follows the operating system as it changes", async () => {
    const { theme, root, media, land } = await loadTheme({ prefersLight: false });
    theme.apply("system");
    expect(root.dataset.theme).toBe("viber-coder");
    expect(root.dataset.themePreference).toBe("system");
    media["(prefers-color-scheme: light)"].fire(true);
    land();
    expect(root.dataset.theme).toBe("light");
    media["(prefers-color-scheme: light)"].fire(false);
    land();
    expect(root.dataset.theme).toBe("viber-coder");
  });

  it("stops following the OS once an explicit theme is chosen", async () => {
    const { theme, root, media, land } = await loadTheme({ prefersLight: false });
    theme.apply("system");
    theme.apply("dark");
    media["(prefers-color-scheme: light)"].fire(true);
    land();
    expect(root.dataset.theme).toBe("dark");
    expect(media["(prefers-color-scheme: light)"].handlers).toHaveLength(1);
  });
});
