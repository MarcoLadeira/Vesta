import { test, expect } from "@playwright/test";

import { openApp, openNav, sendPrompt, finishRequest } from "./helpers/app.js";

test("terminal chat errors announce the reason and a safe next action", async ({ page }) => {
  await openApp(page);
  const requestId = await sendPrompt(page, "Explain the router");
  await finishRequest(page, requestId, {
    status: "account_error",
    answer: "The provider is unavailable right now.",
  });

  const card = page.locator(".error-card");
  await expect(card).toHaveAttribute("role", "alert");
  await expect(card).toContainText("The provider is unavailable right now.");
  await expect(card.getByRole("button", { name: "Retry" })).toBeVisible();
});

test("dashboard failures explain what happened and offer a manual retry", async ({ page }) => {
  await openApp(page, { dashboardErrors: { home: "RuntimeError: C:\\private\\ledger.sqlite" } });
  await openNav(page, "Money Saved");

  const card = page.locator("#dashPage .state-card");
  await expect(card).toHaveAttribute("role", "alert");
  await expect(card).toContainText("Dashboard data is temporarily unavailable.");
  await expect(card).not.toContainText("private\\ledger.sqlite");
  const requestsBeforeRetry = await page.evaluate(() => window.__mock.dashboardRequests.length);
  await card.getByRole("button", { name: "Try again" }).click();
  await expect.poll(() => page.evaluate(() => window.__mock.dashboardRequests.length)).toBe(requestsBeforeRetry + 1);
});

test("a degraded dashboard keeps its last safe path available without an automatic retry", async ({ page }) => {
  await openApp(page, {
    dashboards: {
      home: {
        degraded: { userMessage: "You appear to be offline, so fresh savings data is unavailable." },
      },
    },
  });
  await openNav(page, "Money Saved");

  const dashboard = page.locator("#dashPage");
  await expect(dashboard.locator(".state-card.degraded")).toHaveAttribute("role", "status");
  await expect(dashboard).toContainText("You appear to be offline, so fresh savings data is unavailable.");
  await expect(dashboard.getByRole("button", { name: "Try again" })).toHaveCount(0);
  await expect(dashboard.getByRole("button", { name: "Open chat" })).toBeVisible();
});

test("empty dashboards teach the fastest next step", async ({ page }) => {
  await openApp(page, {
    dashboards: {
      home: { title: "Money Saved", subtitle: "No saved runs yet.", hero: null, kpis: [], cards: [], actions: [] },
    },
  });
  await openNav(page, "Money Saved");

  const card = page.locator("#dashPage .state-card.empty");
  await expect(card).toHaveAttribute("role", "status");
  await expect(card).toContainText("No saved runs yet.");
  await expect(card.getByRole("button", { name: "Open chat" })).toBeVisible();
});

test("settings failures explain what happened and offer a manual retry", async ({ page }) => {
  await openApp(page, { settings: { error: "RuntimeError: C:\\private\\settings.json" } });
  await openNav(page, "Settings");

  const card = page.locator("#settingsPage .state-card");
  await expect(card).toHaveAttribute("role", "alert");
  await expect(card).toContainText("Settings data is temporarily unavailable.");
  await expect(card).not.toContainText("private\\settings.json");
  const requestsBeforeRetry = await page.evaluate(() => window.__mock.settingsRequests.length);
  await card.getByRole("button", { name: "Try again" }).click();
  await expect.poll(() => page.evaluate(() => window.__mock.settingsRequests.length)).toBe(requestsBeforeRetry + 1);
});

test("malformed async state envelopes replace loading with a safe manual-retry error", async ({ page }) => {
  await openApp(page, { dashboardDelayMs: 400, settingsDelayMs: 400 });
  await openNav(page, "Money Saved");
  await page.evaluate(() => window.__mock.bridge.dashboardReady.emit("not-json"));
  await expect(page.locator("#dashPage .state-card.error")).toContainText("Couldn't load this dashboard");

  await openNav(page, "Settings");
  await page.evaluate(() => window.__mock.bridge.settingsReady.emit("not-json"));
  await expect(page.locator("#settingsPage .state-card.error")).toContainText("Couldn't load settings");
});

test("malformed async state data is rejected before a renderer can consume it", async ({ page }) => {
  await openApp(page, { dashboardDelayMs: 400, settingsDelayMs: 400 });
  await openNav(page, "Money Saved");
  const dashboardRequest = await page.evaluate(() => window.__mock.dashboardRequests.at(-1).requestId);
  await page.evaluate((requestId) => window.__mock.bridge.dashboardReady.emit(JSON.stringify({ requestId, data: "not-an-object" })), dashboardRequest);
  await expect(page.locator("#dashPage .state-card.error")).toContainText("Couldn't load this dashboard");

  await openNav(page, "Settings");
  const settingsRequest = await page.evaluate(() => window.__mock.settingsRequests.at(-1));
  await page.evaluate((requestId) => window.__mock.bridge.settingsReady.emit(JSON.stringify({ requestId, data: ["not-an-object"] })), settingsRequest);
  await expect(page.locator("#settingsPage .state-card.error")).toContainText("Couldn't load settings");
});

test("degraded settings has a manual retry and does not retry by itself", async ({ page }) => {
  await openApp(page, {
    settings: { degraded: { userMessage: "Local settings data is temporarily unavailable." } },
  });
  await openNav(page, "Settings");

  const card = page.locator("#settingsPage .state-card.degraded");
  await expect(card).toHaveAttribute("role", "status");
  const requestsBeforeRetry = await page.evaluate(() => window.__mock.settingsRequests.length);
  await expect.poll(() => page.evaluate(() => window.__mock.settingsRequests.length)).toBe(requestsBeforeRetry);
  await card.getByRole("button", { name: "Try again" }).click();
  await expect.poll(() => page.evaluate(() => window.__mock.settingsRequests.length)).toBe(requestsBeforeRetry + 1);
});

test("empty recents teaches the fastest next step", async ({ page }) => {
  await openApp(page, { boot: { recents: [] } });

  const card = page.locator("#recents .state-card");
  await expect(card).toHaveAttribute("role", "status");
  await expect(card).toContainText("No saved chats yet");
  await card.getByRole("button", { name: "New chat" }).click();
  await expect(page.locator("#input")).toBeFocused();
});

test("toasts are polite live announcements", async ({ page }) => {
  await openApp(page);
  await expect(page.locator("#toast")).toHaveAttribute("role", "status");
  await expect(page.locator("#toast")).toHaveAttribute("aria-live", "polite");
});
