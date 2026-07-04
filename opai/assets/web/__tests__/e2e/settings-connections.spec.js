import { test, expect } from "@playwright/test";

import { DISCONNECTED_ACCOUNTS } from "./helpers/fixtures.js";
import { expectNoUiSentinels, openApp, openNav } from "./helpers/app.js";


test("settings renders defaults, firewall, accounts, privacy, and version", async ({ page }) => {
  await openApp(page);
  await openNav(page, "Settings");
  const settings = page.locator("#settingsPage");
  await expect(settings).toContainText("Default model");
  await expect(settings).toContainText("Safe Auto");
  await expect(settings).toContainText("solo-balanced");
  await expect(settings).toContainText("Claude");
  await expect(settings).toContainText("Codex");
  await expect(settings).toContainText("Copilot");
  await expect(settings).toContainText("No telemetry");
  await expect(settings).toContainText("0.2.0a1");
});

test("test connection on a connected account reports the live truth, not the cached label", async ({ page }) => {
  // Reproduces the reported bug: OPai's on-disk "connected" state can be stale
  // (an OAuth session that died since detection). Clicking Test connection
  // must run a live check and update the row, not just repeat "connected".
  await openApp(page, {
    providerTestResponses: { claude: { authStatus: "invalid", safeDiagnostic: "Session expired.", loginHint: "Run `claude` once and sign in to connect your account." } },
  });
  await openNav(page, "Settings");
  const row = page.locator('[data-account-row="claude"]');
  await expect(row).toContainText("connected");
  await page.locator('[data-test-account="claude"]').click();
  expect(await page.evaluate(() => window.__mock.providerTests)).toContain("claude");
  await expect(page.locator('[data-account-status="claude"]')).toHaveText("invalid");
});

test("test connection on a genuinely healthy account confirms connected", async ({ page }) => {
  await openApp(page, { providerTestResponses: { claude: { authStatus: "connected" } } });
  await openNav(page, "Settings");
  await page.locator('[data-test-account="claude"]').click();
  await expect(page.locator('[data-account-status="claude"]')).toHaveText("connected");
});

test("disconnect asks for confirmation, then signs out and updates the row", async ({ page }) => {
  await openApp(page);
  await openNav(page, "Settings");
  page.once("dialog", (dialog) => dialog.accept());
  await page.locator('[data-disconnect-account="claude"]').click();
  expect(await page.evaluate(() => window.__mock.disconnects)).toContain("claude");
  await expect(page.locator('[data-account-status="claude"]')).toHaveText("not connected");
  // Nothing left to disconnect or test once signed out.
  await expect(page.locator('[data-disconnect-account="claude"]')).toBeDisabled();
});

test("cancelling the disconnect confirmation leaves the account untouched", async ({ page }) => {
  await openApp(page);
  await openNav(page, "Settings");
  page.once("dialog", (dialog) => dialog.dismiss());
  await page.locator('[data-disconnect-account="claude"]').click();
  expect(await page.evaluate(() => window.__mock.disconnects)).toEqual([]);
  await expect(page.locator('[data-account-status="claude"]')).toHaveText("connected");
});

test("settings renders disconnected accounts without crashing", async ({ page }) => {
  await openApp(page, { settings: { accounts: DISCONNECTED_ACCOUNTS } });
  await openNav(page, "Settings");
  await expect(page.locator("#settingsPage")).toContainText("not connected");
  await expectNoUiSentinels(page, page.locator("#settingsPage"));
});

test("connect accounts action delegates to the safe native tool", async ({ page }) => {
  await openApp(page);
  await openNav(page, "Settings");
  await page.getByRole("button", { name: "Connect accounts" }).click();
  expect(await page.evaluate(() => window.__mock.runTools)).toEqual(["connect"]);
  await expect(page.locator("#view-chat")).toBeVisible();
});

test("panic action delegates without performing a provider call", async ({ page }) => {
  await openApp(page);
  await openNav(page, "Settings");
  await page.getByRole("button", { name: "Enable panic" }).click();
  expect(await page.evaluate(() => window.__mock.runTools)).toEqual(["panic"]);
  expect(await page.evaluate(() => window.__mock.sendCount)).toBe(0);
});

test("model and mode selections persist through the preference bridge", async ({ page }) => {
  await openApp(page);
  await page.selectOption("#modelSel", "account:claude:opus");
  await page.selectOption("#modeSel", "approve-edits");
  const saved = await page.evaluate(() => window.__mock.savedPrefs);
  expect(saved).toContainEqual(["default_model", "account:claude:opus"]);
  expect(saved).toContainEqual(["default_mode", "approve-edits"]);
});

