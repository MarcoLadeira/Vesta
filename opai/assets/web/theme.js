/**
 * Theme: light, dark, or whatever the operating system is using.
 *
 * The whole mechanism is one attribute. design-tokens.css keys each palette on
 * [data-theme], so setting <html data-theme="light"> repaints every surface that
 * takes its colour from a token -- which the token lint makes every surface.
 * Nothing here knows what any component looks like, and nothing has to change
 * here when a new one is added.
 *
 * This module owns three things:
 *
 *   - resolving a preference to a theme ("system" becomes light or dark);
 *   - following the OS while the preference is "system", so a machine that
 *     switches at sunset takes the app with it;
 *   - the change itself. A theme switch is a whole-window repaint, and a
 *     repaint that lands in one frame reads as a flash, so the old look
 *     dissolves into the new one through a view transition: one snapshot
 *     cross-fading on the compositor, however much is on screen. Reduced
 *     motion, a hidden window and an engine without view transitions all get
 *     the plain switch instead.
 *
 * Anything that paints outside CSS -- the star canvas -- listens for
 * `opai:themechange` and re-reads its tokens.
 */
(function (global) {
  "use strict";

  var THEMES = ["light", "dark", "system"];
  var DEFAULT_THEME = "dark";
  var EVENT = "opai:themechange";

  var state = { preference: null, theme: null, watching: false, pending: false };

  function normalize(value) {
    return THEMES.indexOf(value) >= 0 ? value : DEFAULT_THEME;
  }

  function query(media) {
    return typeof global.matchMedia === "function" ? global.matchMedia(media) : null;
  }

  function systemPrefersLight() {
    var light = query("(prefers-color-scheme: light)");
    return !!(light && light.matches);
  }

  function resolve(preference, prefersLight) {
    var choice = normalize(preference);
    if (choice !== "system") return choice;
    var light = prefersLight === undefined ? systemPrefersLight() : !!prefersLight;
    return light ? "light" : "dark";
  }

  // Same precedence as the rest of the app: the Appearance override wins, and
  // "system" (no attribute) defers to the OS.
  function motionReduced(root) {
    var override = root && root.dataset ? root.dataset.motion : undefined;
    if (override === "on") return true;
    if (override === "off") return false;
    var reduce = query("(prefers-reduced-motion: reduce)");
    return !!(reduce && reduce.matches);
  }

  function announce() {
    if (typeof global.dispatchEvent !== "function" || typeof global.CustomEvent !== "function") return;
    global.dispatchEvent(new global.CustomEvent(EVENT, {
      detail: { theme: state.theme, preference: state.preference },
    }));
  }

  function watchSystem() {
    if (state.watching) return;
    var light = query("(prefers-color-scheme: light)");
    if (!light) return;
    state.watching = true;
    var onChange = function () {
      if (state.preference === "system") apply("system");
    };
    if (typeof light.addEventListener === "function") light.addEventListener("change", onChange);
    else if (typeof light.addListener === "function") light.addListener(onChange);
  }

  function apply(preference, options) {
    var doc = global.document;
    var root = doc && doc.documentElement;
    if (!root || !root.dataset) return null;
    var choice = normalize(preference);
    var theme = resolve(choice);
    var first = state.theme === null;
    state.preference = choice;
    root.dataset.themePreference = choice;
    watchSystem();

    // Always the latest wish, read when the change actually lands: a newer
    // apply() can arrive while a transition is still capturing the old frame.
    var land = function () {
      state.pending = false;
      root.dataset.theme = state.theme;
      announce();
    };
    if (theme === state.theme) {
      // Already the wish. Land it only if something else moved the attribute
      // and no transition is about to put it back.
      if (root.dataset.theme !== theme && !state.pending) land();
      return theme;
    }
    state.theme = theme;

    var animate =
      !first &&
      !(options && options.animate === false) &&
      typeof doc.startViewTransition === "function" &&
      !doc.hidden &&
      !motionReduced(root);
    if (!animate) {
      land();
      return theme;
    }
    state.pending = true;
    try {
      doc.startViewTransition(land);
    } catch (_error) {
      land();
    }
    return theme;
  }

  function current() {
    return { preference: state.preference, theme: state.theme };
  }

  var api = {
    THEMES: THEMES.slice(),
    DEFAULT_THEME: DEFAULT_THEME,
    EVENT: EVENT,
    normalize: normalize,
    resolve: resolve,
    apply: apply,
    current: current,
  };
  global.OPaiTheme = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : this);
