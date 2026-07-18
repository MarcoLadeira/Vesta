import { test, expect } from "@playwright/test";

import { openApp, openNav } from "./helpers/app.js";

test("chrome uses accessible SVG icons rather than emoji glyphs", async ({ page }) => {
  await openApp(page);
  await expect(page.locator("#newApp .ui-icon")).toBeVisible();
  await expect(page.locator("#buildToggle .ui-icon")).toBeAttached();
  await expect(page.locator("#newApp")).not.toContainText("✦");
  await expect(page.locator("#buildToggle")).not.toContainText("✦");
  await expect(page.locator("#newApp .ui-icon")).toHaveAttribute("aria-hidden", "true");
});

for (const [density, prefs] of Object.entries({
  comfortable: { density: "comfortable" },
  compact: { density: "compact" },
})) {
  test(`tokenized core surfaces stay usable at ${density} density`, async ({ page }) => {
    await openApp(page, { boot: { prefs } });
    await expect(page.locator("#app")).toHaveCSS("font-size", "14px");

    for (const [label, view] of [
      ["Chat", "#view-chat"],
      ["Prompt Library", "#view-prompts"],
      ["Money Saved", "#view-dashboard"],
      ["Settings", "#view-settings"],
    ]) {
      await openNav(page, label);
      await expect(page.locator(view)).toBeVisible();
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
      await expect(page.locator(view)).toHaveScreenshot(`${density}-${label.toLowerCase().replaceAll(" ", "-")}.png`, {
        animations: "disabled",
        maxDiffPixelRatio: 0.01,
      });
    }
  });
}

test("rendered design-token preview documents the canonical scales", async ({ page }) => {
  await page.goto("/opai/assets/web/design-tokens-preview.html");
  await expect(page.getByRole("heading", { name: "OPai web design tokens" })).toBeVisible();
  await expect(page.getByText("Body — 14px / 1.5 / regular")).toBeVisible();
  await expect(page.getByLabel("Spacing samples from 4 to 32 pixels")).toBeVisible();
});
