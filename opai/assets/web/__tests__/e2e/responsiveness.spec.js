import { test, expect } from "@playwright/test";

import { openApp, openNav } from "./helpers/app.js";


async function expectUsableViewport(page) {
  const metrics = await page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    innerWidth: window.innerWidth,
  }));
  expect(metrics.scrollWidth).toBeLessThanOrEqual(metrics.innerWidth);
  await expect(page.locator("#input")).toBeVisible();
  await expect(page.locator("#send")).toBeVisible();
  await expect(page.locator("#wsSwitch")).toBeVisible();
}

for (const viewport of [
  { name: "tablet", width: 768, height: 1024 },
  { name: "desktop", width: 1280, height: 820 },
  { name: "large desktop", width: 1600, height: 1000 },
]) {
  test(`${viewport.name} keeps chat and navigation usable`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await openApp(page);
    await expectUsableViewport(page);
    await openNav(page, "Money Saved");
    await expect(page.locator("#dashPage .kpis")).toBeVisible();
  });
}

for (const viewport of [
  { name: "mobile large", width: 430, height: 932 },
  { name: "mobile small", width: 375, height: 667 },
]) {
  test(`${viewport.name} has no overflow and keeps composer usable`, async ({ page }) => {
    test.fail(true, "BUG-QA-004: desktop grid has no tablet/mobile responsive layout");
    await page.setViewportSize(viewport);
    await openApp(page);
    await expectUsableViewport(page);
  });
}

test("desktop Inspector remains usable when toggled", async ({ page }) => {
  await page.setViewportSize({ width: 1040, height: 700 });
  await openApp(page);
  await expect(page.locator("#inspector")).toBeVisible();
  await page.getByRole("button", { name: "Inspector" }).click();
  await expect(page.locator("#input")).toBeVisible();
  await expectUsableViewport(page);
});
