import { test, expect } from "@playwright/test";

import { finishRequest, openApp, openNav, openTurnDetails, sendPrompt } from "./helpers/app.js";
import { auditThemeInPage } from "./helpers/theme-audit.js";


// Themes (Settings › Appearance): Light, Viber Coder -- OPai's original night
// sky and the default -- Dark, which is pitch black in Dracula's colours, and
// System. A theme is one attribute on <html>; these specs hold the promises
// that attribute makes: it is chosen and remembered like any other appearance
// setting, it changes softly, the star field shines in every theme, and it
// reaches every surface of the app rather than most of them.

const PALETTES = ["light", "viber-coder", "dark"];
const PALETTE_BG = { light: "rgb(238, 241, 246)", "viber-coder": "rgb(4, 5, 15)", dark: "rgb(0, 0, 0)" };

const theme = (page) => page.locator("html");
const picker = (page) => page.locator('[data-appearance-key="theme"]');
const option = (page, name) => picker(page).getByRole("radio", { name, exact: true });
const bodyBackground = (page) => page.evaluate(() => getComputedStyle(document.body).backgroundColor);
const token = (page, name) =>
  page.evaluate((property) => getComputedStyle(document.documentElement).getPropertyValue(property).trim(), name);

async function openAppearance(page) {
  await openNav(page, "Settings");
  await page.locator('.settings-rail-item[data-rail-target="appearance"]').click();
  await expect(picker(page)).toBeVisible();
}

test("Appearance offers Light, Viber Coder, Dark and System, and Viber Coder stays the default", async ({ page }) => {
  await openApp(page);
  await expect(theme(page)).toHaveAttribute("data-theme", "viber-coder");
  expect(await bodyBackground(page)).toBe(PALETTE_BG["viber-coder"]);
  await openAppearance(page);
  await expect(picker(page)).toHaveAttribute("role", "radiogroup");
  await expect(picker(page).getByRole("radio")).toHaveText(["Light", "Viber Coder", "Dark", "System"]);
  await expect(option(page, "Viber Coder")).toHaveAttribute("aria-checked", "true");
  await expect(page.locator("#settingsPage")).not.toContainText("not shipped");
});

test("choosing Light repaints the whole app, is saved, and holds across views", async ({ page }) => {
  await openApp(page);
  await openAppearance(page);
  await option(page, "Light").click();

  await expect(theme(page)).toHaveAttribute("data-theme", "light");
  await expect.poll(() => bodyBackground(page)).toBe(PALETTE_BG.light);
  await expect(option(page, "Light")).toHaveAttribute("aria-checked", "true");
  await expect.poll(() => page.evaluate(() => window.__mock.savedPrefs)).toContainEqual(["theme", "light"]);

  await openNav(page, "Chat");
  await expect(theme(page)).toHaveAttribute("data-theme", "light");
  await openAppearance(page);
  await expect(option(page, "Light")).toHaveAttribute("aria-checked", "true");
});

test("choosing Dark turns the lights all the way off, in Dracula's colours", async ({ page }) => {
  await openApp(page);
  await openAppearance(page);
  await option(page, "Dark").click();

  await expect(theme(page)).toHaveAttribute("data-theme", "dark");
  await expect.poll(() => bodyBackground(page)).toBe(PALETTE_BG.dark);
  expect(await token(page, "--accent")).toBe("#bd93f9");
  expect(await token(page, "--ink")).toBe("#f8f8f2");
  expect(await token(page, "--stage-dark")).toBe("#000000");
  await expect.poll(() => page.evaluate(() => window.__mock.savedPrefs)).toContainEqual(["theme", "dark"]);
  await expect(option(page, "Dark")).toHaveAttribute("aria-checked", "true");
});

test("a saved theme is worn from boot, before Settings ever opens", async ({ page }) => {
  await openApp(page, { boot: { prefs: { theme: "light" } } });
  await expect(theme(page)).toHaveAttribute("data-theme", "light");
  expect(await bodyBackground(page)).toBe(PALETTE_BG.light);
  expect(await token(page, "--ink")).toBe("#1b2236");
});

