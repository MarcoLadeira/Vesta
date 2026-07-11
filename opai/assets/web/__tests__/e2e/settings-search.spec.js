import { test, expect } from "@playwright/test";

import { openApp, openNav } from "./helpers/app.js";


test.beforeEach(async ({ page }) => {
  await openApp(page);
  await openNav(page, "Settings");
});

test("the search box is present and everything shows by default", async ({ page }) => {
  await expect(page.locator("#settingsSearch")).toBeVisible();
  const settings = page.locator("#settingsPage");
  await expect(settings).toContainText("Cost firewall");
  await expect(settings).toContainText("Privacy");
  await expect(settings).toContainText("Default model");
});

test("searching narrows to matching sections and hides the rest", async ({ page }) => {
  await page.locator("#settingsSearch").fill("panic");
  // The Cost firewall block (which mentions Panic mode) stays visible…
  await expect(page.locator("#settingsPage")).toContainText("Panic mode");
  // …while an unrelated section (Privacy) is hidden.
  await expect(page.locator(".cb", { hasText: "No telemetry" })).toBeHidden();
});

test("a query with no matches shows the empty state", async ({ page }) => {
  await page.locator("#settingsSearch").fill("zzz-nonexistent-setting");
  await expect(page.locator("#settingsNoResults")).toBeVisible();
  await expect(page.locator(".set-head", { hasText: "Cost firewall" })).toBeHidden();
});

test("Escape clears the search and restores every section", async ({ page }) => {
  const search = page.locator("#settingsSearch");
  await search.fill("panic");
  await expect(page.locator(".cb", { hasText: "No telemetry" })).toBeHidden();
  await search.press("Escape");
  await expect(search).toHaveValue("");
  await expect(page.locator(".cb", { hasText: "No telemetry" })).toBeVisible();
  await expect(page.locator("#settingsNoResults")).toBeHidden();
});

test("clearing the query manually also restores everything", async ({ page }) => {
  const search = page.locator("#settingsSearch");
  await search.fill("panic");
  await search.fill("");
  await expect(page.locator("#settingsPage")).toContainText("No telemetry");
  await expect(page.locator("#settingsNoResults")).toBeHidden();
});
