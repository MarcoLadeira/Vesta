import { test, expect } from "@playwright/test";

const MOCK = "opai/assets/web/__tests__/e2e/mock-bridge.js";

test.beforeEach(async ({ page }) => {
  await page.addInitScript({ path: MOCK });
  await page.goto("/opai/assets/web/index.html");
  await page.waitForSelector("#input");
});

test("Mission Control makes capture gaps visible without leaking prompts", async ({ page }) => {
  await page.getByRole("button", { name: "Home" }).click();

  await expect(page.getByText("Capture health")).toBeVisible();
  await expect(page.getByText("67%")).toBeVisible();
  await expect(page.getByText("Observed sessions")).toBeVisible();
  await expect(page.getByText("1 fail-open session was not accounted.")).toBeVisible();
  await expect(page.getByText(/Direct unwrapped launches are not measurable yet/)).toBeVisible();
  await expect(page.locator("body")).not.toContainText("sk-supersecret");
});
