import { test, expect } from "@playwright/test";

const MOCK = "opai/assets/web/__tests__/e2e/mock-bridge.js";

test.beforeEach(async ({ page }) => {
  await page.addInitScript({ path: MOCK });
  await page.goto("/opai/assets/web/index.html");
  await page.waitForSelector("#input");
});

test("Agent Readiness shows wrapper capture capability", async ({ page }) => {
  await page.getByRole("button", { name: "Agents" }).click();

  await expect(page.getByText("Agent Readiness")).toBeVisible();
  await expect(page.getByText("Wrapper")).toBeVisible();
  await expect(page.getByText("installed")).toBeVisible();
  await expect(page.getByText("Capture", { exact: true })).toBeVisible();
  await expect(page.getByText("selective proxy")).toBeVisible();
});
