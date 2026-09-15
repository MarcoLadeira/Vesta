import { test, expect } from "@playwright/test";

import { openApp, openNav } from "./helpers/app.js";

// Prompt Library and the seven Insights dashboards moved off the sidebar. That
// is only an improvement if they are still reachable -- otherwise the change
// did not relocate them, it removed them.

async function openTools(page) {
  await openNav(page, "Settings");
  await page.locator('.settings-rail-item[data-rail-target="tools"]').click();
  await expect(page.locator("#settingsPage")).toContainText("Tools & Insights");
}

test("Settings offers every page that left the sidebar", async ({ page }) => {
  await openApp(page, {});
  await openTools(page);
  const page_ = page.locator("#settingsPage");
  for (const title of [
    "Prompt Library",
    "Money Saved",
    "Cost Firewall",
    "Context Waste",
    "Benchmark",
    "Agents",
    "Proof Bundle",
    "Workflows",
  ]) {
    await expect(page_.locator(`[data-go-view] >> text=${title}`).first()).toBeVisible();
  }
});

test("a tile leaves Settings for the real view", async ({ page }) => {
  await openApp(page, {});
  await openTools(page);
  await page.locator('[data-go-view="prompts"]').click();
  // It navigates the app, not the settings rail.
  await expect(page.locator("#settingsPage")).toBeHidden();
});

test("the moved pages are searchable from Settings", async ({ page }) => {
  await openApp(page, {});
  await openNav(page, "Settings");
  await page.locator("#settingsSearch").fill("insights");
  await expect(page.locator("#settingsPage")).toContainText("Tools & Insights");
});
