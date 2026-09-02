import { test, expect } from "@playwright/test";

// Connection status is rendered through one label map (settings.js,
// AUTH_STATUS_LABEL) rather than by printing whichever raw value reached the
// element. These assertions therefore read "Connected" / "Not connected" /
// "Sign-in rejected" rather than the enum.
//
// That map exists because a real user report showed a card saying "Sign-in
// verified locally; provider acceptance is confirmed" directly above
// "Status: not connected": the sentence came from the payload while the status
// line printed authStatus verbatim, and two other code paths wrote that same
// element using words the backend never emits.

import { DISCONNECTED_ACCOUNTS } from "./helpers/fixtures.js";
import { expectNoUiSentinels, openApp, openSettings } from "./helpers/app.js";


test("settings renders defaults, firewall, accounts, privacy, and version", async ({ page }) => {
  await openApp(page);
  await openSettings(page, "providers");
  const settings = page.locator("#settingsPage");
  await expect(settings).toContainText("Default model");
  await expect(settings).toContainText("Auto");
  await expect(settings).toContainText("solo-balanced");
  await expect(settings).toContainText("Claude");
  await expect(settings).toContainText("Codex");
  await expect(settings).toContainText("Copilot");
  await expect(settings).toContainText("No telemetry");
  await expect(settings).toContainText("0.2.0a1");
});

test("the Providers page opens with an honest health summary that tracks live checks", async ({ page }) => {
  // Default fixtures: three connected accounts -> all good.
  await openApp(page, {
    providerTestResponses: { claude: { provider: "claude", authStatus: "expired", safeDiagnostic: "Sign-in expired." } },
  });
  await openSettings(page, "providers");
  const summary = page.locator("[data-doctor-summary]");
  await expect(summary).toContainText("All 3 connections look good");
  await expect(summary).toHaveClass(/ok/);
  // A live check that fails flips the summary without a re-render.
  await page.locator('[data-test-account="claude"]').click();
  await expect(summary).toContainText("1 of 3 connections need attention");
  await expect(summary).toHaveClass(/warn/);
});

test("background Codex discovery clears a stale degraded connection card", async ({ page }) => {
  await openApp(page, {
    deferDiscovery: true,
    settings: {
      connectionDoctor: [
        {
          providerId: "codex",
          displayName: "Codex",
          health: "degraded",
          authStatus: "misconfigured",
          cliInstalled: true,
          safeDiagnostic: "The installed CLI is too old.",
        },
      ],
    },
    discoveredModels: [
      { id: "auto", label: "OPai · Auto mode", kind: "auto", group: "routing" },
    ],
    discoveredConnections: [
      { providerId: "codex", authStatus: "connected", safeDiagnostic: "Connected." },
    ],
  });
  await openSettings(page, "providers");
  await expect(page.locator('[data-doctor-provider="codex"] [data-doctor-health]')).toHaveText("Degraded");
  await page.evaluate(() => window.__mock.emitDiscoveredModels());
  await expect(page.locator('[data-doctor-provider="codex"] [data-doctor-health]')).toHaveText("Verified");
});

test("test connection on a connected account reports the live truth, not the cached label", async ({ page }) => {
  // Reproduces the reported bug: OPai's on-disk "connected" state can be stale
  // (an OAuth session that died since detection). Clicking Test connection
  // must run a live check and update the row, not just repeat "connected".
  await openApp(page, {
    providerTestResponses: { claude: { authStatus: "invalid", safeDiagnostic: "Session expired.", loginHint: "Run `claude` once and sign in to connect your account." } },
  });
  await openSettings(page, "providers");
  const row = page.locator('[data-account-row="claude"]');
  await expect(row).toContainText("Connected");
  await page.locator('[data-test-account="claude"]').click();
  expect(await page.evaluate(() => window.__mock.providerTests)).toContain("claude");
  await expect(page.locator('[data-account-status="claude"]')).toHaveText(
    "Sign-in rejected",
  );
});

test("test connection on a genuinely healthy account confirms connected", async ({ page }) => {
  await openApp(page, { providerTestResponses: { claude: { authStatus: "connected" } } });
  await openSettings(page, "providers");
  const discoveries = await page.evaluate(() => window.__mock.modelDiscoveries);
  await page.locator('[data-test-account="claude"]').click();
  await expect(page.locator('[data-account-status="claude"]')).toHaveText("Connected");
  await expect.poll(() => page.evaluate(() => window.__mock.modelDiscoveries)).toBeGreaterThan(discoveries);
});

