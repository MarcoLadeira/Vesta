import { test, expect } from "@playwright/test";
import { openApp } from "./helpers/app.js";

/**
 * The "New app" button is gone from the UI.
 *
 * An earlier attempt at this reported success having removed the *command
 * palette* entry and its `runCommand` case, and never touched the button in
 * index.html. So the palette route to the feature was deleted while the
 * button the request was about stayed exactly where it was -- and the
 * evidence cited was 56 passing Python tests in test_gui_web.py, which could
 * not have observed an HTML button under any circumstances.
 *
 * This asserts the rendered DOM, which is the only thing that could have
 * caught it.
 */
test("no New app button, and no way left to reach the flow", async ({ page }) => {
  await openApp(page);

  await expect(page.locator("#newApp")).toHaveCount(0);
  await expect(page.locator(".sidebar")).not.toContainText("New app");

  // The command palette must not offer it either -- the two surfaces are how
  // this went wrong the first time. Driven through the real palette rather
  // than by reading a module-scoped array: PALETTE is not on `window`, so
  // `(window.OPAI_PALETTE || [])` would have been an empty list quietly
  // satisfying any assertion made about it.
  await page.evaluate(() => window.dispatchEvent(new Event("resize")));
  await page.keyboard.press("Control+k");
  await expect(page.locator("#palette")).toHaveClass(/open/);
  const options = page.locator("#paletteList .opt");
  await expect(options.first()).toBeVisible();
  await expect(page.locator("#paletteList")).not.toContainText("New app");
  expect(await options.evaluateAll((els) => els.map((e) => e.dataset.id)))
    .not.toContain("new_app");
});