test("System follows the operating system between Light and Viber Coder, until a theme is chosen", async ({ page }) => {
  await page.emulateMedia({ colorScheme: "light" });
  await openApp(page, { boot: { prefs: { theme: "system" } } });
  await expect(theme(page)).toHaveAttribute("data-theme", "light");
  await page.emulateMedia({ colorScheme: "dark" });
  await expect(theme(page)).toHaveAttribute("data-theme", "viber-coder");
  await page.emulateMedia({ colorScheme: "light" });
  await expect(theme(page)).toHaveAttribute("data-theme", "light");

  await openAppearance(page);
  await expect(option(page, "System")).toHaveAttribute("aria-checked", "true");
  await option(page, "Dark").click();
  await expect(theme(page)).toHaveAttribute("data-theme", "dark");
  await page.emulateMedia({ colorScheme: "dark" });
  await page.emulateMedia({ colorScheme: "light" });
  await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  await expect(theme(page)).toHaveAttribute("data-theme", "dark");
});

test("a theme change cross-fades, and switches instantly under reduced motion", async ({ page }) => {
  await page.addInitScript(() => {
    window.__viewTransitions = 0;
    const original = Document.prototype.startViewTransition;
    if (!original) return;
    Document.prototype.startViewTransition = function (update) {
      window.__viewTransitions += 1;
      return original.call(this, update);
    };
  });
  await openApp(page);
  expect(await page.evaluate(() => typeof document.startViewTransition)).toBe("function");
  // Booting into a theme is not a change: nothing fades in.
  expect(await page.evaluate(() => window.__viewTransitions)).toBe(0);

  await openAppearance(page);
  await option(page, "Light").click();
  await expect(theme(page)).toHaveAttribute("data-theme", "light");
  expect(await page.evaluate(() => window.__viewTransitions)).toBe(1);

  await page.locator('[data-appearance-key="reduced_motion"] button[data-value="on"]').click();
  await option(page, "Dark").click();
  await expect(theme(page)).toHaveAttribute("data-theme", "dark");
  expect(await page.evaluate(() => window.__viewTransitions)).toBe(1);
});

test("the theme picker is a keyboard radio group", async ({ page }) => {
  await openApp(page);
  await openAppearance(page);
  const viber = option(page, "Viber Coder");
  await expect(viber).toHaveAttribute("tabindex", "0");
  await viber.focus();
  await viber.press("ArrowRight");
  await expect(option(page, "Dark")).toHaveAttribute("aria-checked", "true");
  await expect(option(page, "Dark")).toBeFocused();
  await expect(theme(page)).toHaveAttribute("data-theme", "dark");
  await option(page, "Dark").press("Home");
  await expect(option(page, "Light")).toBeFocused();
  await expect(theme(page)).toHaveAttribute("data-theme", "light");
  await option(page, "Light").press("End");
  await expect(option(page, "System")).toBeFocused();
  await expect(option(page, "System")).toHaveAttribute("aria-checked", "true");
});

test("Settings search takes “light mode”, “dracula” and “viber coder” straight to the theme picker", async ({ page }) => {
  await openApp(page);
  await openNav(page, "Settings");
  const search = page.locator("#settingsSearch");
  for (const query of ["dracula", "viber coder", "light mode"]) {
    await search.fill(query);
    await expect(page.locator("[data-settings-search-result]").first()).toContainText("Theme");
  }
  await page.locator("[data-settings-search-result]").first().click();
  await expect(page.locator("#set-sec-appearance")).toBeVisible();
  await expect(picker(page)).toBeInViewport();
});

test("the open option list of a select is legible in the light theme too", async ({ page }) => {
  await openApp(page, { boot: { prefs: { theme: "light" } } });
  const colours = await page.locator("#promptCat").evaluate(async (select) => {
    const option = select.querySelector("option") || select.appendChild(document.createElement("option"));
    const style = getComputedStyle(option);
    return { background: style.backgroundColor, foreground: style.color };
  });
  expect(colours.background).toBe("rgb(249, 250, 252)");
  expect(colours.foreground).toBe("rgb(27, 34, 54)");
});

/* ---------- the star field shines in every theme ----------
 *
 * The sky is a canvas, so no CSS reaches it: it reads its starlight and star
 * strength from the theme's tokens and repaints on a theme change. Measured
 * from the canvas pixels themselves -- the brightest still star against the
 * unlit room behind it -- so a theme whose stars vanish into its ground fails
 * here, whatever the tokens say.
 */

