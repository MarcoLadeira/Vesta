import { test, expect } from "@playwright/test";

import { DISCONNECTED_ACCOUNTS } from "./helpers/fixtures.js";
import { finishRequest, openApp, sendPrompt } from "./helpers/app.js";


test("disconnected providers produce a clear OPai setup state", async ({ page }) => {
  await openApp(page, { boot: { accounts: DISCONNECTED_ACCOUNTS } });
  await expect(page.locator("#acct")).toContainText("No account connected");
  await expect(page.locator("#emptySub")).toContainText("Connect your Claude, Codex, or Copilot account");
});

test("missing account result is actionable and never completed", async ({ page }) => {
  await openApp(page);
  const id = await sendPrompt(page);
  await finishRequest(page, id, { status: "account_not_connected", answer: "Connect the selected account first." });
  await expect(page.locator(".error-card")).toContainText("No account connected");
  await expect(page.locator('.error-card [data-a="retry"]')).toBeVisible();
  await expect(page.locator('.error-card [data-a="switch"]')).toBeVisible();
  await expect(page.locator(".msg.bot")).not.toContainText("Completed");
});

test("codex invalid-config error offers a one-click repair, then retries", async ({ page }) => {
  await openApp(page);
  const id = await sendPrompt(page);
  await finishRequest(page, id, {
    status: "failed",
    error: {
      code: "CONFIG_INVALID",
      title: "OPai found a problem in this provider's config.",
      userMessage: "The provider CLI's config file has an invalid setting, so it won't start.",
      recoveryActions: ["repair_config", "open_settings", "show_details"],
      technicalMessage: "unknown variant `default`, expected `fast` or `flex`",
    },
  });
  const repair = page.locator('.error-card [data-a="repair"]');
  await expect(repair).toBeVisible();
  await expect(repair).toHaveText("Repair Codex config");
  const before = await page.evaluate(() => window.__mock.sendCount);
  await repair.click();
  // The repair goes through the bridge, then the message is retried.
  expect(await page.evaluate(() => window.__mock.codexRepairs)).toBe(1);
  expect(await page.evaluate(() => window.__mock.sendCount)).toBe(before + 1);
});

test("a live 401 after 'connected' offers a real Test connection check, not just Open Settings", async ({ page }) => {
  // The reported bug: OPai says connected, the real send still 401s. Open
  // Settings alone showed nothing new (it just repeats the same cached
  // "connected" row) — this proves the error card offers an actual live
  // re-check with the concrete next step (login hint), right where the
  // failure happened.
  await openApp(page, { providerTestResponses: { claude: { authStatus: "invalid", safeDiagnostic: "Session expired.", loginHint: "Run `claude` once and sign in to connect your account." } } });
  const id = await sendPrompt(page);
  await finishRequest(page, id, {
    status: "failed",
    error: {
      code: "AUTH_INVALID",
      title: "OPai could not authenticate this connection.",
      userMessage: "Reconnect the provider account or update its credentials in Settings.",
      recoveryActions: ["open_settings", "reconnect", "show_details"],
      technicalMessage: "Failed to authenticate. API Error: 401 Invalid authentication credentials",
      provider: "claude",
    },
  });
  const test = page.locator('.error-card [data-a="reconnect"]');
  await expect(test).toBeVisible();
  await expect(test).toHaveText("Test connection");
  await test.click();
  expect(await page.evaluate(() => window.__mock.providerTests)).toContain("claude");
});

test("a live 401 also offers Disconnect account, since Retry alone cannot fix a dead session", async ({ page }) => {
  await openApp(page);
  const id = await sendPrompt(page);
  await finishRequest(page, id, {
    status: "failed",
    error: {
      code: "AUTH_INVALID",
      title: "This account's sign-in was rejected by the provider.",
      userMessage: "OPai detected a signed-in session, but the request was refused (401).",
      recoveryActions: ["disconnect", "reconnect", "open_settings", "show_details"],
      provider: "claude",
    },
  });
  const disconnectBtn = page.locator('.error-card [data-a="disconnect"]');
  await expect(disconnectBtn).toBeVisible();
  await expect(disconnectBtn).toHaveText("Disconnect account");
  // No native confirm here — the error card commits directly, matching the
  // existing single-click convention for other consequential card actions
  // (Send to <provider>, Confirm cloud fallback).
  await disconnectBtn.click();
  expect(await page.evaluate(() => window.__mock.disconnects)).toContain("claude");
});

test("timeout is distinct, recoverable, and not successful", async ({ page }) => {
  await openApp(page);
  const id = await sendPrompt(page);
  await finishRequest(page, id, { status: "account_timeout", answer: "The provider did not respond in time." });
  await expect(page.locator(".error-card .ec-t")).toHaveText("Ran out of time");
  await expect(page.locator(".error-card")).toContainText("Retry");
  await expect(page.locator(".msg.bot")).not.toContainText("Completed");
});

test("raw 401 text is not duplicated in visible normal chat", async ({ page }) => {
  await openApp(page);
  const id = await sendPrompt(page);
  await finishRequest(page, id, {
    status: "account_error",
    answer: "401 Unauthorized",
    error: "401 Unauthorized",
  });
  const occurrences = ((await page.locator(".error-card").innerText()).match(/401 Unauthorized/g) || []).length;
  expect(occurrences).toBeLessThanOrEqual(1);
});

test("technical details stay collapsed by default", async ({ page }) => {
  await openApp(page);
  const id = await sendPrompt(page);
  await finishRequest(page, id, { status: "account_error", answer: "Sign-in failed.", error: "provider diagnostic" });
  await expect(page.locator(".ec-details")).not.toHaveAttribute("open", "");
  await expect(page.locator(".ec-details pre")).not.toBeVisible();
  await page.locator(".ec-details summary").click();
  await expect(page.locator(".ec-details pre")).toBeVisible();
});

test("defensive UI redacts secret-like technical details", async ({ page }) => {
  await openApp(page);
  const id = await sendPrompt(page);
  await finishRequest(page, id, { status: "account_error", answer: "Sign-in failed.", error: "token=super-secret-value" });
  await expect(page.locator(".error-card")).not.toContainText("super-secret-value");
});
