import { test, expect } from "@playwright/test";

import { openApp, openNav } from "./helpers/app.js";


// Permissions & Safety + Privacy & Data pages (#239): honest run-mode
// comparison + factual privacy stance + a gated destructive clear.

const seen = { useInnerText: true };
const goto = async (page, id) => {
  await openNav(page, "Settings");
  await page.locator(`.settings-rail-item[data-rail-target="${id}"]`).click();
};

test("Permissions page shows current-mode rules and a run-mode comparison", async ({ page }) => {
  await openApp(page);
  await goto(page, "permissions");
  const settings = page.locator("#settingsPage");
  // Current-mode capability rows with plain-language notes.
  await expect(settings).toContainText("Read files", seen);
  await expect(settings).toContainText("Pauses for your OK", seen);
  // Run-mode comparison, with the active mode marked current.
  await expect(settings).toContainText("Run modes", seen);
  await expect(page.locator(".mode-row.active")).toContainText("Safe Auto");
  await expect(page.locator(".mode-row.active")).toContainText("current");
  await expect(page.locator(".mode-row", { hasText: "Full Auto" })).toContainText("6 allowed");
});

test("Privacy page states the factual data stance", async ({ page }) => {
  await openApp(page);
  await goto(page, "privacy");
  const settings = page.locator("#settingsPage");
  await expect(settings).toContainText("Raw prompts are never stored", seen);
  await expect(settings).toContainText("one-way task hashes", seen);
  await expect(settings).toContainText("No telemetry", seen);
});

test("clearing saved chat requires the styled confirm; cancel keeps data", async ({ page }) => {
  await openApp(page);
  await goto(page, "privacy");
  await page.locator("#settingsClearRecents").click();
  // A styled inline confirm appears — not a native dialog, not an instant clear.
  const confirm = page.locator(".inline-confirm");
  await expect(confirm).toBeVisible();
  await expect(confirm).toContainText("cannot be undone");
  await confirm.locator('[data-ic="cancel"]').click();
  expect(await page.evaluate(() => window.__mock.clearedRecents)).toBe(0);
});

test("confirming the clear calls the session bridge exactly once", async ({ page }) => {
  await openApp(page);
  await goto(page, "privacy");
  await page.locator("#settingsClearRecents").click();
  const confirm = page.locator(".inline-confirm");
  await confirm.locator('[data-ic="ok"]').click();
  await expect.poll(() => page.evaluate(() => window.__mock.clearedRecents)).toBe(1);
});
