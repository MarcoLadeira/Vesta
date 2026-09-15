import { test, expect } from "@playwright/test";

import { openApp, openNav } from "./helpers/app.js";


test.beforeEach(async ({ page }) => {
  await openApp(page);
  await openNav(page, "Settings");
});

test("Settings opens on General with one canonical page visible", async ({ page }) => {
  await expect(page.locator("#settingsSearch")).toBeVisible();
  await expect(page.locator("#set-sec-general")).toBeVisible();
  await expect(page.locator("#set-sec-connections")).toBeHidden();
  await expect(page.locator('.settings-rail-item[aria-current="page"]')).toHaveAttribute(
    "data-rail-target",
    "general",
  );
});

for (const [query, destination] of [
  ["balance", "Usage & Budgets"],
  ["firewall", "Usage & Budgets"],
  ["permissions", "Safety & Privacy"],
  ["api key", "Connections"],
]) {
  test(`historical term ${query} resolves to ${destination}`, async ({ page }) => {
    await page.locator("#settingsSearch").fill(query);
    const results = page.locator("[data-settings-search-result]");
    await expect(results.first()).toBeVisible();
    await expect(page.locator("#settingsSearchResults")).toContainText(destination);
    await expect(results.first().locator(".settings-result-path")).toContainText("›");
  });
}

test("a search result navigates to its canonical setting and clears search", async ({ page }) => {
  const search = page.locator("#settingsSearch");
  await search.fill("daily cap");
  const result = page.locator("[data-settings-search-result]").filter({ hasText: "Daily cap" }).first();
  await expect(result).toContainText("Usage & Budgets");
  await result.click();
  await expect(search).toHaveValue("");
  await expect(page.locator("#set-sec-usage")).toBeVisible();
  await expect(page.locator('[data-settings-subsection="budgets"]')).toBeVisible();
});

for (const [query, label, destination] of [
  ["prompt library", "Prompt Library", "plugins"],
  ["workflows", "Workflows", "agents"],
]) {
  test(`${label} search opens its grouped destination`, async ({ page }) => {
    await page.locator("#settingsSearch").fill(query);
    const result = page
      .locator("[data-settings-search-result]")
      .filter({ hasText: label })
      .first();
    await result.click();
    await expect(page.locator(`#set-sec-${destination}`)).toBeVisible();
    await expect(
      page.locator(`#set-sec-${destination} [data-go-view]`).filter({ hasText: label })
    ).toBeFocused();
  });
}

test("search supports ArrowDown, Enter, Escape, and a visible clear action", async ({ page }) => {
  const search = page.locator("#settingsSearch");
  await search.fill("reduced motion");
  await expect(page.locator("#settingsSearchClear")).toBeVisible();
  await search.press("ArrowDown");
  await expect(page.locator("[data-settings-search-result]").first()).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.locator("#set-sec-appearance")).toBeVisible();

  await search.fill("privacy");
  await search.press("Escape");
  await expect(search).toHaveValue("");
  await expect(page.locator("#settingsSearchResults")).toBeHidden();
});

test("a query with no matches gives an announced empty state", async ({ page }) => {
  await page.locator("#settingsSearch").fill("zzz-nonexistent-setting");
  await expect(page.locator("#settingsNoResults")).toBeVisible();
  await expect(page.locator("#settingsNoResults")).toHaveAttribute("role", "status");
  await expect(page.locator("[data-settings-search-result]")).toHaveCount(0);
});
