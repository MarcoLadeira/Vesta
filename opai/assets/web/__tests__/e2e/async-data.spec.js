import { test, expect } from "@playwright/test";

import { openApp, openNav } from "./helpers/app.js";


// #146: heavy data payloads (dashboard sections, settings aggregation, tool
// application) are computed off the GUI thread and delivered via signals. The
// UI shows an honest loading state, renders from the async delivery, and drops
// stale responses when the user navigates faster than the worker.

test("dashboard renders through the async request/ready path", async ({ page }) => {
  await openApp(page);
  await openNav(page, "Money Saved");
  const requests = await page.evaluate(() => window.__mock.dashboardRequests);
  expect(requests.length).toBeGreaterThan(0);
  expect(requests[0].requestId).toContain("dash-");
  await expect(page.locator("#dashPage")).not.toContainText("Loading…");
});

test("a slow dashboard shows loading until the worker delivers", async ({ page }) => {
  await openApp(page, { dashboardDelayMs: 400 });
  await openNav(page, "Money Saved");
  await expect(page.locator("#dashPage")).toContainText("Loading…");
  await expect(page.locator("#dashPage")).not.toContainText("Loading…", { timeout: 3000 });
});

test("a stale dashboard response is dropped after navigating on", async ({ page }) => {
  await openApp(page, { dashboardDelayMs: 300 });
  await openNav(page, "Money Saved");
  // Navigate to Settings before the slow dashboard payload arrives.
  await openNav(page, "Settings");
  await page.waitForTimeout(500); // let the stale dashboard payload arrive
  // The settings view is still what's rendered — the stale payload didn't
  // repaint the dashboard page or steal the view.
  await expect(page.locator("#view-settings")).toHaveClass(/active/);
  await expect(page.locator("#settingsPage")).toContainText("Settings");
});

test("settings renders through the async request/ready path", async ({ page }) => {
  await openApp(page);
  await openNav(page, "Settings");
  const requests = await page.evaluate(() => window.__mock.settingsRequests);
  expect(requests.length).toBeGreaterThan(0);
  await expect(page.locator("#settingsPage")).toContainText("Connection Doctor");
});
