import { test, expect } from "@playwright/test";

import { openApp, openNav } from "./helpers/app.js";


test.beforeEach(async ({ page }) => {
  await openApp(page);
  await openNav(page, "Prompt Library");
});

test("prompt library renders useful templates and categories", async ({ page }) => {
  await expect(page.locator(".prompt-card")).toHaveCount(3);
  await expect(page.locator("#promptCat")).toContainText("Coding");
  await expect(page.locator("#promptCat")).toContainText("Testing");
  await expect(page.locator("#promptCat")).toContainText("Security");
});

test("prompt search filters to matching user value", async ({ page }) => {
  await page.fill("#promptSearch", "security audit");
  await expect(page.locator(".prompt-card")).toHaveCount(1);
  await expect(page.locator(".prompt-card")).toContainText("Security review");
});

test("category filter narrows the library", async ({ page }) => {
  await page.selectOption("#promptCat", "Testing");
  await expect(page.locator(".prompt-card")).toHaveCount(1);
  await expect(page.locator(".prompt-card")).toContainText("Write tests for a file");
});

test("using a prompt fills the composer and updates task focus", async ({ page }) => {
  const card = page.locator(".prompt-card").filter({ hasText: "Security review" });
  await card.getByRole("button", { name: "Use prompt" }).click();
  await expect(page.locator("#view-chat")).toBeVisible();
  await expect(page.locator("#input")).toHaveValue("Audit <area> for security risks. Do not modify files.");
  expect(await page.evaluate(() => window.__mock.savedPrefs)).toContainEqual(["default_task_mode", "review"]);
});

test("no-match search renders a deliberate empty state", async ({ page }) => {
  await page.fill("#promptSearch", "zzzz-no-template");
  await expect(page.locator("#promptList")).toContainText("No prompts match.");
  await expect(page.locator(".prompt-card")).toHaveCount(0);
});
