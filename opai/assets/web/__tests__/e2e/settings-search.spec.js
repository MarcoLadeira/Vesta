import { test, expect } from "@playwright/test";

import { openApp, openNav } from "./helpers/app.js";


// Settings are paned pages (#217): one page visible at a time. Search stays
// global (#240) — typing switches into a cross-page results mode; clearing the
// query returns to the page the user was on. All containment assertions use
// innerText so they reflect what is actually on screen (hidden panes stay in
// the DOM for wiring, so textContent would see everything).

test.beforeEach(async ({ page }) => {
  await openApp(page);
  await openNav(page, "Settings");
});

const seen = { useInnerText: true };

test("the search box is present and only the active page shows by default", async ({ page }) => {
  await expect(page.locator("#settingsSearch")).toBeVisible();
  const settings = page.locator("#settingsPage");
  await expect(settings).toContainText("OPai status", seen);
  // Other pages exist in the rail but their content is not on screen.
  await expect(settings).not.toContainText("Connection Doctor", seen);
  await expect(settings).not.toContainText("Cost firewall", seen);
  await expect(settings).not.toContainText("No telemetry", seen);
});

test("searching finds matches across pages and hides everything else", async ({ page }) => {
  await page.locator("#settingsSearch").fill("panic");
  // The Cost Firewall block (which mentions Panic mode) appears even though
  // that page wasn't open…
  await expect(page.locator("#settingsPage")).toContainText("Panic mode", seen);
  // …while non-matching content from the active page is hidden.
  await expect(page.locator("#settingsPage")).not.toContainText("Connection Doctor", seen);
  // The rail dims pages with no matches and keeps matching pages normal.
  await expect(page.locator('.settings-rail-item[data-rail-target="firewall"]')).not.toHaveAttribute("data-dim", "");
  await expect(page.locator('.settings-rail-item[data-rail-target="privacy"]')).toHaveAttribute("data-dim", "");
});

test("a query with no matches shows the empty state", async ({ page }) => {
  await page.locator("#settingsSearch").fill("zzz-nonexistent-setting");
  await expect(page.locator("#settingsNoResults")).toBeVisible();
  await expect(page.locator(".set-head", { hasText: "Cost firewall" })).toBeHidden();
});

test("Escape clears the search and returns to the active page", async ({ page }) => {
  const search = page.locator("#settingsSearch");
  await search.fill("panic");
  await expect(page.locator("#settingsPage")).toContainText("Panic mode", seen);
  await search.press("Escape");
  await expect(search).toHaveValue("");
  await expect(page.locator("#settingsPage")).toContainText("OPai status", seen);
  await expect(page.locator("#settingsPage")).not.toContainText("Panic mode", seen);
  await expect(page.locator("#settingsNoResults")).toBeHidden();
});

test("clearing the query manually also returns to the active page", async ({ page }) => {
  const search = page.locator("#settingsSearch");
  await search.fill("panic");
  await search.fill("");
  await expect(page.locator("#settingsPage")).toContainText("OPai status", seen);
  await expect(page.locator("#settingsNoResults")).toBeHidden();
});

test("search started from another page returns there on clear", async ({ page }) => {
  await page.locator('.settings-rail-item[data-rail-target="privacy"]').click();
  await expect(page.locator("#settingsPage")).toContainText("No telemetry", seen);
  const search = page.locator("#settingsSearch");
  await search.fill("panic");
  await expect(page.locator("#settingsPage")).toContainText("Panic mode", seen);
  await expect(page.locator("#settingsPage")).not.toContainText("No telemetry", seen);
  await search.press("Escape");
  await expect(page.locator("#settingsPage")).toContainText("No telemetry", seen);
  await expect(page.locator("#settingsPage")).not.toContainText("Panic mode", seen);
});