async function brightestStar(page) {
  return page.evaluate(() => {
    const canvas = document.getElementById("starfall");
    const { data } = canvas.getContext("2d").getImageData(0, 0, canvas.width, canvas.height);
    let best = { r: 0, g: 0, b: 0, a: 0 };
    for (let index = 3; index < data.length; index += 4) {
      if (data[index] > best.a) best = { r: data[index - 3], g: data[index - 2], b: data[index - 1], a: data[index] };
    }
    const probe = document.createElement("canvas").getContext("2d");
    probe.fillStyle = getComputedStyle(document.querySelector(".main")).backgroundColor;
    probe.fillRect(0, 0, 1, 1);
    const [r, g, b] = probe.getImageData(0, 0, 1, 1).data;
    const stage = { r, g, b };
    const alpha = best.a / 255;
    const star = { r: best.r * alpha + stage.r * (1 - alpha), g: best.g * alpha + stage.g * (1 - alpha), b: best.b * alpha + stage.b * (1 - alpha) };
    const luminance = ({ r: red, g: green, b: blue }) => {
      const linear = (value) => {
        const channel = value / 255;
        return channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4;
      };
      return 0.2126 * linear(red) + 0.7152 * linear(green) + 0.0722 * linear(blue);
    };
    const [light, dark] = [luminance(star), luminance(stage)].sort((x, y) => y - x);
    const sky = getComputedStyle(canvas);
    return {
      contrast: (light + 0.05) / (dark + 0.05),
      colour: [best.r, best.g, best.b],
      starlight: sky.getPropertyValue("--space-star").trim(),
      showing: sky.opacity === "1" && sky.visibility === "visible",
    };
  });
}

const near = (actual, expected) => actual.every((channel, index) => Math.abs(channel - expected[index]) <= 6);
const channels = (value) => value.split(",").map((part) => Number(part.trim()));

for (const palette of PALETTES) {
  test(`the star field shines in the ${palette} theme`, async ({ page }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await openApp(page, { boot: { prefs: { theme: palette } } });
    await expect(page.locator("#app")).toHaveAttribute("data-stage", "dark");
    await expect.poll(async () => (await brightestStar(page)).showing).toBe(true);
    const sky = await brightestStar(page);
    expect(near(sky.colour, channels(sky.starlight)), `${palette}: stars drawn in ${sky.colour}, token ${sky.starlight}`).toBe(true);
    // Viber Coder's own brightest stars measure about 2.3:1 against its room.
    expect(sky.contrast, `${palette}: brightest star contrast`).toBeGreaterThan(1.8);
  });
}

test("a theme change repaints the stars in the new theme's starlight", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await openApp(page);
  for (const [name, palette] of [["Light", "light"], ["Dark", "dark"], ["Viber Coder", "viber-coder"]]) {
    await page.evaluate((next) => window.__opai.applyAppearance({ theme: next }), palette);
    await expect(theme(page)).toHaveAttribute("data-theme", palette);
    await expect
      .poll(async () => {
        const sky = await brightestStar(page);
        return near(sky.colour, channels(sky.starlight)) && sky.contrast > 1.8;
      }, { message: `${name} stars` })
      .toBe(true);
  }
});

/* ---------- the whole app wears the theme ----------
 *
 * The audit measures what reaches the screen in each state of the app: text
 * against the background really behind it, and neutral surfaces of the wrong
 * polarity. A component added later that bypasses the palette -- a hard-coded
 * style attribute, a colour computed in a script -- fails here.
 */

// The floor each palette is held to. Text that ignores the theme lands near
// 1:1 -- dark ink on a dark well, white on white -- so every floor catches a
// bypass. Light and Dark were designed alongside this audit and meet 3:1
// everywhere; Viber Coder keeps its existing look, whose faintest labels
// (secondary text in the composer's menus) measure about 2.4:1, and is held
// there rather than restyled.
const MIN_CONTRAST = { light: 3, "viber-coder": 2.2, dark: 3 };

async function audit(page) {
  const palette = await page.evaluate(() => document.documentElement.dataset.theme || "viber-coder");
  return page.evaluate(auditThemeInPage, { minContrast: MIN_CONTRAST[palette] });
}