test("disconnect asks with a styled inline confirm, then signs out and updates the row", async ({ page }) => {
  let dialogs = 0;
  page.on("dialog", async (dialog) => { dialogs += 1; await dialog.dismiss(); });
  await openApp(page);
  await openSettings(page, "providers");
  await page.locator('[data-disconnect-account="claude"]').click();
  // Confirmation is an in-place card, never a native dialog (#151).
  await expect(page.locator(".inline-confirm").first()).toBeVisible();
  expect(dialogs).toBe(0);
  await page.locator('.inline-confirm [data-ic="ok"]').first().click();
  expect(await page.evaluate(() => window.__mock.disconnects)).toContain("claude");
  await expect(page.locator('[data-account-status="claude"]')).toHaveText(
    "Not connected",
  );
  // Nothing left to disconnect or test once signed out.
  await expect(page.locator('[data-disconnect-account="claude"]')).toBeDisabled();
  await expect.poll(() => page.evaluate(() => window.__mock.modelDiscoveries)).toBeGreaterThan(1);
});

test("successful Codex sign-in refreshes the model catalog", async ({ page }) => {
  await openApp(page, {
    settings: { accounts: DISCONNECTED_ACCOUNTS },
    discoveredAccounts: DISCONNECTED_ACCOUNTS.map((account) =>
      account.id === "codex"
        ? { ...account, connected: true, authenticated: true }
        : account
    ),
    discoveredModels: [
      { id: "auto", label: "OPai · Auto mode", kind: "auto", group: "routing" },
      {
        id: "account:codex:gpt-5.6-sol",
        label: "Codex · GPT-5.6 Sol",
        provider: "codex",
        kind: "account",
        group: "codex",
      },
    ],
    loginResponses: {
      codex: { provider: "codex", signedIn: true, authStatus: "connected" },
    },
  });
  await openSettings(page, "providers");
  const discoveries = await page.evaluate(() => window.__mock.modelDiscoveries);
  await page.locator('[data-login-account="codex"]').click();
  await expect.poll(() => page.evaluate(() => window.__mock.modelDiscoveries)).toBeGreaterThan(discoveries);
  await expect(page.locator('#modelSel option[value="account:codex:gpt-5.6-sol"]')).toHaveText("Codex · GPT-5.6 Sol");
  await expect(page.locator("#acct")).toContainText("Codex");
});

test("cancelling the disconnect confirmation leaves the account untouched", async ({ page }) => {
  await openApp(page);
  await openSettings(page, "providers");
  await page.locator('[data-disconnect-account="claude"]').click();
  await page.locator('.inline-confirm [data-ic="cancel"]').first().click();
  expect(await page.evaluate(() => window.__mock.disconnects)).toEqual([]);
  await expect(page.locator('[data-account-status="claude"]')).toHaveText("Connected");
  // The button is usable again after cancelling.
  await expect(page.locator('[data-disconnect-account="claude"]')).toBeEnabled();
});

test("settings renders disconnected accounts without crashing", async ({ page }) => {
  await openApp(page, { settings: { accounts: DISCONNECTED_ACCOUNTS } });
  await openSettings(page, "providers");
  await expect(page.locator("#settingsPage")).toContainText("Not connected");
  await expectNoUiSentinels(page, page.locator("#settingsPage"));
});

test("connect accounts action delegates to the safe native tool", async ({ page }) => {
  await openApp(page);
  await openSettings(page, "providers");
  // Bug 7: the button is "Connect CLI accounts…" now — it opens a guided
  // sign-in for the CLI accounts only, which the API-key section above does not.
  await page.locator("#setConnect").click();
  expect(await page.evaluate(() => window.__mock.runTools)).toEqual(["connect"]);
  await expect(page.locator("#view-chat")).toBeVisible();
});

test("panic action delegates without performing a provider call", async ({ page }) => {
  await openApp(page);
  await openSettings(page, "providers");
  await page.locator('.settings-rail-item[data-rail-target="firewall"]').click();
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
  await openSettings(page, "providers");
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
  await openSettings(page, "providers");
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
  await openSettings(page, "providers");
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
  await openSettings(page, "providers");
  // Usage limits live on the Cost Firewall page (#238).
  await page.locator('.settings-rail-item[data-rail-target="firewall"]').click();
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
  await openSettings(page, "providers");
  let dialogs = 0;
  page.on("dialog", async (dialog) => { dialogs += 1; await dialog.dismiss(); });
  await page.getByRole("button", { name: "Repair Codex config" }).click();
  // Confirmation is an in-place card, never a native dialog (#151).
  await expect(page.locator(".inline-confirm .ic-title")).toContainText("Repair Codex config");
  expect(dialogs).toBe(0);
  await page.locator('.inline-confirm [data-ic="ok"]').click();
  expect(await page.evaluate(() => window.__mock.codexRepairs)).toBe(1);
  await expect(page.locator("#settingsPage")).toContainText("backup created");
});
