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

test("raw stack details are hidden until the user expands them", async ({ page }) => {
  await openApp(page);
  const id = await sendPrompt(page);
  await finishRequest(page, id, { status: "error", answer: "Request failed cleanly.", error: "Traceback: internal stack" });
  await expect(page.locator(".ec-details pre")).toBeHidden();
});

test("known bug: malformed answer object never renders object coercion", async ({ page }) => {
  test.fail(true, "BUG-QA-003: malformed answered payload renders [object Object] in chat");
  await openApp(page);
  const id = await sendPrompt(page);
  await finishRequest(page, id, { status: "answered", answer: { unexpected: "shape" } });
  await expect(page.locator(".msg.bot")).not.toContainText("[object Object]");
});
