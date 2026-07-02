import { test, expect } from "@playwright/test";

import { openApp, openNav } from "./helpers/app.js";


test("dashboard shows loading until bridge data arrives", async ({ page }) => {
  await openApp(page, { dashboardDelayMs: 250 });
  await openNav(page, "Money Saved");
  await expect(page.locator("#dashPage")).toHaveText("Loading…");
  await expect(page.locator("#dashPage")).toContainText("Money Saved");
});

test("settings shows loading until connection data arrives", async ({ page }) => {
  await openApp(page, { settingsDelayMs: 250 });
  await openNav(page, "Settings");
  await expect(page.locator("#settingsPage")).toHaveText("Loading…");
  await expect(page.locator("#settingsPage")).toContainText("Cost firewall");
});

test("Prompt Library transitions from loading boundary to templates", async ({ page }) => {
  await openApp(page, { promptDelayMs: 250 });
  await openNav(page, "Prompt Library");
  await expect(page.locator(".prompt-card")).toHaveCount(3);
});
