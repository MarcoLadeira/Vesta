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