test("sparse settings data produces honest empty values, never broken sentinels", async ({ page }) => {
  await openApp(page, {
    settings: {
      prefs: { default_model: "", default_mode: "" },
      firewall: { profile: "", panic: false, spent_today: 0, cloud_gate: false },
      permissions: [], accounts: [], about: { version: null, release_stage: null },
    },
  });
  await openNav(page, "Settings");
  await expect(page.locator("#settingsPage")).toContainText("$0.00");
  await expectNoUiSentinels(page, page.locator("#settingsPage"));
});

test("settings connects a free provider without retaining the secret in the DOM", async ({ page }) => {
  await openApp(page, {
    settings: {
      prefs: { default_model: "auto", default_mode: "safe-auto" },
      firewall: {}, permissions: [], accounts: [], about: {},
      models: [{ id: "free:groq:openai/gpt-oss-120b", label: "Groq · GPT-OSS 120B", provider: "groq", kind: "free" }],
      credentials: [{ provider: "groq", configured: false, source: null, keychainAvailable: true }],
      usage: [],
    },
  });
  await openNav(page, "Settings");
  const input = page.getByLabel("Groq API key");
  await input.fill("temporary-super-secret");
  await page.getByRole("button", { name: "Connect Groq" }).click();
  expect(await page.evaluate(() => window.__mock.savedProviderKeys)).toEqual([["groq", "temporary-super-secret"]]);
  await expect(input).toHaveValue("");
  await expect(page.locator('[data-provider="groq"]')).toContainText("Connected securely");
  await expect(page.locator("#settingsPage")).not.toContainText("temporary-super-secret");
});

test("settings tests a free provider connection without sending a prompt", async ({ page }) => {
  await openApp(page, {
    settings: {
      prefs: {}, firewall: {}, permissions: [], accounts: [], about: {}, models: [], usage: [],
      credentials: [{ provider: "groq", configured: true, source: "keychain", keychainAvailable: true }],
    },
    providerTestResponses: { groq: { provider: "groq", connected: true, configured: true } },
  });
  await openNav(page, "Settings");
  await page.getByRole("button", { name: "Test Groq" }).click();
  expect(await page.evaluate(() => window.__mock.providerTests)).toEqual(["groq"]);
  await expect(page.locator('[data-provider="groq"]')).toContainText("Connection verified");
  expect(await page.evaluate(() => window.__mock.sendCount)).toBe(0);
});

test("settings shows an accessible usage bar and saves a soft limit", async ({ page }) => {
  const modelId = "account:claude:haiku";
  await openApp(page, {
    settings: {
      prefs: { default_model: "auto", default_mode: "safe-auto" },
      firewall: {}, permissions: [], accounts: [], about: {}, credentials: [],
      models: [{ id: modelId, label: "Claude · Haiku 4.5", provider: "claude", kind: "account" }],
      usage: [{ modelId, provider: "claude", source: "opai", metric: "tokens", used: 2500, limit: 5000, remaining: 2500, percent: 50, window: "month", confidence: "measured" }],
    },
  });
  await openNav(page, "Settings");
  const card = page.locator(`[data-model-id="${modelId}"]`);
  await expect(card).toContainText("2,500 / 5,000 tokens");
  await expect(card.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "50");
  await card.getByLabel("Soft token limit").fill("7500");
  await card.getByRole("button", { name: "Save limit" }).click();
  expect(await page.evaluate(() => window.__mock.savedUsageLimits)).toEqual([[modelId, "tokens", 7500, "month"]]);
});

test("known invalid Codex tier is repaired only after confirmation", async ({ page }) => {
  await openApp(page, {
    settings: {
      prefs: { default_model: "auto", default_mode: "safe-auto" },
      firewall: {}, permissions: [], accounts: [], about: {}, models: [], credentials: [], usage: [],
      codexConfig: { repairable: true, code: "CODEX_INVALID_SERVICE_TIER", message: "Codex service_tier 'default' is invalid." },
    },
  });
  await openNav(page, "Settings");
  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "Repair Codex config" }).click();
  expect(await page.evaluate(() => window.__mock.codexRepairs)).toBe(1);
  await expect(page.locator("#settingsPage")).toContainText("backup created");
});