async function expectWorn(page, state) {
  const result = await audit(page);
  expect.soft(result.lowContrast, `${result.theme}: low-contrast text in ${state}`).toEqual([]);
  expect.soft(result.wrongSurfaces, `${result.theme}: wrong-polarity surfaces in ${state}`).toEqual([]);
}

test("the audit catches a component that ignores the theme", async ({ page }) => {
  await openApp(page, { boot: { prefs: { theme: "light" } } });
  await expectWorn(page, "the empty chat");
  await page.evaluate(() => {
    const island = document.createElement("div");
    island.className = "unthemed-island";
    island.style.cssText = "position:fixed;left:40px;top:120px;width:220px;height:80px;background:#090b10;color:#1b2236";
    island.textContent = "Hard-coded for a night theme";
    document.body.appendChild(island);
  });
  const result = await audit(page);
  expect(result.lowContrast.map((item) => item.element)).toContain("div.unthemed-island");
  expect(result.wrongSurfaces.map((item) => item.element)).toContain("div.unthemed-island");
});

function richAnswer() {
  return {
    answer: [
      "# Fixed the updater regression",
      "",
      "The listener stays stable. See [the notes](https://example.com) and `renderUpdateState`.",
      "",
      "> Quoted context from the issue.",
      "",
      "| Check | Result |",
      "| --- | --- |",
      "| Focused web tests | Passed |",
      "",
      "```javascript",
      "const unsubscribe = updater.onStateChange(renderUpdateState);",
      "```",
    ].join("\n"),
    changed_files: [" M opai/assets/web/app.js", " A opai/assets/web/theme.js"],
    presentation: {
      schema_version: 1,
      run: { state: "completed", label: "Ready for review", reason: "Focused verification passed.", next_action: "Review the changes." },
      evidence: { verification: { applicable: true, verdict: "verified" } },
      tests: { status: "passed", passed: 79, failed: 0, skipped: 0 },
      changes: { summary: { files: 2, additions: 4, deletions: 1 } },
      activity: [{ phase: "test", status: "completed", message: "Focused checks passed" }],
    },
    verification_manifest: {
      checks: [{
        check_id: "web", kind: "unit", requirement: "Run focused web checks", status: "passed",
        attempts: [{ index: 1, status: "passed", command: ["npm", "test"], exit_status: 0, output_summary: "All passed.", teardown_verified: true }],
      }],
    },
    workflow: {
      mode: "implement", phase: "completed",
      diff_review: {
        summary: { files: 2, pending: 0, additions: 4, deletions: 1 },
        files: [
          {
            path: "opai/assets/web/app.js", decision: "approved", additions: 3, deletions: 1,
            hunks: [{ old_start: 10, old_count: 2, new_start: 10, new_count: 4, heading: "applyAppearance", lines: [" const p = prefs || {};", "-const theme = null;", "+const theme = p.theme;", "+applyTheme(theme);"] }],
          },
          { path: "opai/assets/web/theme.js", decision: "approved", additions: 1, deletions: 0, hunks: [] },
        ],
      },
    },
  };
}

/* ---------- how the new themes look ----------
 *
 * Reviewed baselines of Light and Dark on their busiest surfaces. The sky
 * canvas is hidden because its stars are placed at random; the star tests
 * above measure it instead.
 */

const SHOT = { animations: "disabled", caret: "hide", maxDiffPixels: 200 };

async function bootIn(page, palette, viewport, overrides = {}) {
  await page.setViewportSize(viewport);
  await page.emulateMedia({ reducedMotion: "reduce" });
  await openApp(page, { ...overrides, boot: { ...(overrides.boot || {}), prefs: { theme: palette, ...((overrides.boot || {}).prefs || {}) } } });
  await page.addStyleTag({ content: "#starfall { display: none !important; }" });
  await page.evaluate(() => document.fonts.ready);
}

