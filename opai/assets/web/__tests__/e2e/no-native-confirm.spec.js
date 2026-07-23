import { test, expect } from "@playwright/test";

import { openApp, openSettings } from "./helpers/app.js";

// #151: the approval-card system owns every confirmation. If any flow ever
// falls back to a native window.confirm, this trap fires and fails the test.
async function trapNativeConfirm(page) {
  await page.addInitScript(() => {
    window.__nativeConfirmCalls = 0;
    window.confirm = () => {
      window.__nativeConfirmCalls += 1;
      return false;
    };
  });
}

test("Full Auto uses the styled card, never window.confirm", async ({ page }) => {
  await trapNativeConfirm(page);
  await openApp(page);
  await page.selectOption("#modeSel", "full-auto");
  await expect(page.locator(".inline-confirm")).toBeVisible();
  expect(await page.evaluate(() => window.__nativeConfirmCalls)).toBe(0);
});

test("account disconnect uses the styled card, never window.confirm", async ({ page }) => {
  await trapNativeConfirm(page);
  await openApp(page);
  await openSettings(page, "providers");
  await page.locator('[data-disconnect-account="claude"]').click();
  await expect(page.locator(".inline-confirm").first()).toBeVisible();
  expect(await page.evaluate(() => window.__nativeConfirmCalls)).toBe(0);
});

test("the confirm card can be dismissed with Escape (#151)", async ({ page }) => {
  await openApp(page);
  await page.selectOption("#modeSel", "full-auto");
  await expect(page.locator(".inline-confirm")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.locator("#modeSel")).toHaveValue("safe-auto");
  expect(await page.evaluate(() => window.__mock.fullAutoPins)).toBe(0);
});
