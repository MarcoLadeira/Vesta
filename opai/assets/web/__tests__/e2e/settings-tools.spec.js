import { test, expect } from "@playwright/test";

import { openApp, openNav } from "./helpers/app.js";

// Tools that left the app sidebar remain reachable from their grouped Settings
// destinations after the information architecture changed.

async function openTools(page, destination = "advanced") {
  await openNav(page, "Settings");
  await page.locator(`.settings-rail-item[data-rail-target="${destination}"]`).click();
  await expect(page.locator(`#set-sec-${destination}`)).toBeVisible();
  if (destination === "advanced") await page.locator("[data-settings-tools] > summary").click();
}

test("Settings offers every page that left the sidebar", async ({ page }) => {
  await openApp(page, {});
  for (const [destination, titles] of Object.entries({
    connections: ["Prompt Library"],
    agents: ["Agents", "Proof Bundle", "Workflows"],
    advanced: ["Money Saved", "Cost Firewall", "Context Waste", "Benchmark"],
  })) {
    await openTools(page, destination);
    for (const title of titles) {
      await expect(
        page.locator(`#set-sec-${destination} [data-go-view] >> text=${title}`).first()
      ).toBeVisible();
    }
  }
});

test("a tile leaves Settings for the real view", async ({ page }) => {
  await openApp(page, {});
  await openTools(page, "connections");
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
