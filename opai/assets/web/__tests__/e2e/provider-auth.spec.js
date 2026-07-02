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