for (const palette of ["light", "dark"]) {
  test(`${palette} theme gallery: a finished turn`, async ({ page }) => {
    await bootIn(page, palette, { width: 1440, height: 900 });
    const id = await sendPrompt(page, "Fix the updater");
    await finishRequest(page, id, richAnswer());
    await page.locator("#chatScroll").evaluate((element) => { element.scrollTop = 0; });
    await expect(page.locator("#view-chat")).toHaveScreenshot(`${palette}-chat-finished-turn.png`, SHOT);
  });

  test(`${palette} theme gallery: Settings`, async ({ page }) => {
    await bootIn(page, palette, { width: 1440, height: 900 });
    await openNav(page, "Settings");
    for (const id of ["appearance", "connections", "safety"]) {
      await page.locator(`.settings-rail-item[data-rail-target="${id}"]`).click();
      await expect(page.locator(`#set-sec-${id}`)).toBeVisible();
      await expect(page.locator("#view-settings")).toHaveScreenshot(`${palette}-settings-${id}.png`, SHOT);
    }
  });
}

test("light theme gallery: phone Appearance", async ({ page }) => {
  await bootIn(page, "light", { width: 390, height: 844 });
  await openNav(page, "Settings");
  await page.locator('.settings-rail-item[data-rail-target="appearance"]').click();
  await expect(picker(page)).toBeVisible();
  await expect(page.locator("#view-settings")).toHaveScreenshot("light-settings-phone-appearance.png", SHOT);
});

for (const palette of PALETTES) {
  test.describe(`${palette} theme`, () => {
    test.beforeEach(async ({ page }) => {
      await page.setViewportSize({ width: 1440, height: 900 });
      await page.emulateMedia({ reducedMotion: "reduce" });
    });

    test(`every chat state wears the ${palette} theme`, async ({ page }) => {
      await openApp(page, { boot: { prefs: { theme: palette, responseDensity: "detailed" } } });
      await expectWorn(page, "the empty chat");

      const working = await sendPrompt(page, "Fix the updater");
      await expectWorn(page, "a running turn");
      await finishRequest(page, working, richAnswer());
      await openTurnDetails(page);
      await expectWorn(page, "a finished turn with its evidence open");

      for (const [button, state] of [["#modelBtn", "the model menu"], ["#modeBtn", "the mode menu"], ["#ctxBtn", "the context menu"], ["#moreBtn", "the composer menu"]]) {
        await page.locator(button).click();
        await expectWorn(page, state);
        await page.keyboard.press("Escape");
      }
      await page.locator("#wsSwitch").click();
      await expectWorn(page, "the workspace menu");
      await page.keyboard.press("Escape");

      const failing = await sendPrompt(page, "Try again");
      await finishRequest(page, failing, { status: "error", answer: "The provider returned a server error." });
      await expect(page.locator(".error-card")).toBeVisible();
      await expectWorn(page, "a failed turn");
    });

    test(`every destination wears the ${palette} theme`, async ({ page }) => {
      await openApp(page, { boot: { prefs: { theme: palette } } });
      for (const label of ["Prompt Library", "Money Saved"]) {
        await openNav(page, label);
        await expectWorn(page, label);
      }
      await openNav(page, "Settings");
      for (const id of ["general", "models", "connections", "usage", "safety", "appearance", "advanced"]) {
        await page.locator(`.settings-rail-item[data-rail-target="${id}"]`).click();
        await expect(page.locator(`#set-sec-${id}`)).toBeVisible();
        await expectWorn(page, `Settings › ${id}`);
      }
    });

    test(`overlays and approvals wear the ${palette} theme`, async ({ page }) => {
      await openApp(page, {
        boot: { prefs: { theme: palette, onboardingSeen: false } },
        toolResponses: { panic: { title: "Panic mode", text: "Switch to local-only routing?", needs_confirm: true, apply: "panic" } },
      });
      await expect(page.locator(".ob-card")).toBeVisible();
      await expectWorn(page, "the first-run tour");
      await page.locator(".ob-close").click();

      await page.keyboard.press("Control+k");
      await expect(page.locator("#palette")).toHaveClass(/open/);
      await expectWorn(page, "the command palette");
      await page.keyboard.press("Escape");

      await openNav(page, "Settings");
      await page.locator('.settings-rail-item[data-rail-target="safety"]').click();
      await page.getByRole("button", { name: "Use local only" }).click();
      await expect(page.getByRole("group", { name: "Approval required" })).toBeVisible();
      await expectWorn(page, "an approval card");
    });
  });
}
