import { test, expect } from "@playwright/test";

import { openApp, openNav } from "./helpers/app.js";


test("an old deep link opens its canonical destination and subsection", async ({ page }) => {
  await openApp(page);
  await page.evaluate(() => window.history.replaceState(null, "", "#settings/privacy"));
  await openNav(page, "Settings");
  await expect(page.locator("#set-sec-safety")).toBeVisible();
  await expect(page.locator('[data-settings-subsection="privacy"]')).toBeVisible();
  await expect(page.locator('.settings-rail-item[aria-current="page"]')).toHaveAttribute(
    "data-rail-target",
    "safety",
  );
});

test("category changes move focus to the destination heading", async ({ page }) => {
  await openApp(page);
  await openNav(page, "Settings");
  await page.locator('.settings-rail-item[data-rail-target="connections"]').click();
  await expect(page.locator("#set-sec-connections .pane-title")).toBeFocused();
});

test("segmented settings expose radio semantics and arrow-key selection", async ({ page }) => {
  await openApp(page);
  await openNav(page, "Settings");
  await page.locator('.settings-rail-item[data-rail-target="appearance"]').click();
  const group = page.locator('[data-appearance-key="density"]');
  await expect(group).toHaveAttribute("role", "radiogroup");
  const comfortable = group.getByRole("radio", { name: "Comfortable" });
  const compact = group.getByRole("radio", { name: "Compact" });
  await expect(comfortable).toHaveAttribute("aria-checked", "true");
  await comfortable.focus();
  await comfortable.press("ArrowRight");
  await expect(compact).toHaveAttribute("aria-checked", "true");
  await expect(compact).toBeFocused();
  await expect
    .poll(() => page.evaluate(() => window.__mock.savedPrefs))
    .toContainEqual(["density", "compact"]);
});

test("phone detail headings are not hidden by the sticky navigation", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await openApp(page);
  await openNav(page, "Settings");
  await page.locator('.settings-rail-item[data-rail-target="models"]').click();
  const positions = await page.evaluate(() => {
    const heading = document.querySelector("#set-sec-models .pane-title").getBoundingClientRect();
    const back = document.querySelector(".settings-mobile-bar").getBoundingClientRect();
    return { heading, back };
  });
  expect(positions.heading.top).toBeGreaterThanOrEqual(positions.back.bottom);
});
