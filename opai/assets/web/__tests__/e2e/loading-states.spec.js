import { test, expect } from "@playwright/test";

import { openApp, openNav } from "./helpers/app.js";


test("dashboard shows loading until bridge data arrives", async ({ page }) => {
  await openApp(page, { dashboardDelayMs: 250 });
  await openNav(page, "Money Saved");
  await expect(page.locator("#dashPage .state-card.loading")).toHaveAttribute("role", "status");
  await expect(page.locator("#dashPage")).toContainText("Waiting for locally prepared dashboard data.");
  await expect(page.locator("#dashPage")).toContainText("Money Saved");
});

test("settings shows loading until connection data arrives", async ({ page }) => {
  await openApp(page, { settingsDelayMs: 250 });
  await openNav(page, "Settings");
  await expect(page.locator("#settingsPage .state-card.loading")).toHaveAttribute("role", "status");
  await expect(page.locator("#settingsPage")).toContainText("Checking local preferences and connections.");
  // Paned settings (#217): the default page is Providers & Connections.
  await expect(page.locator("#settingsPage")).toContainText("Connection Doctor");
});

test("Prompt Library transitions from loading boundary to templates", async ({ page }) => {
  await openApp(page, { promptDelayMs: 250 });
  await openNav(page, "Prompt Library");
  await expect(page.locator(".prompt-card")).toHaveCount(3);
});

test("shell renders before delayed local discovery and updates the picker asynchronously", async ({ page }) => {
  const discovered = [
    { id: "auto", label: "Vesta · Auto mode", kind: "auto", group: "routing", provider: "auto", available: true },
    { id: "ollama:qwen", label: "Qwen · local", kind: "local", group: "local", provider: "ollama", available: true },
  ];
  await openApp(page, { discoveredModels: discovered, deferDiscovery: true });
  await expect(page.getByRole("button", { name: "Send" })).toBeVisible();
  await expect(page.locator('#modelSel option[value="ollama:qwen"]')).toHaveCount(0);
  await page.evaluate(() => window.__mock.emitDiscoveredModels());
  await expect(page.locator('#modelSel option[value="ollama:qwen"]')).toHaveCount(1, { timeout: 2000 });
});
