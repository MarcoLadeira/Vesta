import { test, expect } from "@playwright/test";

import { finishRequest, openApp, sendPrompt } from "./helpers/app.js";


test.beforeEach(async ({ page }) => openApp(page));

test("Escape stops an active request and records one cancellation", async ({ page }) => {
  await sendPrompt(page);
  await page.keyboard.press("Escape");
  await expect(page.locator(".stopped-card")).toBeVisible();
  expect(await page.evaluate(() => window.__mock.cancelCount)).toBe(1);
});

test("composer send control becomes Stop and returns to Send", async ({ page }) => {
  const id = await sendPrompt(page);
  await expect(page.locator("#send")).toHaveText("Stop");
  await expect(page.locator("#send")).toHaveAttribute("aria-label", "Stop generation");
  await finishRequest(page, id);
  await expect(page.locator("#send")).toHaveText("Send");
  await expect(page.locator("#send")).toHaveAttribute("aria-label", "Send prompt");
});

test("new chat during generation cancels before clearing", async ({ page }) => {
  await sendPrompt(page);
  await page.getByRole("button", { name: "New chat" }).click();
  expect(await page.evaluate(() => window.__mock.cancelCount)).toBe(1);
  await expect(page.locator(".msg")).toHaveCount(0);
  await expect(page.locator("#empty")).toBeVisible();
});

test("edit after stop restores the original prompt without resending", async ({ page }) => {
  await sendPrompt(page, "keep my original prompt");
  await page.locator(".gen-stop").click();
  await page.locator('.stopped-card [data-a="edit"]').click();
  await expect(page.locator("#input")).toHaveValue("keep my original prompt");
  expect(await page.evaluate(() => window.__mock.sendCount)).toBe(1);
});
