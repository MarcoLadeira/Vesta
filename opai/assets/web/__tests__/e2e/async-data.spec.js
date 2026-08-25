import { test, expect } from "@playwright/test";

import { openApp, openNav, sendPrompt, finishRequest } from "./helpers/app.js";


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
  await expect(page.locator("#dashPage .state-card.loading")).toHaveCount(0);
});

test("a slow dashboard shows loading until the worker delivers", async ({ page }) => {
  await openApp(page, { dashboardDelayMs: 400 });
  await openNav(page, "Money Saved");
  await expect(page.locator("#dashPage .state-card.loading")).toHaveAttribute("role", "status");
  await expect(page.locator("#dashPage .state-card.loading")).toContainText("Waiting for locally prepared dashboard data.");
  await expect(page.locator("#dashPage .state-card.loading")).toHaveCount(0, { timeout: 3000 });
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

test("status refresh after a turn uses the async request/ready path", async ({ page }) => {
  await openApp(page);
  // A completed turn refreshes the persistent status line; it must go through
  // the worker-backed path (the post-turn overview recompute is the freeze the
  // cache+async fix targets), not a synchronous GUI-thread read.
  const id = await sendPrompt(page);
  await finishRequest(page, id, { status: "answered_by_account", answer: "done" });
  await expect
    .poll(() => page.evaluate(() => window.__mock.statusRequests.length))
    .toBeGreaterThan(0);
  const requests = await page.evaluate(() => window.__mock.statusRequests);
  expect(requests[requests.length - 1]).toContain("status-");
  await expect(page.locator("#statusLine")).not.toBeEmpty();
});

test("workspace refresh after a turn uses the async request/ready path", async ({ page }) => {
  await openApp(page);
  const id = await sendPrompt(page);
  await finishRequest(page, id, { status: "answered_by_account", answer: "done" });

  await expect
    .poll(() => page.evaluate(() => window.__mock.workspaceRequests.length))
    .toBeGreaterThan(0);
  const requests = await page.evaluate(() => window.__mock.workspaceRequests);
  expect(requests[requests.length - 1]).toContain("workspace-");
  expect(await page.evaluate(() => window.__mock.workspaceStateCalls)).toBe(0);
});

test("visible inspector loads through the async request/ready path", async ({ page }) => {
  await openApp(page);

  await expect
    .poll(() => page.evaluate(() => window.__mock.inspectorRequests.length))
    .toBeGreaterThan(0);
  const requests = await page.evaluate(() => window.__mock.inspectorRequests);
  expect(requests[requests.length - 1]).toContain("inspector-");
});
