import { test, expect } from "@playwright/test";

import { openApp, openNav } from "./helpers/app.js";


// Models & Routing + Cost Firewall pages (#238): defaults are editable in
// place and reflect in the composer instantly; budgets are shown honestly from
// the ledger-backed payload; limit inputs validate client-side.

const railItem = (page, id) => page.locator(`.settings-rail-item[data-rail-target="${id}"]`);
const seen = { useInnerText: true };

test("defaults are editable and reflect in the composer immediately", async ({ page }) => {
  await openApp(page);
  await openNav(page, "Settings");
  await railItem(page, "models").click();

  await page.locator('select[data-default-pref="default_model"]').selectOption("account:claude:opus");
  await page.locator('select[data-default-pref="default_mode"]').selectOption("approve-edits");
  const saved = await page.evaluate(() => window.__mock.savedPrefs);
  expect(saved).toContainEqual(["default_model", "account:claude:opus"]);
  expect(saved).toContainEqual(["default_mode", "approve-edits"]);
  // The composer selects show the new defaults without a reload.
  await expect(page.locator("#modelSel")).toHaveValue("account:claude:opus");
  await expect(page.locator("#modeSel")).toHaveValue("approve-edits");
});

test("the run-mode select never offers Full Auto", async ({ page }) => {
  await openApp(page);
  await openNav(page, "Settings");
  await railItem(page, "models").click();
  const options = await page.locator('select[data-default-pref="default_mode"] option').allTextContents();
  expect(options).toEqual(["Ask", "Plan", "Safe Auto", "Approve Edits"]);
  await expect(page.locator("#settingsPage")).toContainText("Full Auto can only be pinned from the composer", seen);
});

test("the routing explainer states the honest local-first order", async ({ page }) => {
  await openApp(page);
  await openNav(page, "Settings");
  await railItem(page, "models").click();
  const settings = page.locator("#settingsPage");
  await expect(settings).toContainText("Local-first routing", seen);
  await expect(settings).toContainText("deterministic tools -> cache -> local model -> confirmed cloud", seen);
});

test("budgets render caps, spend, and remaining honestly", async ({ page }) => {
  await openApp(page);
  await openNav(page, "Settings");
  await railItem(page, "firewall").click();
  const settings = page.locator("#settingsPage");
  await expect(settings).toContainText("Daily cap", seen);
  await expect(settings).toContainText("$2.00 · $1.58 left", seen);
  await expect(settings).toContainText("Monthly cap", seen);
  await expect(settings).toContainText("No cap set", seen); // monthly is unset in fixtures
  await expect(settings).toContainText("Spent this month", seen);
  await expect(settings).toContainText("$3.10", seen);
});

test("an invalid usage limit shows an inline error and saves nothing", async ({ page }) => {
  const modelId = "account:claude:haiku";
  await openApp(page, {
    settings: {
      prefs: { default_model: "auto", default_mode: "safe-auto" },
      firewall: {}, permissions: [], accounts: [], about: {}, credentials: [],
      models: [{ id: modelId, label: "Claude · Haiku 4.5", provider: "claude", kind: "account" }],
      usage: [{ modelId, provider: "claude", source: "opai", metric: "tokens", used: 100, limit: null, remaining: null, percent: 0, window: "month", confidence: "measured" }],
    },
  });
  await openNav(page, "Settings");
  await railItem(page, "firewall").click();
  const card = page.locator(`[data-model-id="${modelId}"]`);
  await card.getByLabel("Soft token limit").fill("0");
  await card.getByRole("button", { name: "Save limit" }).click();
  await expect(card.locator("[data-usage-error]")).toBeVisible();
  await expect(card.locator("[data-usage-error]")).toContainText("whole number above zero");
  expect(await page.evaluate(() => window.__mock.savedUsageLimits)).toEqual([]);
});

test("a large token total is made legible by the model-call count (#334)", async ({ page }) => {
  const modelId = "free:gemini:gemini-3.1-flash-lite";
  await openApp(page, {
    settings: {
      prefs: { default_model: "auto", default_mode: "safe-auto" },
      firewall: {}, permissions: [], accounts: [], about: {}, credentials: [],
      models: [{ id: modelId, label: "Gemini 3.1 Flash-Lite", provider: "gemini", kind: "free" }],
      usage: [{ modelId, provider: "gemini", source: "opai", metric: "tokens", used: 700000, limit: null, window: "month", confidence: "measured", modelCalls: 87, taskCount: 5 }],
    },
  });
  await openNav(page, "Settings");
  await railItem(page, "firewall").click();
  const card = page.locator(`[data-model-id="${modelId}"]`);
  // The alarming 700k is now explained: it came from 87 calls across 5 tasks.
  await expect(card).toContainText("87 model calls across 5 tasks", seen);
});
