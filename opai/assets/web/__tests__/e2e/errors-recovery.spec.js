import { test, expect } from "@playwright/test";

import { finishRequest, openApp, sendPrompt } from "./helpers/app.js";


const CASES = [
  ["network error", "account_error", "Network unavailable. Check your connection."],
  ["provider rate limit", "account_error", "Rate limit reached. Try again later."],
  ["provider timeout", "account_timeout", "The request timed out."],
  ["server failure", "error", "The provider returned a server error."],
  ["missing credentials", "account_not_connected", "Connect the selected account first."],
  ["empty response", "empty", "The provider returned no usable response."],
];

for (const [name, status, answer] of CASES) {
  test(`${name} renders one recoverable error and never success`, async ({ page }) => {
    await openApp(page);
    const id = await sendPrompt(page);
    await finishRequest(page, id, { status, answer });
    await expect(page.locator(".error-card")).toHaveCount(1);
    await expect(page.locator(".error-card")).toContainText(answer);
    await expect(page.locator('.error-card [data-a="retry"]')).toBeVisible();
    await expect(page.locator(".msg.bot")).not.toContainText("Completed");
  });
}

test("retry after failure starts one clean replacement request", async ({ page }) => {
  await openApp(page);
  const id = await sendPrompt(page, "retry this safely");
  await finishRequest(page, id, { status: "account_error", answer: "Temporary provider error." });
  await page.locator('.error-card [data-a="retry"]').click();
  expect(await page.evaluate(() => window.__mock.sendCount)).toBe(2);
  await expect(page.locator(".gen-stop")).toBeVisible();
  await expect(page.locator(".error-card")).toHaveCount(1);
  await expect(page.locator(".msg.bot")).toHaveCount(2);
});

test("Switch model opens the visible picker and Retry uses the new selection", async ({ page }) => {
  await openApp(page, {
    boot: {
      models: [
        { id: "account:claude:sonnet", label: "Claude · Sonnet 4.6", kind: "account", group: "claude", provider: "claude", available: true, healthy: true },
        { id: "account:claude:opus", label: "Claude · Opus 4.8", kind: "account", group: "claude", provider: "claude", available: true, healthy: true },
        { id: "auto", label: "OPai · Auto mode", kind: "auto", group: "routing", available: true, healthy: true },
      ],
      selectedModel: "account:claude:sonnet",
      prefs: { model: "account:claude:sonnet" },
    },
  });
  const id = await sendPrompt(page, "retry with another provider");
  await finishRequest(page, id, {
    status: "account_error",
    answer: "The selected provider is unavailable.",
  });

  await page.locator('.error-card [data-a="switch"]').click();
  await expect(page.locator("#modelPop")).toBeVisible();
  await page.getByRole("menuitemradio", { name: /Opus/ }).click();
  await page.locator('.error-card [data-a="retry"]').click();

  expect(await page.evaluate(() => window.__mock.lastRequest.model)).toBe("account:claude:opus");
});

test("raw stack details are hidden until the user expands them", async ({ page }) => {
  await openApp(page);
  const id = await sendPrompt(page);
  await finishRequest(page, id, { status: "error", answer: "Request failed cleanly.", error: "Traceback: internal stack" });
  await expect(page.locator(".ec-details pre")).toBeHidden();
});

test("Claude spend-limit guidance is visible while technical details stay collapsed", async ({ page }) => {
  await openApp(page);
  const id = await sendPrompt(page);
  await finishRequest(page, id, {
    status: "failed",
    answer: "Claude says you've hit your monthly spend limit.",
    error: {
      code: "PROVIDER_QUOTA_EXHAUSTED",
      title: "This provider's quota has been exhausted.",
      userMessage: "Claude says you've hit your monthly spend limit. Wait for it to reset, raise it at https://claude.ai/settings/usage, or switch model.",
      recoveryActions: ["change_mode", "open_settings", "show_details"],
      technicalMessage: "You've hit your monthly spend limit · raise it at claude.ai/settings/usage?from=cc_cli_limit_message",
      provider: "claude",
      retryable: false,
    },
  });

  await expect(page.locator(".error-card")).toContainText("monthly spend limit");
  await expect(page.locator(".error-card")).toContainText("claude.ai/settings/usage");
  await expect(page.locator(".ec-details pre")).toBeHidden();
});

test("malformed answer object never renders object coercion", async ({ page }) => {
  await openApp(page);
  const id = await sendPrompt(page);
  await finishRequest(page, id, { status: "answered", answer: { unexpected: "shape" } });
  await expect(page.locator(".msg.bot")).not.toContainText("[object Object]");
});
