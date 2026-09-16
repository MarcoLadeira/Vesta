import { test, expect } from "@playwright/test";
import { openApp, openNav } from "./helpers/app.js";

test("team default persists and updates the composer without cloud consent", async ({ page }) => {
  await openApp(page);
  await openNav(page, "Settings");
  await page.locator('[data-rail-target="agents"]').click();
  await page.getByRole("switch", { name: "Use an AI team" }).check();
  expect(await page.evaluate(() => window.__mock.savedPrefs)).toContainEqual(["multi_agent_enabled", "true"]);
  expect(await page.evaluate(() => window.__vesta.state.agentsAllowCloud)).toBeFalsy();
  await page.locator("#settingsBack").click();
  await expect(page.locator("#teamModeBtn")).toHaveAttribute("aria-pressed", "true");
  await openNav(page, "Settings");
  await page.locator('[data-rail-target="agents"]').click();
  await expect(page.getByRole("switch", { name: "Use an AI team" })).toBeChecked();
  await page.getByRole("switch", { name: "Use an AI team" }).uncheck();
  expect(await page.evaluate(() => window.__mock.savedPrefs)).toContainEqual(["multi_agent_enabled", "false"]);
});

test("search reveals a collapsed custom model form", async ({ page }) => {
  await openApp(page);
  await openNav(page, "Settings");
  await page.locator('[data-rail-target="models"]').click();
  await expect(page.locator("[data-add-custom-model]")).toBeHidden();
  await page.locator("#settingsSearch").fill("Add a custom model");
  await page.locator("[data-settings-search-result]").filter({ hasText: "Add a custom model" }).first().click();
  await expect(page.locator("[data-add-custom-model]")).toBeVisible();
  await expect(page.locator("[data-add-custom-model]")).toBeFocused();
});
