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

test("account disconnect uses the styled card, never window.confirm", async ({ page }) => {
  await trapNativeConfirm(page);
  await openApp(page);
  await openSettings(page, "providers");
  await page.locator('[data-disconnect-account="claude"]').click();
  await expect(page.locator(".inline-confirm").first()).toBeVisible();
  expect(await page.evaluate(() => window.__nativeConfirmCalls)).toBe(0);
});

test("the confirm card can be dismissed with Escape (#151)", async ({ page }) => {
  // Was driven off the Full Auto pin card, which no longer exists. Account
  // disconnect is a real remaining confirmation, so the property is unchanged.
  await openApp(page);
  await openSettings(page, "providers");
  await page.locator('[data-disconnect-account="claude"]').click();
  await expect(page.locator(".inline-confirm").first()).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.locator(".inline-confirm")).toHaveCount(0);
});
