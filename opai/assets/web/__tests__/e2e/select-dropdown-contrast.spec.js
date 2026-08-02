import { test, expect } from "@playwright/test";

import { openApp, openSettings } from "./helpers/app.js";


// Regression: every <select>'s closed-box text is styled for the dark theme,
// but the *open* option list is rendered by the engine with its own default
// background (white in Chromium) unless <option> is explicitly styled — so
// every dropdown in the app rendered near-illegible pale-gray/near-white text
// on a white popup once opened.
//
// The popup listbox is engine-native-rendered, so its pixels are not stable
// between local Chromium and the hosted Windows Chromium image even when both
// render the same dark, legible menu. Keep the cross-host regression contract
// at the CSS boundary: option colours must resolve to a dark surface with WCAG
// AA-or-better contrast instead of relying on host-specific popup geometry.

function relativeLuminance(rgb) {
  const channels = rgb.match(/\d+(?:\.\d+)?/g)?.slice(0, 3).map(Number);
  if (!channels || channels.length !== 3) throw new Error(`Expected resolved RGB colour, received ${rgb}`);
  const linear = channels.map((channel) => {
    const normalized = channel / 255;
    return normalized <= 0.04045
      ? normalized / 12.92
      : ((normalized + 0.055) / 1.055) ** 2.4;
  });
  return (0.2126 * linear[0]) + (0.7152 * linear[1]) + (0.0722 * linear[2]);
}

function contrastRatio(first, second) {
  const [lighter, darker] = [first, second].sort((a, b) => b - a);
  return (lighter + 0.05) / (darker + 0.05);
}

test("the Default model dropdown's open option list is legible (dark popup, not a native white one)", async ({ page }) => {
  await page.setViewportSize({ width: 900, height: 700 });
  await openApp(page);
  await openSettings(page, "models");
  const select = page.locator('select[data-default-pref="default_model"]');
  const colours = await select.locator("option").first().evaluate((option) => {
    const style = getComputedStyle(option);
    return { background: style.backgroundColor, foreground: style.color };
  });
  const background = relativeLuminance(colours.background);
  const foreground = relativeLuminance(colours.foreground);
  expect(background).toBeLessThan(foreground);
  expect(contrastRatio(background, foreground)).toBeGreaterThanOrEqual(4.5);
});
