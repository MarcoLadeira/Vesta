import { test, expect } from "@playwright/test";

const MOCK = "vesta/assets/web/__tests__/e2e/mock-bridge.js";

test.beforeEach(async ({ page }) => {
  await page.addInitScript({ path: MOCK });
  await page.goto("/vesta/assets/web/index.html");
  await page.waitForSelector("#input");
});

// The Agents page became the multi-agent workspace (ee73c88), which no longer
// renders the Agent Readiness cards -- wrapper and capture status have no home
// in the UI now, though the view model still sends them. Kept as fixme until
// that status is given a place again, rather than deleted and forgotten.
test.fixme("Agent Readiness shows wrapper capture capability", async ({ page }) => {
  // Exact: the header also has an "Agents workspace" button.
  await page.getByRole("button", { name: "Agents", exact: true }).click();

  await expect(page.getByText("Agent Readiness")).toBeVisible();
  await expect(page.getByText("Wrapper")).toBeVisible();
  await expect(page.getByText("installed")).toBeVisible();
  await expect(page.getByText("Capture", { exact: true })).toBeVisible();
  await expect(page.getByText("selective proxy")).toBeVisible();
});
