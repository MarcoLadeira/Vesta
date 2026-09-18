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
  await railItem(page, "general").click();
  await page.locator('select[data-default-pref="default_mode"]').selectOption("approve-edits");
  const saved = await page.evaluate(() => window.__mock.savedPrefs);
  expect(saved).toContainEqual(["default_model", "account:claude:opus"]);
  expect(saved).toContainEqual(["default_mode", "approve-edits"]);
  // The composer selects show the new defaults without a reload.
  await expect(page.locator("#modelSel")).toHaveValue("account:claude:opus");
  await expect(page.locator("#modeSel")).toHaveValue("approve-edits");
});

test("the global model picker manages hidden and custom models without showing hidden choices", async ({ page }) => {
  const modelOverrides = {
    global: true,
    path: "~/.opai/models.json",
    providers: { codex: { models: [{ id: "gpt-custom", display: "My GPT", capability: "best", full: "My GPT", aliases: [] }] } },
    hidden: { claude: ["opus"] },
    errors: [],
  };
  await openApp(page, {
    boot: {
      modelOverrides,
      models: [
        { id: "account:claude:opus", model: "opus", label: "Claude · Opus 4.8", kind: "account", provider: "claude" },
        { id: "account:codex:gpt-5.6-sol", model: "gpt-5.6-sol", label: "Codex · GPT-5.6 Sol", kind: "account", provider: "codex" },
        { id: "auto", label: "Vesta · Auto mode", kind: "auto", group: "routing" },
      ],
    },
    settings: {
      modelOverrides,
    },
  });
  await openNav(page, "Settings");
  await railItem(page, "models").click();

  await expect(page.getByText("Your model picker", { exact: true })).toBeVisible();
  await expect(page.getByText("Global · ~/.opai/models.json")).toBeVisible();
  const hidden = page.locator('[data-model-visibility="account:claude:opus"]');
  await expect(hidden).not.toBeChecked();
  await expect(page.locator('#modelSel option[value="account:claude:opus"]')).toHaveCount(0);

  await page.getByText("Add a custom model", { exact: true }).click();
  await page.locator('[data-custom-provider]').selectOption("codex");
  await page.locator('[data-custom-model]').fill("gpt-new");
  await page.locator('[data-custom-label]').fill("My new GPT");
  const addButton = page.getByRole("button", { name: "Add model" });
  await expect(addButton).toBeEnabled();
  expect(await addButton.evaluate((button) => typeof button.onclick)).toBe("function");
  await addButton.click();
  await expect.poll(() => page.evaluate(() => window.__mock.savedModelOverrides.length)).toBe(1);
  expect(await page.evaluate(() => window.__mock.savedModelOverrides)).toContainEqual({
    providers: {
      codex: {
        models: [
          { id: "gpt-custom", display: "My GPT", capability: "best", full: "My GPT", aliases: [] },
          { id: "gpt-new", display: "My new GPT", capability: "balanced" },
        ],
      },
      claude: { hide: ["opus"] },
    },
  });
  await openNav(page, "Chat");
  await page.locator("#modelBtn").click();
  const customModel = page.locator('#modelPop [data-id="account:codex:gpt-new"]');
  await expect(customModel).toBeVisible();
  await customModel.click();
  await expect(page.locator("#modelSel")).toHaveValue("account:codex:gpt-new");
});

test("the run-mode select offers every mode, in order, Auto-apply included", async ({ page }) => {
  // Full Auto used to be absent here, and shown disabled if it was already the
  // default, because a bare savePref for it was rewritten server side — so
  // this page would have been an alternate route around the composer's pin
  // gate. There is no rewrite and no gate now.
  await openApp(page);
  await openNav(page, "Settings");
  await railItem(page, "general").click();
  const options = await page.locator('select[data-default-pref="default_mode"] option').allTextContents();
  expect(options).toEqual([
    "Ask",
    "Plan",
    "Manual",
    "Auto",
    "Accept edits",
    "Bypass permissions",
  ]);
  await expect(page.locator("#settingsPage")).toContainText("approval mode Vesta starts with", seen);
});

test("an Auto-apply default is shown as the selection it is, and stays changeable", async ({ page }) => {
  await openApp(page, {
    settings: { prefs: { default_model: "auto", default_mode: "full-auto" } },
  });
  await openNav(page, "Settings");
  await railItem(page, "general").click();

  const select = page.locator('select[data-default-pref="default_mode"]');
  await expect(select).toHaveValue("full-auto");
  const current = select.locator('option[value="full-auto"]');
  await expect(current).toHaveText("Bypass permissions");
  await expect(current).toBeEnabled();
  expect(await page.evaluate(() => window.__mock.savedPrefs)).toEqual([]);
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
  await railItem(page, "usage").click();
  const settings = page.locator("#settingsPage");
  await expect(settings).toContainText("Daily cap", seen);
  await expect(settings).toContainText("$2.00 · $1.58 left", seen);
  await expect(settings).toContainText("Monthly cap", seen);
  await expect(settings).toContainText("No cap set", seen); // monthly is unset in fixtures
  // Stat-tile labels render uppercase via CSS, so match case-insensitively.
  await expect(settings).toContainText(/spent this month/i, seen);
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
  await railItem(page, "usage").click();
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
  await railItem(page, "usage").click();
  const card = page.locator(`[data-model-id="${modelId}"]`);
  // The alarming 700k is now explained: it came from 87 calls across 5 tasks.
  await expect(card).toContainText("87 model calls across 5 tasks", seen);
});
