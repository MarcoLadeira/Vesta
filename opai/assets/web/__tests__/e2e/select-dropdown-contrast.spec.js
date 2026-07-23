import { test, expect } from "@playwright/test";

import { openApp, openSettings } from "./helpers/app.js";


// Regression: every <select>'s closed-box text is styled for the dark theme,
// but the *open* option list is rendered by the engine with its own default
// background (white in Chromium) unless <option> is explicitly styled — so
// every dropdown in the app rendered near-illegible pale-gray/near-white text
// on a white popup once opened.
//
// getComputedStyle on <option> does not reflect this: the popup listbox is
// engine-native-rendered, not a normal CSS box, so computed styles report the
// declared values even when the real bug is present (verified empirically —
// that check still "passed" with the buggy CSS reverted). A real screenshot
// is the only reliable way to catch this — and it must be a full *page*
// screenshot, not one scoped to a container locator: the open option popup
// composites as its own overlay layer above the page, outside any element's
// paint bounds, so `expect(locator).toHaveScreenshot()` misses it entirely
// (verified: that variant also "passed" with the bug present). This baseline
// was captured with the fix in place, so a background/color regression fails
// the diff.

test("the Default model dropdown's open option list is legible (dark popup, not a native white one)", async ({ page }) => {
  await page.setViewportSize({ width: 900, height: 700 });
  await openApp(page);
  await openSettings(page, "models");
  await page.evaluate(() => document.fonts.ready);
  const select = page.locator('select[data-default-pref="default_model"]');
  await select.click();
  await page.waitForTimeout(150);
  await expect(page).toHaveScreenshot("default-model-select-open.png", {
    animations: "disabled",
    // The bug this guards against (white popup, near-invisible text) is a
    // large, unmistakable diff across the whole option list — a slightly
    // looser tolerance than design-tokens.spec.js's 0.01 absorbs minor
    // font-hinting jitter under parallel test load without masking it.
    maxDiffPixelRatio: 0.03,
  });
});
